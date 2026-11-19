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

# Hoeveel USDT per trade, hou 5 over voor fees
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
    print(f"BYBIT GET {path} -> {r.status_code} {r.text[:300]}", flush=True)
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
    for acct in ["SPOT", "UNIFIED"]:
        try:
            d = bybit_get("/v5/account/wallet-balance", {"accountType": acct})
            if d.get("retCode") != 0:
                continue
            lst = d.get("result", {}).get("list", [])
            if not lst:
                continue
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
    # Bybit geeft [start, open, high, low, close, volume, turnover]
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
    # Simpele ATR = gemiddelde van laatste 'period' TR's
    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0
    return sum(trs[-period:]) / period

def get_signals():
    closes, highs, lows = get_klines()
    if len(closes) < 30:
        return "wait", closes[-1], "te weinig candles"

    # --- Supertrend 10, 3 ---
    atr10 = calc_atr(highs, lows, closes, 10)
    price = closes[-1]
    prev_price = closes[-2]
    # Supertrend: groen als prijs boven (prijs - 3*ATR) blijft en stijgt
    # Simpele, stabiele versie: trend op basis van prijs vs midden + ATR
    upper = price - (3 * atr10)
    super_green = price > closes[-5] and price > upper

    # --- UT Bot 4, 14 ---
    atr14 = calc_atr(highs, lows, closes, 14)
    n_loss = 4 * atr14
    # UT Buy als prijs boven trailing stop van vorige candles uitkomt
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

    signal, price, reason = get_signals()

    # SELL
    if signal == "sell" and (in_position or pepe > 0):
        qty_step, min_qty = get_qty_step()
        qty = int(pepe / qty_step) * qty_step
        if qty < min_qty:
            print("SELL skip: te weinig PEPE", flush=True)
            return {"status": "sell skip", "pepe": pepe}
        res = bybit_post("/v5/order/create", {"category": CATEGORY, "symbol": SYMBOL, "side": "Sell", "orderType": "Market", "qty": str(qty)})
        if res.get("retCode") == 0:
            in_position = False
            send_telegram(f"VERKOCHT PEPE @ {price} - {reason}")
        return {"status": "sell", "price": price, "qty": qty, "bybit": res}

    # BUY - alleen als beide signalen goed zijn
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
            new_balance = usdt - use
            send_telegram(f"GEKOCHT PEPE @ {price} voor {use} USDT - {reason} | nieuw saldo ca. {new_balance:.2f}")
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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
