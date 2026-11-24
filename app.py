import os
import time
import hmac
import hashlib
import threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE_URL = os.getenv("BASE_URL", "https://api.bybit.eu")

SYMBOL = "PEPEUSDT"
COIN = "PEPE"
CASH_COIN = "USDT"

def sign(params, secret):
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()

def bybit_get(path, params=None):
    if params is None:
        params = {}
    params["api_key"] = API_KEY
    params["timestamp"] = str(int(time.time() * 1000))
    params["recv_window"] = "5000"
    params["sign"] = sign(params, API_SECRET)
    r = requests.get(BASE_URL + path, params=params, timeout=10)
    return r.json()

def get_price():
    try:
        r = requests.get(
            BASE_URL + "/v5/market/tickers",
            params={"category": "spot", "symbol": SYMBOL},
            timeout=10
        ).json()
        lst = r.get("result", {}).get("list", [])
        if not lst:
            return 0.0, r
        return float(lst[0].get("lastPrice", 0)), r
    except Exception as e:
        return 0.0, {"error": str(e)}

def get_balances():
    try:
        data = bybit_get("/v5/account/wallet-balance", {"accountType": "SPOT"})
        result = data.get("result", {})
        if isinstance(result, dict):
            lst = result.get("list", [])
        elif isinstance(result, list):
            lst = result
        else:
            lst = []

        balances = {}
        for acc in lst:
            coins = acc.get("coin", [])
            for c in coins:
                balances[c.get("coin")] = float(c.get("walletBalance", 0) or 0)
        return balances, data
    except Exception as e:
        return {}, {"error": str(e)}

def check_once():
    prijs, prijs_resp = get_price()
    balances, bal_resp = get_balances()
    geld = balances.get(CASH_COIN, 0.0)
    pepe = balances.get(COIN, 0.0)
    waarde = geld + (pepe * prijs)
    print(f"Check: {CASH_COIN} {geld:.2f} PEPE {pepe:.0f} waarde {waarde:.2f} prijs {prijs} in_positie {pepe > 0}", flush=True)
    return {
        "prijs": prijs,
        "geld": geld,
        "pepe": pepe,
        "waarde": waarde,
        "prijs_resp": prijs_resp,
        "bal_resp": bal_resp
    }

def bot_loop():
    while True:
        try:
            check_once()
        except Exception as e:
            print(f"BOT ERROR: {e}", flush=True)
        time.sleep(60)

@app.route("/")
def home():
    return "Pepebot-3 is live"

@app.route("/test-key")
def test_key():
    prijs, prijs_resp = get_price()
    balances, bal_resp = get_balances()
    return jsonify({
        "symbol": SYMBOL,
        "base_url": BASE_URL,
        "prijs": prijs,
        "USDT": balances.get("USDT", 0),
        "PEPE": balances.get("PEPE", 0),
        "prijs_resp": prijs_resp,
        "bal_resp": bal_resp
    })

@app.route("/check")
def check():
    return jsonify(check_once())

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
