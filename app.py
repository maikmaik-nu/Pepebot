"""
PEPEBOT-3 - Werkende 【entity-Bybit¦canonical_name=Bybit】 v5 Bot
"""
import os
import time
import hmac
import hashlib
import json
import requests
import threading
from flask import Flask

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TESTNET = os.getenv("BYBIT_TESTNET", "false").lower() == "true"
SYMBOL = os.getenv("SYMBOL", "PEPEUSDT")
TRADE_USDT = float(os.getenv("TRADE_USDT", "10"))
BASE_URL = "https://api-testnet.bybit.com" if TESTNET else "https://api.bybit.com"

app = Flask(__name__)

@app.route("/")
def home():
    return "PEPEBOT-3 RUNNING", 200

def sign(params_str, timestamp):
    payload = f"{timestamp}{API_KEY}5000{params_str}"
    return hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

def bybit_get(path, params):
    timestamp = str(int(time.time() * 1000))
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    signature = sign(query, timestamp)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": "5000",
        "X-BAPI-SIGN": signature,
    }
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
    return r.json()

def bybit_post(path, body):
    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(body)
    signature = sign(body_str, timestamp)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": "5000",
        "X-BAPI-SIGN": signature,
        "Content-Type": "application/json"
    }
    r = requests.post(BASE_URL + path, data=body_str, headers=headers, timeout=10)
    return r.json()

def get_prijs():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": "linear", "symbol": SYMBOL}, timeout=10)
        data = r.json()
        lst = data.get("result", {}).get("list", [])
        if not lst:
            print(f"Prijs fout: lege list, response={data}", flush=True)
            return 0.0
        prijs = float(lst[0].get("lastPrice", 0))
        return prijs
    except Exception as e:
        print(f"Prijs fout: {e}", flush=True)
        return 0.0

def get_balance():
    try:
        if not API_KEY or not API_SECRET:
            print("Balance fail: GEEN API KEY / SECRET in Render Environment!", flush=True)
            return 0.0, 0.0
        data = bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        if data.get("retCode") != 0:
            print(f"Balance fail: {data}", flush=True)
            if data.get("retCode") == 10003:
                print(">>> 10003 = KEY HEEFT GEEN PERMISSIE. In Bybit: API > Edit > Unified Trading Read + Trade AAN, IP whitelist UIT.", flush=True)
            return 0.0, 0.0
        coins = data["result"]["list"][0]["coin"]
        usdt = 0.0
        pepe = 0.0
        for c in coins:
            if c["coin"] == "USDT":
                usdt = float(c.get("walletBalance", 0) or 0)
            if c["coin"] == "PEPE":
                pepe = float(c.get("walletBalance", 0) or 0)
        return usdt, pepe
    except Exception as e:
        print(f"Balance fail exception: {e}", flush=True)
        return 0.0, 0.0

def plaats_order(side, qty):
    body = {
        "category": "linear",
        "symbol": SYMBOL,
        "side": side,
        "orderType": "Market",
        "qty": str(int(qty)),
        "timeInForce": "GTC",
        "positionIdx": 0
    }
    res = bybit_post("/v5/order/create", body)
    print(f"Order {side} {qty} -> {res}", flush=True)
    return res

def check_en_trade():
    prijs = get_prijs()
    geld, pepe = get_balance()
    print(f"Check: Geld {geld:.2f} PEPE {pepe} prijs {prijs}", flush=True)
    if prijs == 0 or geld == 0:
        print("Niet traden: prijs 0 of geld 0 - check keys / balance", flush=True)
        return
    if pepe == 0 and geld >= TRADE_USDT:
        qty = TRADE_USDT / prijs
        qty = int(qty // 1000 * 1000)
        if qty >= 1000:
            print(f"KOOP signaal: {qty} PEPE voor {TRADE_USDT} USDT", flush=True)
            plaats_order("Buy", qty)
        else:
            print(f"Qty te klein: {qty}", flush=True)
    else:
        print("Geen actie: al PEPE in bezit of te weinig geld", flush=True)

def loop():
    time.sleep(5)
    while True:
        try:
            check_en_trade()
        except Exception as e:
            print(f"Loop fout: {e}", flush=True)
        time.sleep(60)

threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
