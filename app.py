import os, time, hmac, hashlib, threading, json, math
import requests
from flask import Flask, jsonify, request
from urllib.parse import urlencode

app = Flask(__name__)

BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
TIMEFRAME = "15"

API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

USE_USDT = 95.0
in_position = False

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print(f"TELEGRAM FAIL: {e}", flush=True)

# --- 【entity-BYBIT¦canonical_name=BYBIT】 ---

def bybit_get(path, params):
    ts = str(int(time.time() * 1000))
    recv = "20000"
    query = urlencode(sorted(params.items()))
    to_sign = ts + API_KEY + recv + query
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN-TYPE": "2",
        "Content-Type": "application/json"
    }
    try:
        r = requests.get(f"{BASE_URL}{path}?{query}", headers=headers, timeout=15)
        print(f"【entity-BYBIT¦canonical_name=BYBIT】 GET {path} -> {r.status_code} {r.text[:300]}", flush=True)
        if r.status_code != 200:
            return {"retCode": r.status_code, "retMsg": r.text[:200], "result": {"list": []}}
        return r.json()
    except Exception as e:
        print(f"BYBIT GET FAIL: {e}", flush=True)
        return {"retCode": -1, "result": {"list": []}}

def bybit_post(path, params):
    ts = str(int(time.time() * 1000))
    recv = "20000"
    body_str = json.dumps(params, separators=(',', ':'))
    to_sign = ts + API_KEY + recv + body_str
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN-TYPE": "2",
        "Content-Type": "application/json"
    }
    try:
        r = requests.post(BASE_URL + path, data=body_str, headers=headers, timeout=15)
        print(f"BYBIT POST {path} -> {r.status_code} {r.text[:500]}", flush=True)
        if r.status_code != 200:
            return {"retCode": r.status_code, "retMsg": r.text[:200]}
        return r.json()
    except Exception as e:
        print(f"BYBIT POST FAIL: {e}", flush=True)
        return {"retCode": -1, "retMsg": str(e)}

def get_balances():
    out = {}
    try:
        d = bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        if d.get("retCode") != 0:
            print(f"BALANCE FAIL: {d}", flush=True)
            return out
        lst = d.get("result", {}).get("list", [])
        if not lst:
            return out
        for coin in lst[0].get("coin", []):
            out[coin["coin"]] = float(coin.get("walletBalance", 0))
    except Exception as e:
        print(f"Saldo FAIL: {e}", flush=True)
    return out

def get_price():
    r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    return float(r["result"]["list"][0]["lastPrice"])

def get_qty_step():
    r = requests.get(BASE_URL + "/v5/market/instruments-info", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    info = r["result"]["list"][0]
    return float(info["lotSizeFilter"]["qtyStep"]), float(info["lotSizeFilter"]["minOrderQty"])

def get_klines():
    r = requests.get(BASE_URL + "/v5/market/kline", params={"category": CATEGORY, "symbol": SYMBOL, "interval": TIMEFRAME, "limit": 200}, timeout=10).json()
    klines = sorted(r["result"]["list"], key=lambda x: int(x[0]))
    closes = [float(x[4]) for x in klines]
    highs = [float(x[2]) for x in klines]
    lows = [float(x[3]) for x in klines]
    return closes, highs, lows

def calc_atr(highs, lows, closes, period):
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0
    return sum(trs[-period:]) / period

def get_signals():
    closes, highs, lows = get_klines()
    if len(closes) < 30:
        return "wait", closes[-1] if closes else 0, "te weinig candles"

    atr10 = calc_atr(highs, lows, closes, 10)
    price = closes[-1]
    upper = price - (3 * atr10)
    super_green = price > closes[-5] and price > upper

    atr14 = calc_atr(highs, lows, closes, 14)
    n_loss = 4 * atr14
    trailing = closes[-2] - n_loss
    ut_buy = price > trailing and price > closes[-2]
    ut_sell = price < trailing

    print(f"SIGNAL: price={price} super_green={super_green} ut_buy={ut_buy} ut_sell={ut_sell} atr10={atr10} atr14={atr14}", flush=True)

    if super_green and ut_buy:
        return "buy", price, "Supertrend groen + UT Buy"
    if not super_green or ut_sell:
        return "sell", price, "Supertrend rood / UT Sell"
    return "hold", price, "geen dubbel signaal"

def do_check():
    global in_position
    bal = get_balances()
    usdt = bal.get("USDT", 0)
    pepe = bal.get("PEPE", 0)
    print(f"CHECK: USDT={usdt} PEPE={pepe} in_position={in_position}", flush=True)

    try:
        signal, price, reason = get_signals()
    except Exception as e:
        print(f"SIGNAL FAIL: {e}", flush=True)
        return {"status": "signal fail", "error": str(e)}

    # SELL
    if signal == "sell" and (in_position or pepe > 0):
        qty_step, min_qty = get_qty_step()
        qty = math.floor(pepe / qty_step) * qty_step
        if qty < min_qty:
            print("SELL skip: te weinig PEPE", flush=True)
            return {"status": "sell skip", "pepe": pepe}
        res = bybit_post("/v5/order/create", {"category": CATEGORY, "symbol": SYMBOL, "side": "Sell", "orderType": "Market", "qty": str(qty)})
        if res.get("retCode") == 0:
            in_position = False
            send_telegram(f"VERKOCHT PEPE @ {price} - {reason}")
        return {"status": "sell", "price": price, "qty": qty, "res": res}

    # BUY
    if signal == "buy" and not in_position:
        if usdt < 10:
            print("BUY skip: te weinig USDT", flush=True)
            return {"status": "buy skip", "usdt": usdt}
        qty_step, min_qty = get_qty_step()
        amount = min(usdt, USE_USDT)
        qty = math.floor((amount / price) / qty_step) * qty_step
        if qty < min_qty:
            print("BUY skip: qty te klein", flush=True)
            return {"status": "buy skip qty", "qty": qty}
        res = bybit_post("/v5/order/create", {"category": CATEGORY, "symbol": SYMBOL, "side": "Buy", "orderType": "Market", "qty": str(qty)})
        if res.get("retCode") == 0:
            in_position = True
            send_telegram(f"GEKOCHT PEPE @ {price} - {reason} - qty {qty}")
        return {"status": "buy", "price": price, "qty": qty, "res": res}

    return {"status": "hold", "signal": signal, "price": price, "usdt": usdt, "pepe": pepe, "reason": reason}

def auto_loop():
    while True:
        try:
            do_check()
        except Exception as e:
            print(f"AUTO CHECK FAIL: {e}", flush=True)
        time.sleep(60)

# --- ROUTES ---

@app.route("/", methods=["GET"])
def home():
    return "Bot draait", 200

@app.route("/test-key", methods=["GET"])
def test_key():
    bal = get_balances()
    return jsonify({"balances": bal, "api_key_set": bool(API_KEY)}), 200

@app.route("/check", methods=["GET"])
def check():
    return jsonify(do_check()), 200

@app.route("/telegram", methods=["POST"])
def telegram():
    data = request.get_json(force=True, silent=True) or {}
    text = str(data.get("message", {}).get("text", "")).lower()
    if "check" in text:
        res = do_check()
        send_telegram(f"Check: {res}")
    elif "saldo" in text or "balance" in text:
        send_telegram(f"Saldo: {get_balances()}")
    return jsonify({"ok": True}), 200

@app.route("/webhook", methods=["POST"])
def webhook():
    return jsonify(do_check()), 200

if __name__ == "__main__":
    threading.Thread(target=auto_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
