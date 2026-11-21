import os
import time
import hmac
import hashlib
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# --- CONFIG ---
API_KEY = os.environ.get("BYBIT_API_KEY", "")
API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"

# --- 【entity-BYBIT¦canonical_name=BYBIT】 HELPER ---
def bybit_get(path, params=""):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    sign_str = ts + API_KEY + recv + params
    sign = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sign,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json"
    }
    url = BASE_URL + path
    if params:
        url += "?" + params
    r = requests.get(url, headers=headers, timeout=10)
    try:
        return r.status_code, r.json()
    except:
        return r.status_code, {"raw": r.text}

def get_balances():
    # Probeer SPOT eerst, daarna UNIFIED
    for account_type in ["SPOT", "UNIFIED"]:
        status, data = bybit_get("/v5/account/wallet-balance", f"accountType={account_type}")
        if data.get("retCode") == 0:
            coins = {}
            try:
                lst = data["result"]["list"][0]["coin"]
                for c in lst:
                    coins[c["coin"]] = {
                        "wallet": c.get("walletBalance"),
                        "available": c.get("availableToWithdraw")
                    }
            except Exception as e:
                coins = {"parse_error": str(e)}
            return account_type, status, data, coins
        else:
            # Onthoud de fout, probeer de volgende
            last_error = (account_type, status, data)
    return last_error[0], last_error[1], last_error[2], {}

# --- ROUTES ---
@app.route("/")
def home():
    return jsonify({"status": "live", "bot": "Pepebot-3", "symbol": SYMBOL})

@app.route("/test-key")
def test_key():
    # Dit is de echte test - verbergt niks meer
    account_type, status, data, coins = get_balances()
    return jsonify({
        "api_key_set": bool(API_KEY),
        "base_url": BASE_URL,
        "accountType_used": account_type,
        "http_status": status,
        "bybit_retCode": data.get("retCode"),
        "bybit_retMsg": data.get("retMsg"),
        "balances": coins,
        "raw": data
    })

@app.route("/check")
def check():
    account_type, status, data, coins = get_balances()
    usdt = 0
    pepe = 0
    try:
        usdt = float(coins.get("USDT", {}).get("wallet") or 0)
        pepe = float(coins.get("PEPE", {}).get("wallet") or 0)
    except:
        pass
    return jsonify({
        "accountType": account_type,
        "retCode": data.get("retCode"),
        "retMsg": data.get("retMsg"),
        "USDT": usdt,
        "PEPE": pepe,
        "in_position": pepe > 0
    })

@app.route("/balance")
def balance():
    return check()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
