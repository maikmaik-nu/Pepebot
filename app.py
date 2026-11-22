import os
import time
import hmac
import hashlib
import requests
import json as json_lib
from urllib.parse import urlencode
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = os.environ.get("BYBIT_BASE_URL", "https://api.bybit.eu").rstrip("/")
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()

ACCOUNT_TYPE = "UNIFIED"
SYMBOL = "PEPEUSDC"
CATEGORY = "spot"
TRADE_AMOUNT_USDC = 10
MIN_BALANCE = 5

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
}

def get_server_time():
    try:
        r = requests.get(BASE_URL + "/v5/market/time", headers=BASE_HEADERS, timeout=10)
        print("TIME STATUS:", r.status_code, "RAW:", r.text[:500])
        if r.status_code == 200:
            return int(r.json()["result"]["timeSecond"]) * 1000
    except Exception as e:
        print("Time error:", e)
    return int(time.time() * 1000)

def _sign(timestamp, recv_window, param_str):
    sign_str = timestamp + API_KEY + recv_window + param_str
    return hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()

def bybit_get(path, params):
    if not API_KEY or not API_SECRET:
        print("FOUT: BYBIT_API_KEY of BYBIT_API_SECRET ontbreekt!")
        return 0, {"retCode": -99, "retMsg": "API key/secret ontbreekt in Render env vars"}
    timestamp = str(get_server_time())
    recv_window = "5000"
    query = urlencode(sorted(params.items()))
    signature = _sign(timestamp, recv_window, query)
    headers = {
        **BASE_HEADERS,
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": signature,
        "X-BAPI-SIGN-TYPE": "2",
    }
    url = f"{BASE_URL}{path}?{query}" if query else f"{BASE_URL}{path}"
    print(f"CALL: {url} KEY_PREFIX: {API_KEY[:4]}...")
    r = requests.get(url, headers=headers, timeout=10)
    print("STATUS:", r.status_code)
    print("RAW:", r.text[:1000])
    try:
        return r.status_code, r.json()
    except Exception as e:
        print("JSON error:", e)
        return r.status_code, {"retCode": -1, "retMsg": f"Geen JSON terug, lege body. Geblokkeerd voor API? HTTP {r.status_code}"}

def bybit_post(path, body):
    if not API_KEY or not API_SECRET:
        return {"retCode": -99, "retMsg": "API key/secret ontbreekt"}
    timestamp = str(get_server_time())
    recv_window = "5000"
    body_str = json_lib.dumps(body, separators=(',', ':'))
    signature = _sign(timestamp, recv_window, body_str)
    headers = {
        **BASE_HEADERS,
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": signature,
        "X-BAPI-SIGN-TYPE": "2",
        "Content-Type": "application/json"
    }
    r = requests.post(BASE_URL + path, data=body_str, headers=headers, timeout=10)
    print("POST STATUS:", r.status_code)
    print("POST RAW:", r.text[:1000])
    try:
        return r.json()
    except Exception:
        return {"retCode": -1, "retMsg": f"Geen JSON: {r.text[:500]}"}

def get_balance():
    status, data = bybit_get("/v5/account/wallet-balance", {"accountType": ACCOUNT_TYPE})
    print(f"Saldo check {ACCOUNT_TYPE}: {data}")
    if data.get("retCode") != 0:
        return 0.0, 0.0, data.get("retCode"), data.get("retMsg", ""), status
    try:
        coin_list = data["result"]["list"][0]["coin"]
        usdc = 0.0
        pepe = 0.0
        for c in coin_list:
            if c["coin"] == "USDC":
                usdc = float(c.get("walletBalance", 0) or 0)
            if c["coin"] == "PEPE":
                pepe = float(c.get("walletBalance", 0) or 0)
        return usdc, pepe, 0, "ok", status
    except Exception as e:
        print("Saldo parse error:", e)
        return 0.0, 0.0, -1, str(e), status

def get_price():
    try:
        r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, headers=BASE_HEADERS, timeout=10)
        data = r.json()
        return float(data["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print("Price error:", e)
        return 0

def buy_pepe(amount_usdc, price):
    qty = str(int(amount_usdc / price))
    print(f"BUY {SYMBOL} qty={qty} voor {amount_usdc} USDC")
    return bybit_post("/v5/order/create", {"category": CATEGORY, "symbol": SYMBOL, "side": "Buy", "orderType": "Market", "qty": qty})

@app.route("/")
def home():
    usdc, pepe, retcode, msg, http_status = get_balance()
    price = get_price()
    in_position = pepe > 0
    print(f"CHECK: USDC={usdc} PEPE={pepe} price={price} in_position={in_position} retCode={retcode} http={http_status}")
    if retcode != 0:
        return jsonify({"status": "saldo_error", "http_status": http_status, "retCode": retcode, "retMsg": msg, "usdc": usdc, "pepe": pepe, "base_url": BASE_URL})
    if usdc < MIN_BALANCE:
        return jsonify({"status": "te_weinig", "usdc": usdc, "pepe": pepe})
    if not in_position and usdc >= TRADE_AMOUNT_USDC and price > 0:
        result = buy_pepe(TRADE_AMOUNT_USDC, price)
        return jsonify({"status": "bought", "usdc": usdc, "price": price, "order": result})
    return jsonify({"status": "ok", "usdc": usdc, "pepe": pepe, "price": price, "in_position": in_position})

@app.route("/test-key")
def test_key():
    try:
        pub = requests.get(BASE_URL + "/v5/market/time", headers=BASE_HEADERS, timeout=10)
        pub_status = pub.status_code
        pub_raw = pub.text[:200]
    except Exception as e:
        pub_status = -1
        pub_raw = str(e)
    usdc, pepe, retcode, msg, http_status = get_balance()
    return jsonify({
        "base_url": BASE_URL,
        "api_key_set": bool(API_KEY),
        "api_key_prefix": API_KEY[:4] + "..." if API_KEY else "",
        "public_time_status": pub_status,
        "public_time_raw": pub_raw,
        "private_wallet_status": http_status,
        "retCode": retcode,
        "retMsg": msg,
        "balances": {"USDC": usdc, "PEPE": pepe},
        "diagnose": "Als public_time_status ook 401 is met lege body = Render IP geblokkeerd door Bybit.eu. Als public 200 is en private 401 = key/domein fout."
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
