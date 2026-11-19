import os
import time
import hmac
import hashlib
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# ===== CONFIG =====
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
API_KEY = os.environ.get("【entity-BYBIT_API_KEY¦canonical_name=Bybit】", "")
API_SECRET = os.environ.get("【entity-BYBIT_API_SECRET¦canonical_name=Bybit】", "")
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

def bybit_request(method, endpoint, params=None, signed=True):
    if params is None:
        params = {}
    # DE FIX: timestamp meesturen, VOOR de signature
    if signed:
        params["api_key"] = API_KEY
        params["timestamp"] = str(int(time.time() * 1000))
        params["recv_window"] = "5000"
        # signature maken van alle params gesorteerd
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        sign = hmac.new(
            API_SECRET.encode(),
            query.encode(),
            hashlib.sha256
        ).hexdigest()
        params["sign"] = sign

    url = BASE_URL + endpoint
    try:
        if method == "GET":
            r = requests.get(url, params=params, timeout=15)
        else:
            r = requests.post(url, json=params, timeout=15)
        print(f"【entity-Bybit¦canonical_name=Bybit】 {endpoint}: {r.status_code} {r.text[:300]}")
        return r.json()
    except Exception as e:
        print(f"【entity-Bybit¦canonical_name=Bybit】 error: {e}")
        return {"retCode": -1, "retMsg": str(e)}

def get_balances():
    # Alleen accountType, GEEN coin parameter
    res = bybit_request("GET", "/v5/account/wallet-balance", {"accountType": "SPOT"})
    print(f"Saldo check SPOT: retCode={res.get('retCode')} retMsg={res.get('retMsg')}")
    balances = {}
    try:
        lst = res.get("result", {}).get("list", [])
        for acc in lst:
            for c in acc.get("coin", []):
                balances[c["coin"]] = float(c.get("walletBalance", 0))
    except Exception as e:
        print(f"Parse saldo error: {e}")
    return balances

def get_price():
    res = bybit_request("GET", "/v5/market/tickers", {"category": CATEGORY, "symbol": SYMBOL}, signed=False)
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
        msg = f"Bot stopt: te weinig USDT ({usdt:.2f})"
        print(msg)
        return msg
    if price <= 0:
        return "Geen prijs"

    # Koop voor 10 USDT
    qty = round(10.0 / price, 0)
    print(f"Koop {qty} PEPE voor 10 USDT")
    order = bybit_request("POST", "/v5/order/create", {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": "Buy",
        "orderType": "Market",
        "qty": str(qty)
    })
    if order.get("retCode") == 0:
        send_telegram(f"Gekocht: {qty} PEPE @ {price}")
        return f"Gekocht {qty}"
    else:
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
