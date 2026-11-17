import os
import time
import hmac
import hashlib
import requests
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE_URL = "https://api.bybit.eu"

def get_saldo():
    # Geen keys? Dan meteen duidelijk
    if not API_KEY or not API_SECRET:
        return {"status": "ERROR", "pepe": None, "usdt": None, "error": "Geen API key in Render"}

    try:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        params = {"accountType": "SPOT", "coin": "PEPE,USDT"}
        qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))

        # Bybit V5 signature: timestamp + apiKey + recvWindow + queryString
        payload = ts + API_KEY + recv + qs
        sig = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

        r = requests.get(
            f"{BASE_URL}/v5/account/wallet-balance?{qs}",
            headers={
                "X-BAPI-API-KEY": API_KEY,
                "X-BAPI-TIMESTAMP": ts,
                "X-BAPI-RECV-WINDOW": recv,
                "X-BAPI-SIGN": sig,
            },
            timeout=10,
        )
        data = r.json()

        if data.get("retCode") != 0:
            return {"status": "ERROR", "pepe": None, "usdt": None, "bybit": data}

        coins = {}
        for c in data["result"]["list"][0]["coin"]:
            coins[c["coin"]] = c["walletBalance"]

        return {
            "status": "OK",
            "pepe": coins.get("PEPE"),
            "usdt": coins.get("USDT"),
            "key_start": API_KEY[:4] + "***",
        }

    except Exception as e:
        return {"status": "ERROR", "pepe": None, "usdt": None, "error": str(e)}

# ALLE routes -> zelfde functie, dus geen 404 meer
@app.route("/")
@app.route("/saldo")
@app.route("/balance")
@app.route("/status")
@app.route("/check")
def saldo():
    return jsonify(get_saldo())

@app.route("/health")
def health():
    return jsonify({"status": "LIVE"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
