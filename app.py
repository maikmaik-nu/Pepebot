import os
import time
import hmac
import hashlib
import requests
import threading
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.environ.get("【entity-BYBIT_API_KEY¦canonical_name=BYBIT】", "")
API_SECRET = os.environ.get("【entity-BYBIT_API_SECRET¦canonical_name=BYBIT】", "")
BASE_URL = os.environ.get("【entity-BYBIT_BASE_URL¦canonical_name=BYBIT】", "https://api.bybit.eu")
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"

def sign_request(params):
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    param_str = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    to_sign = timestamp + API_KEY + recv_window + param_str
    signature = hmac.new(
        API_SECRET.encode("utf-8"),
        to_sign.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": signature,
        "Content-Type": "application/json"
    }
    return headers

def get_balance(coin):
    try:
        params = {
            "accountType": "SPOT",
            "coin": coin
        }
        headers = sign_request(params)
        url = BASE_URL + "/v5/account/wallet-balance"
        r = requests.get(url, params=params, headers=headers, timeout=10)
        data = r.json()
        print(f"BALANCE RAW {coin}: {data}", flush=True)
        if data.get("retCode") != 0:
            print(f"Balance fail {coin}: retCode={data.get('retCode')} retMsg={data.get('retMsg')}", flush=True)
            return 0.0, data.get("retCode")
        lst = data.get("result", {}).get("list", [])
        if not lst:
            return 0.0, 0
        coins = lst[0].get("coin", [])
        for c in coins:
            if c.get("coin") == coin:
                return float(c.get("walletBalance", 0)), 0
        return 0.0, 0
    except Exception as e:
        print(f"Balance error {coin}: {e}", flush=True)
        return 0.0, -1

def get_price():
    try:
        url = BASE_URL + "/v5/market/tickers"
        params = {"category": CATEGORY, "symbol": SYMBOL}
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        lst = data.get("result", {}).get("list", [])
        if lst:
            return float(lst[0].get("lastPrice", 0))
        return 0.0
    except Exception as e:
        print(f"Price error: {e}", flush=True)
        return 0.0

def check_loop():
    while True:
        try:
            usdt, code1 = get_balance("USDT")
            usdc, code2 = get_balance("USDC")
            pepe, code3 = get_balance("PEPE")
            price = get_price()
            print(f"CHECK: USDT={usdt} USDC={usdc} PEPE={pepe} price={price} retCode={code1}", flush=True)
            if usdt == 0 and usdc == 0 and pepe == 0:
                print("Geen saldo, probeer over 60 sec opnieuw - geen stop", flush=True)
            time.sleep(60)
        except Exception as e:
            print(f"Loop error: {e}", flush=True)
            time.sleep(60)

@app.route("/")
def home():
    return "Pepebot-3 is live", 200

@app.route("/test-key")
def test_key():
    usdt, code1 = get_balance("USDT")
    usdc, code2 = get_balance("USDC")
    pepe, code3 = get_balance("PEPE")
    price = get_price()
    return jsonify({
        "api_key_set": bool(API_KEY),
        "base_url": BASE_URL,
        "usdt": usdt,
        "usdc": usdc,
        "pepe": pepe,
        "price": price,
        "retCode": code1
    })

threading.Thread(target=check_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
