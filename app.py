import os
import time
import hmac
import hashlib
import requests
import threading
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
API_KEY = os.environ.get("BYBIT_API_KEY", "")
API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        print(f"Telegram error: {e}")

def 【entity-bybit_request¦canonical_name=Bybit】(method, endpoint, params=None, signed=True):
    if params is None:
        params = {}
    url = BASE_URL + endpoint
    headers = {"Content-Type": "application/json"}
    timestamp = str(int(time.time() * 1000))
    if signed:
        # V5 signing: timestamp + key + recv_window + query/body
        recv_window = "5000"
        if method == "GET":
            query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            sign_str = timestamp + API_KEY + recv_window + query
        else:
            import json as js
            body = js.dumps(params, separators=(",", ":"))
            sign_str = timestamp + API_KEY + recv_window + body
        sign = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
        headers["X-BAPI-API-KEY"] = API_KEY
        headers["X-BAPI-TIMESTAMP"] = timestamp
        headers["X-BAPI-RECV-WINDOW"] = recv_window
        headers["X-BAPI-SIGN"] = sign
    try:
        if method == "GET":
            r = requests.get(url, params=params, headers=headers, timeout=15)
        else:
            r = requests.post(url, json=params, headers=headers, timeout=15)
        print(f"【entity-Bybit¦canonical_name=Bybit】 {endpoint}: {r.text[:400]}")
        return r.json()
    except Exception as e:
        print(f"【entity-Bybit¦canonical_name=Bybit】 error: {e}")
        return {"retCode": -1, "retMsg": str(e)}

def get_balances():
    res = 【entity-bybit_request¦canonical_name=Bybit】("GET", "/v5/account/wallet-balance", {"accountType": "SPOT"})
    print(f"Saldo check SPOT: retCode={res.get('retCode')} retMsg={res.get('retMsg')}")
    balances = {}
    try:
        for acc in res.get("result", {}).get("list", []):
            for c in acc.get("coin", []):
                balances[c["coin"]] = float(c.get("walletBalance", 0))
    except Exception as e:
        print(f"Parse saldo error: {e}")
    return balances

def get_price():
    res = 【entity-bybit_request¦canonical_name=Bybit】("GET", "/v5/market/tickers", {"category": CATEGORY, "symbol": SYMBOL}, signed=False)
    try:
        return float(res["result"]["list"][0]["lastPrice"])
    except Exception:
        return 0

def do_check():
    balances = get_balances()
    usdt = balances.get("USDT", 0)
    price = get_price()
    print(f"USDT: {usdt} | PEPE prijs: {price}")
    if usdt < 10:
        print(f"Bot stopt: te weinig USDT ({usdt:.2f})")
        return f"Te weinig USDT ({usdt:.2f})"
    if price <= 0:
        return "Geen prijs"
    qty = round(10.0 / price, 0)
    print(f"Koop {qty} PEPE voor 10 USDT")
    order = bybit_request("POST", "/v5/order/create", {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": "Buy",
        "orderType": "Market",
        "qty": str(int(qty))
    })
    if order.get("retCode") == 0:
        send_telegram(f"Gekocht: {qty} PEPE @ {price}")
        return f"Gekocht {qty}"
    return f"Order mislukt: {order.get('retMsg')}"

@app.route("/")
def home():
    return "Pepebot-3 draait"

@app.route("/test-key")
def test_key():
    b = get_balances()
    return jsonify({"USDT": b.get("USDT", 0), "balances": b})

@app.route("/check")
def check():
    return do_check()

def loop():
    time.sleep(10)
    while True:
        try:
            do_check()
        except Exception as e:
            print(f"Loop error: {e}")
        time.sleep(300)

threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
