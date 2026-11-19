import os
import time
import hmac
import hashlib
import threading
import requests
from flask import Flask, jsonify

# ========= CONFIG =========
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
INTERVAL = "15"
CHECK_INTERVAL = 60

app = Flask(__name__)
in_position = False
last_price = 0
running = False

# ========= HELPERS =========
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram niet ingesteld:", msg)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        print("Telegram fout:", e)

def sign(params):
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sig = hmac.new(
        BYBIT_API_SECRET.encode(),
        query.encode(),
        hashlib.sha256
    ).hexdigest()
    return query, sig

def bybit_get(path, params):
    params["api_key"] = BYBIT_API_KEY
    params["timestamp"] = str(int(time.time() * 1000))
    params["recv_window"] = "5000"
    query, sig = sign(params)
    headers = {"X-BAPI-API-KEY": BYBIT_API_KEY}
    url = f"{BASE_URL}{path}?{query}&sign={sig}"
    r = requests.get(url, headers=headers, timeout=15)
    if not r.text.strip():
        return {"retCode": -1, "retMsg": f"Lege response, status {r.status_code}"}
    try:
        return r.json()
    except Exception:
        return {"retCode": -1, "retMsg": f"Geen JSON: {r.text[:200]}"}

def bybit_post(path, params):
    params["api_key"] = BYBIT_API_KEY
    params["timestamp"] = str(int(time.time() * 1000))
    params["recv_window"] = "5000"
    query, sig = sign(params)
    headers = {"X-BAPI-API-KEY": BYBIT_API_KEY, "Content-Type": "application/json"}
    url = f"{BASE_URL}{path}"
    body = dict(params)
    body["sign"] = sig
    r = requests.post(url, headers=headers, json=body, timeout=15)
    if not r.text.strip():
        return {"retCode": -1, "retMsg": f"Lege response, status {r.status_code}"}
    try:
        return r.json()
    except Exception:
        return {"retCode": -1, "retMsg": f"Geen JSON: {r.text[:200]}"}

def get_balance():
    # GEEN coin= meesturen, alleen accountType
    data = bybit_get("/v5/account/wallet-balance", {"accountType": "SPOT"})
    if data.get("retCode") != 0:
        return 0, 0, data
    try:
        coins = data["result"]["list"][0]["coin"]
        usdt = 0
        pepe = 0
        for c in coins:
            if c["coin"] == "USDT":
                usdt = float(c.get("walletBalance", 0))
            if c["coin"] == "PEPE":
                pepe = float(c.get("walletBalance", 0))
        return usdt, pepe, data
    except Exception as e:
        return 0, 0, {"retCode": -1, "retMsg": str(e)}

def get_klines():
    data = bybit_get("/v5/market/kline", {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "limit": "10"
    })
    if data.get("retCode") != 0:
        return None, data
    try:
        # Bybit: [start, open, high, low, close, volume, turnover]
        klines = data["result"]["list"][::-1]
        return klines, data
    except Exception as e:
        return None, {"retCode": -1, "retMsg": str(e)}

def get_price():
    data = bybit_get("/v5/market/tickers", {"category": CATEGORY, "symbol": SYMBOL})
    try:
        return float(data["result"]["list"][0]["lastPrice"])
    except Exception:
        return 0

def do_check():
    global in_position, last_price
    usdt, pepe, bal_data = get_balance()
    if bal_data.get("retCode") != 0:
        print(f"Saldo check SPOT: retCode={bal_data.get('retCode')} retMsg={bal_data.get('retMsg')}")
        return f"Saldo fout: {bal_data.get('retMsg')}"
    klines, k_data = get_klines()
    if klines is None or len(klines) < 3:
        print("Te weinig klines")
        return "Te weinig klines"
    price = get_price()
    last_price = price
    last = klines[-1]
    prev = klines[-2]
    last_open = float(last[1])
    last_close = float(last[4])
    prev_close = float(prev[4])
    print(f"Saldo USDT: {usdt} | PEPE: {pepe} | Prijs: {price} | last_close {last_close} > last_open {last_open} = {last_close > last_open} | > prev {prev_close} = {last_close > prev_close}")
    # KOOP: 2 groene candles + genoeg USDT + niet in positie
    if not in_position and last_close > last_open and last_close > prev_close and usdt > 5:
        qty = round(usdt / price, 0)
        # Bybit PEPE qty moet heel getal zijn, min 1000
        if qty < 1000:
            return "Te weinig USDT voor min qty"
        order = bybit_post("/v5/order/create", {
            "category": CATEGORY,
            "symbol": SYMBOL,
            "side": "Buy",
            "orderType": "Market",
            "qty": str(int(qty))
        })
        if order.get("retCode") == 0:
            in_position = True
            msg = f"KOOP uitgevoerd: {qty} PEPE @ {price} | USDT was {usdt}"
            print(msg)
            send_telegram(msg)
            return msg
        else:
            print(f"KOOP fout: {order}")
            return f"KOOP fout: {order.get('retMsg')}"
    # VERKOOP: candle rood en in positie
    if in_position and last_close < last_open and pepe > 1000:
        order = bybit_post("/v5/order/create", {
            "category": CATEGORY,
            "symbol": SYMBOL,
            "side": "Sell",
            "orderType": "Market",
            "qty": str(int(pepe))
        })
        if order.get("retCode") == 0:
            in_position = False
            msg = f"VERKOOP uitgevoerd: {pepe} PEPE @ {price}"
            print(msg)
            send_telegram(msg)
            return msg
        else:
            print(f"VERKOOP fout: {order}")
            return f"VERKOOP fout: {order.get('retMsg')}"
    print(f"Geen signaal. In positie: {in_position}")
    return f"Geen signaal. In positie: {in_position} | Saldo USDT {usdt} | Prijs {price}"

def loop():
    global running
    running = True
    send_telegram("Bot gestart - Rente-op-rente AAN")
    while True:
        try:
            do_check()
        except Exception as e:
            print(f"Loop fout: {e}")
        time.sleep(CHECK_INTERVAL)

# ========= ROUTES =========
@app.route("/")
def home():
    return "Pepebot-3 draait"

@app.route("/test-key")
def test_key():
    if not BYBIT_API_KEY or not BYBIT_API_SECRET:
        return jsonify({"ok": False, "fout": "BYBIT_API_KEY of BYBIT_API_SECRET ontbreekt in Render Environment"})
    usdt, pepe, data = get_balance()
    return jsonify({
        "ok": data.get("retCode") == 0,
        "retCode": data.get("retCode"),
        "retMsg": data.get("retMsg"),
        "saldo_usdt": usdt,
        "saldo_pepe": pepe,
        "in_positie": in_position,
        "prijs": last_price
    })

@app.route("/check")
def check():
    return jsonify({"result": do_check()})

@app.route("/status")
def status():
    usdt, pepe, data = get_balance()
    return jsonify({"saldo_usdt": usdt, "saldo_pepe": pepe, "in_positie": in_position, "prijs": last_price})

# Start loop 1x
if not running:
    t = threading.Thread(target=loop, daemon=True)
    t.start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
