import os
import time
import hmac
import hashlib
import requests
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.environ.get("BYBIT_API_KEY", "")
API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
QTY = "100000"

def make_sign(ts, recv, query):
    msg = ts + API_KEY + recv + query
    return hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

def get_headers(ts, recv, sign):
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json"
    }

def get_balance():
    try:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        query = "accountType=SPOT&coin=USDT"
        sign = make_sign(ts, recv, query)
        headers = get_headers(ts, recv, sign)
        url = BASE_URL + "/v5/account/wallet-balance?" + query
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        lists = data.get("result", {}).get("list", [])
        if len(lists) == 0:
            return 0.0, data
        coins = lists[0].get("coin", [])
        for c in coins:
            if c.get("coin") == "USDT":
                return float(c.get("walletBalance", 0)), data
        return 0.0, data
    except Exception as e:
        return 0.0, {"error": str(e)}

def get_price():
    try:
        url = BASE_URL + "/v5/market/tickers?category=spot&symbol=" + SYMBOL
        r = requests.get(url, timeout=10)
        data = r.json()
        lst = data.get("result", {}).get("list", [])
        if len(lst) == 0:
            return 0.0
        return float(lst[0].get("lastPrice", 0))
    except Exception:
        return 0.0

def do_buy():
    try:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        body = {
            "category": "spot",
            "symbol": SYMBOL,
            "side": "Buy",
            "orderType": "Market",
            "qty": QTY
        }
        import json
        body_str = json.dumps(body)
        sign = make_sign(ts, recv, body_str)
        headers = get_headers(ts, recv, sign)
        url = BASE_URL + "/v5/order/create"
        r = requests.post(url, headers=headers, data=body_str, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

@app.route("/")
def home():
    return "Pepebot-3 draait"

@app.route("/test-key")
def test_key():
    bal, raw = get_balance()
    price = get_price()
    return jsonify({
        "api_key_set": bool(API_KEY),
        "base_url": BASE_URL,
        "category_used": CATEGORY,
        "symbol": SYMBOL,
        "usdt_balance": bal,
        "pepe_price": price,
        "raw": raw
    })

@app.route("/check")
def check():
    bal, raw = get_balance()
    price = get_price()
    if bal > 5:
        return jsonify({"status": "WOULD BUY", "balance": bal, "price": price})
    return jsonify({"status": "NOT ENOUGH", "balance": bal, "price": price})

@app.route("/buy")
def buy():
    bal, raw = get_balance()
    if bal < 5:
        return jsonify({"status": "NOT ENOUGH", "balance": bal})
    result = do_buy()
    return jsonify({"status": "BUY SENT", "balance": bal, "result": result})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
