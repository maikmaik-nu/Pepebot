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

# --- BYBIT HELPER ---
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
    # 1. SPOT moet via ASSET endpoint, NIET via wallet-balance
    status, data = bybit_get("/v5/asset/transfer/query-account-coins-balance", "accountType=SPOT&coin=USDT")
    if data.get("retCode") == 0:
        coins = {}
        try:
            for c in data["result"]["balance"]:
                coins[c["coin"]] = {
                    "wallet": c.get("walletBalance"),
                    "available": c.get("availableToWithdraw")
                }
        except Exception as e:
            coins = {"parse_error": str(e)}
        # PEPE ook ophalen
        _, data2 = bybit_get("/v5/asset/transfer/query-account-coins-balance", "accountType=SPOT&coin=PEPE")
        if data2.get("retCode") == 0:
            for c in data2["result"]["balance"]:
                coins[c["coin"]] = {
                    "wallet": c.get("walletBalance"),
                    "available": c.get("availableToWithdraw")
                }
        return "SPOT", status, data, coins

    # 2. Alleen als SPOT echt faalt, probeer UNIFIED via wallet-balance
    status2, data2 = bybit_get("/v5/account/wallet-balance", "accountType=UNIFIED")
    if data2.get("retCode") == 0:
        coins = {}
        try:
            lst = data2["result"]["list"][0]["coin"]
            for c in lst:
                coins[c["coin"]] = {
                    "wallet": c.get("walletBalance"),
                    "available": c.get("availableToWithdraw")
                }
        except Exception as e:
            coins = {"parse_error": str(e)}
        return "UNIFIED", status2, data2, coins

    # Beide faalden - geef SPOT fout terug, niet UNIFIED
    return "SPOT", status, data, {}

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
