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

MIN_USDT = 10.0

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

def bybit_request(method, endpoint, params=None, signed=False):
    if params is None:
        params = {}
    url = BASE_URL + endpoint
    headers = {"Content-Type": "application/json"}
    query = ""
    body = None

    if method == "GET":
        if params:
            query = "&".join([f"{k}={v}" for k, v in params.items()])
            url = url + "?" + query
    else:
        body = params

    if signed:
        timestamp = str(int(time.time() * 1000))
        recv_window = "5000"
        if method == "GET":
            sign_str = timestamp + API_KEY + recv_window + query
        else:
            import json as js
            sign_str = timestamp + API_KEY + recv_window + js.dumps(body)
        sign = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
        headers.update({
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN": sign,
        })

    try:
        if method == "GET":
            r = requests.get(url, headers=headers, timeout=15)
        else:
            r = requests.post(url, headers=headers, json=body, timeout=15)
        print(f"API {endpoint}: {r.text[:500]}")
        return r.json()
    except Exception as e:
        print(f"API error {endpoint}: {e}")
        return {"retCode": -1, "retMsg": str(e)}

def get_balances():
    balances = {}
    # UNIFIED eerst, daarna SPOT - geld staat in Spot & Margin
    for acc_type in ["UNIFIED", "SPOT"]:
        res = bybit_request("GET", "/v5/account/wallet-balance", {"accountType": acc_type}, signed=True)
        print(f"Saldo check {acc_type}: retCode={res.get('retCode')} retMsg={res.get('retMsg')}")
        try:
            for acc in res.get("result", {}).get("list", []):
                for c in acc.get("coin", []):
                    val = float(c.get("walletBalance", 0) or 0)
                    if val > 0:
                        balances[c["coin"]] = val
            if balances:
                break
        except Exception as e:
            print(f"Parse saldo error {acc_type}: {e}")
    print(f"Balances gevonden: {balances}")
    return balances

def get_price():
    # Tickers is publiek, NIET signed
    res = bybit_request("GET", "/v5/market/tickers", {"category": "spot", "symbol": SYMBOL}, signed=False)
    try:
        return float(res["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print(f"Prijs error: {e}")
        return 0

def do_check():
    balances = get_balances()
    usdt = balances.get("USDT", 0)
    pepe = balances.get("PEPE", 0)
    price = get_price()
    print(f"USDT: {usdt} | PEPE prijs: {price}")

    if usdt < MIN_USDT:
        msg = f"Bot stopt: te weinig USDT ({usdt:.2f})"
        print(msg)
        send_telegram(msg)
        return {"status": "stopped", "usdt": usdt, "price": price, "reason": "te weinig USDT"}

    # Hier komt je buy-logica
    # Voorbeeld: koop voor 10 USDT als je nog geen PEPE hebt
    # qty = str(int(10 / price))
    # bybit_request("POST", "/v5/order/create", {"category": "spot", "symbol": SYMBOL, "side": "Buy", "orderType": "Market", "qty": qty}, signed=True)

    return {"status": "ok", "usdt": usdt, "pepe": pepe, "price": price}

@app.route("/")
def home():
    return "Pepebot draait"

@app.route("/status")
def status():
    balances = get_balances()
    price = get_price()
    return jsonify({"usdt": balances.get("USDT", 0), "pepe": balances.get("PEPE", 0), "price": price})

@app.route("/test-key")
def test_key():
    balances = get_balances()
    return jsonify(balances)

@app.route("/check")
def check():
    result = do_check()
    return jsonify(result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
