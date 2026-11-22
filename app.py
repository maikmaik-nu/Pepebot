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
    # 【entity-Bybit¦canonical_name=BYBIT】.eu: Spot & Margin = FUND in de API
    # Probeer in deze volgorde, pak de eerste die retCode 0 geeft
    tries = [
        ("/v5/asset/transfer/query-account-coins-balance", "accountType=FUND&coin=USDT", "FUND-asset"),
        ("/v5/asset/transfer/query-account-coins-balance", "accountType=SPOT&coin=USDT", "SPOT-asset"),
        ("/v5/account/wallet-balance", "accountType=UNIFIED", "UNIFIED-wallet"),
        ("/v5/asset/transfer/query-account-coins-balance", "accountType=UNIFIED&coin=USDT", "UNIFIED-asset"),
        ("/v5/account/wallet-balance", "accountType=CONTRACT", "CONTRACT-wallet"),
    ]

    last_data = {}
    for path, params, label in tries:
        status, data = bybit_get(path, params)
        if data.get("retCode") == 0:
            coins = {}
            # Asset endpoint: result.balance
            if "balance" in data.get("result", {}):
                for c in data["result"]["balance"]:
                    coins[c["coin"]] = {
                        "wallet": c.get("walletBalance"),
                        "available": c.get("availableToWithdraw")
                    }
                # PEPE erbij ophalen
                _, data2 = bybit_get(path, params.replace("USDT", "PEPE"))
                if data2.get("retCode") == 0 and "balance" in data2.get("result", {}):
                    for c in data2["result"]["balance"]:
                        coins[c["coin"]] = {
                            "wallet": c.get("walletBalance"),
                            "available": c.get("availableToWithdraw")
                        }
            # Wallet-balance endpoint: result.list[0].coin
            elif "list" in data.get("result", {}):
                try:
                    for c in data["result"]["list"][0]["coin"]:
                        coins[c["coin"]] = {
                            "wallet": c.get("walletBalance"),
                            "available": c.get("availableToWithdraw")
                        }
                except Exception as e:
                    coins = {"parse_error": str(e)}
            return label, status, data, coins
        last_data = data

    return "FUND-asset", 200, last_data, {}

# --- ROUTES ---
@app.route("/")
def home():
    return jsonify({"status": "live", "bot": "Pepebot-3", "symbol": SYMBOL})

@app.route("/test-key")
def test_key():
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
