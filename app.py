import os
import time
import hmac
import hashlib
import requests
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = "https://api.bybit.eu"
API_KEY = os.environ.get("BYBIT_API_KEY", "")
API_SECRET = os.environ.get("BYBIT_API_SECRET", "")

# HIER MOET HIJ ZOEKEN - uit je screenshot
ACCOUNT_TYPE = "SPOT"
COIN = "USDC"
SYMBOL = "PEPEUSDC"  # want je geld is USDC, niet USDT
CATEGORY = "spot"
TRADE_AMOUNT_USDC = 10  # hoeveel USDC per trade
MIN_BALANCE = 5

def bybit_get(path, params):
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    # params sorteren voor signature
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sign_str = timestamp + API_KEY + recv_window + query
    signature = hmac.new(
        API_SECRET.encode(),
        sign_str.encode(),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": signature,
    }
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
    print("STATUS:", r.status_code)
    print("RAW:", r.text[:500])
    try:
        return r.status_code, r.json()
    except Exception as e:
        print("JSON error:", e)
        return r.status_code, {"retCode": -1, "retMsg": f"Geen JSON terug: {r.text[:200]}"}

def bybit_post(path, body):
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    import json as json_lib
    body_str = json_lib.dumps(body)
    sign_str = timestamp + API_KEY + recv_window + body_str
    signature = hmac.new(
        API_SECRET.encode(),
        sign_str.encode(),
        hashlib.sha256
    ).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": signature,
        "Content-Type": "application/json"
    }
    r = requests.post(BASE_URL + path, data=body_str, headers=headers, timeout=10)
    print("POST STATUS:", r.status_code)
    print("POST RAW:", r.text[:500])
    try:
        return r.json()
    except Exception:
        return {"retCode": -1, "retMsg": f"Geen JSON: {r.text[:200]}"}

def get_balance():
    # Alleen SPOT opvragen, niet UNIFIED / FUND
    status, data = bybit_get("/v5/account/wallet-balance", {
        "accountType": ACCOUNT_TYPE,
        "coin": COIN
    })
    print(f"Saldo check {ACCOUNT_TYPE}: {data}")

    if data.get("retCode") != 0:
        return 0.0, 0.0, data.get("retCode"), data.get("retMsg", "")

    try:
        coin_list = data["result"]["list"][0]["coin"]
        usdc = 0.0
        pepe = 0.0
        for c in coin_list:
            if c["coin"] == "USDC":
                usdc = float(c.get("walletBalance", 0) or 0)
            if c["coin"] == "PEPE":
                pepe = float(c.get("walletBalance", 0) or 0)
        return usdc, pepe, 0, "ok"
    except Exception as e:
        print("Saldo parse error:", e)
        return 0.0, 0.0, -1, str(e)

def get_price():
    try:
        status, data = bybit_get("/v5/market/tickers", {
            "category": CATEGORY,
            "symbol": SYMBOL
        })
        return float(data["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print("Price error:", e)
        return 0

def buy_pepe(amount_usdc, price):
    qty = str(int(amount_usdc / price))
    print(f"BUY {SYMBOL} qty={qty} voor {amount_usdc} USDC")
    return bybit_post("/v5/order/create", {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": "Buy",
        "orderType": "Market",
        "qty": qty
    })

@app.route("/")
def home():
    usdc, pepe, retcode, msg = get_balance()
    price = get_price()
    in_position = pepe > 0

    print(f"CHECK: USDC={usdc} PEPE={pepe} price={price} in_position={in_position} retCode={retcode}")

    if retcode != 0:
        return jsonify({
            "status": "saldo_error",
            "retCode": retcode,
            "retMsg": msg,
            "usdc": usdc,
            "pepe": pepe,
            "tip": "10003 = key klopt niet of key is van bybit.com i.p.v. bybit.eu"
        })

    if usdc < MIN_BALANCE:
        return jsonify({"status": "te_weinig", "usdc": usdc, "pepe": pepe})

    if not in_position and usdc >= TRADE_AMOUNT_USDC and price > 0:
        result = buy_pepe(TRADE_AMOUNT_USDC, price)
        return jsonify({"status": "bought", "usdc": usdc, "price": price, "order": result})

    return jsonify({
        "status": "ok",
        "usdc": usdc,
        "pepe": pepe,
        "price": price,
        "in_position": in_position
    })

@app.route("/test-key")
def test_key():
    usdc, pepe, retcode, msg = get_balance()
    return jsonify({
        "api_key_set": bool(API_KEY),
        "accountType_used": ACCOUNT_TYPE,
        "coin_gezocht": COIN,
        "symbol": SYMBOL,
        "retCode": retcode,
        "retMsg": msg,
        "balances": {"USDC": usdc, "PEPE": pepe}
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
