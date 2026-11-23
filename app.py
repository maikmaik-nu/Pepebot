import os, time, hmac, hashlib, requests
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"

def sign(params):
    timestamp = str(int(time.time() * 1000))
    recv = "5000"
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    to_sign = timestamp + API_KEY + recv + query
    signature = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": signature,
        "Content-Type": "application/json"
    }
    return headers

def bybit_get(path, params):
    headers = sign(params)
    r = requests.get(f"{BASE_URL}{path}", params=params, headers=headers, timeout=10)
    return r.json()

def get_balance_spot():
    # 1e poging: SPOT via wallet-balance - hier staat je geld
    try:
        params = {"accountType": "SPOT", "coin": "USDT"}
        data = bybit_get("/v5/account/wallet-balance", params)
        if data.get("retCode") == 0:
            coins = data["result"]["list"][0]["coin"]
            for c in coins:
                if c["coin"] == "USDT":
                    return float(c["walletBalance"]), data
                if c["coin"] == "PEPE":
                    pepe_bal = float(c["walletBalance"])
            # USDT gevonden? Anders 0 terug met data
            return 0.0, data
        else:
            last_error = data
    except Exception as e:
        last_error = {"error": str(e)}

    # 2e poging: fallback via asset endpoint - werkt ook met alleen Spot rechten
    try:
        params = {"accountType": "SPOT", "coin": "USDT"}
        data = bybit_get("/v5/asset/transfer/query-account-coins-balance", params)
        if data.get("retCode") == 0:
            bal = data["result"]["balance"]
            for c in bal:
                if c["coin"] == "USDT":
                    return float(c["walletBalance"]), data
    except Exception as e:
        pass

    return 0.0, last_error

def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        data = r.json()
        if data.get("retCode") == 0:
            return float(data["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print(f"Price error: {e}")
    return 0.0

@app.route("/")
def home():
    return "Pepebot-3 live"

@app.route("/test-key")
def test_key():
    usdt, raw = get_balance_spot()
    return jsonify({
        "api_key_set": bool(API_KEY),
        "accountType_used": "SPOT",
        "base_url": BASE_URL,
        "usdt": usdt,
        "balances": raw,
        "status": "OK" if usdt > 0 else "CHECK_BYBIT"
    })

@app.route("/check")
def check():
    usdt, raw = get_balance_spot()
    price = get_price()
    return jsonify({
        "USDT": usdt,
        "PRICE": price,
        "SYMBOL": SYMBOL,
        "in_position": False,
        "raw": raw
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
