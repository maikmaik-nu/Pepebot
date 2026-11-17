import os, time, hmac, hashlib, requests
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.getenv("BYBIT_API_KEY")
API_SECRET = os.getenv("BYBIT_API_SECRET")
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"

def sign(params):
    qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sig = hmac.new(API_SECRET.encode(), (str(int(time.time()*1000)) + API_KEY + "5000" + qs).encode(), hashlib.sha256).hexdigest()
    return sig

@app.route("/")
def saldo():
    try:
        ts = str(int(time.time()*1000))
        params = {"accountType": "SPOT", "coin": "PEPE,USDT"}
        qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        sig = hmac.new(API_SECRET.encode(), (ts + API_KEY + "5000" + qs).encode(), hashlib.sha256).hexdigest()

        r = requests.get(f"{BASE_URL}/v5/account/wallet-balance?{qs}", headers={
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": "5000",
            "X-BAPI-SIGN": sig
        }, timeout=10)

        data = r.json()
        if data.get("retCode") != 0:
            return jsonify({"status": "ERROR", "pepe": None, "usdt": None, "bybit": data})

        coins = {c["coin"]: c["walletBalance"] for c in data["result"]["list"][0]["coin"]}
        return jsonify({
            "status": "OK",
            "pepe": coins.get("PEPE"),
            "usdt": coins.get("USDT"),
            "key_start": API_KEY[:4] + "***"
        })
    except Exception as e:
        return jsonify({"status": "ERROR", "pepe": None, "usdt": None, "error": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
