import os, time, hmac, hashlib, threading
import requests
from flask import Flask, jsonify

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

def sign_params(params, ts, recv):
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    to_sign = ts + API_KEY + recv + query
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json"
    }
    return headers

def bybit_get(path, params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    headers = sign_params(params, ts, recv)
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=15)
    print(f"【entity-BYBIT¦canonical_name=BYBIT】 GET {path} -> {r.status_code} {r.text[:300]}", flush=True)
    return r.json()

def bybit_post(path, params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    to_sign = ts + API_KEY + recv + query
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json"
    }
    r = requests.post(BASE_URL + path, json=params, headers=headers, timeout=15)
    print(f"BYBIT POST {path} -> {r.status_code} {r.text[:500]}", flush=True)
    return r.json()

def get_balances():
    out = {}
    for acct in ["UNIFIED", "SPOT"]:
        try:
            d = bybit_get("/v5/account/wallet-balance", {"accountType": acct})
            if d.get("retCode") != 0:
                print(f"Saldo {acct} FAIL: {d.get('retCode')} {d.get('retMsg')}", flush=True)
                continue
            lst = d.get("result", {}).get("list", [])
            if not lst:
                continue
            for coin in lst[0].get("coin", []):
                val = float(coin.get("walletBalance", 0))
                if coin["coin"] not in out or val > 0:
                    out[coin["coin"]] = val
        except Exception as e:
            print(f"Saldo FAIL {acct}: {e}", flush=True)
    return out

def get_price():
    r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    lst = r.get("result", {}).get("list", [])
    if not lst:
        raise Exception(f"geen price, Bybit zei: {r}")
    return float(lst[0]["lastPrice"])

def get_qty_step():
    r = requests.get(BASE_URL + "/v5/market/instruments-info", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    lst = r.get("result", {}).get("list", [])
    if not lst:
        raise Exception(f"geen instruments, Bybit zei: {r}")
    info = lst[0]
    return float(info["lotSizeFilter"]["qtyStep"]), float(info["lotSizeFilter"]["minOrderQty"])

def get_klines():
    r = requests.get(BASE_URL + "/v5/market/kline", params={"category": CATEGORY, "symbol": SYMBOL, "interval": TIMEFRAME, "limit": 200}, timeout=10).json()
    lst = r.get("result", {}).get("list", [])
    if not lst:
        raise Exception(f"geen klines, Bybit zei: {r}")
    klines = sorted(lst, key=lambda x: int(x[0]))
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
        return "wait", closes[-1], "te weinig candles"
    atr10 = calc_atr(highs, lows, closes, 10)
    price = closes[-1]
    upper = price - (3 * atr10)
    super_green = price > closes[-5] and price > upper
    atr14 = calc_atr(highs, lows, closes, 14)
    n_loss = 4 * atr14
    trailing = closes[-2] - n_loss
    ut_buy = price > trailing and price > closes[-2]
    ut_sell = price < trailing
    print(f"SIGNAL: price={price} super_green={super_green} ut_buy={ut_buy} ut_sell={ut_sell}", flush=True)
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
    signal, price, reason = get_signals()
    if signal == "sell" and (in_position or pepe > 0):
        qty_step, min_qty = get_qty_step()
        qty = int(pepe / qty_step) * qty_step
        if qty < min_qty:
            return {"status": "sell skip", "pepe": pepe}
        res = bybit_post("/v5/order/create", {"category": CATEGORY, "symbol": SYMBOL, "side": "Sell", "orderType": "Market", "qty": str(qty)})
        if res.get("retCode") == 0:
            in_position = False
            send_telegram(f"VERKOCHT PEPE @ {price} - {reason}")
        return {"status": "sell", "price": price, "qty": qty, "bybit": res}
    if signal == "buy" and not in_position:
        if usdt < 10:
            return {"status": "te weinig USDT", "usdt": usdt}
        qty_step, min_qty = get_qty_step()
        use = min(usdt, USE_USDT)
        qty = int((use / price) / qty_step) * qty_step
        if qty < min_qty:
            return {"status": "qty te klein", "qty": qty}
        res = bybit_post("/v5/order/create", {"category": CATEGORY, "symbol": SYMBOL, "side": "Buy", "orderType": "Market", "qty": str(qty)})
        if res.get("retCode") == 0:
            in_position = True
            send_telegram(f"GEKOCHT PEPE @ {price} voor {use} USDT - {reason}")
        return {"status": "buy", "price": price, "qty": qty, "bybit": res}
    return {"status": "hold", "signal": signal, "price": price, "usdt": usdt, "pepe": pepe, "reason": reason}

def bot_loop():
    time.sleep(10)
    while True:
        try:
            print("AUTO CHECK gestart", flush=True)
            res = do_check()
            print(f"AUTO CHECK klaar: {res}", flush=True)
        except Exception as e:
            print(f"AUTO CHECK FAIL: {e}", flush=True)
        time.sleep(300)

threading.Thread(target=bot_loop, daemon=True).start()

@app.route("/")
def home():
    return "Pepebot-3 live"

@app.route("/check")
def check():
    return jsonify(do_check())

@app.route("/test-key")
def test_key():
    data = bybit_get("/v5/account/wallet-balance", {"accountType": "SPOT"})
    return jsonify({"bybit_raw": data, "secret_len": len(API_SECRET)})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
