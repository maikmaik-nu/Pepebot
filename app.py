from flask import Flask, jsonify
import hmac
import hashlib
import time
import requests
import os

app = Flask(__name__)

BASE = "https://api.bybit.com"
API_KEY = os.environ.get("BYBIT_API_KEY", "")
API_SECRET = os.environ.get("BYBIT_API_SECRET", "")

def sign(params, secret):
    query = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()

@app.route("/")
def home():
    return "Pepebot draait"

@app.route("/test-key")
def test_key():
    timestamp = str(int(time.time() * 1000))
    params = {
        "api_key": API_KEY,
        "timestamp": timestamp,
    }
    params["sign"] = sign(params, API_SECRET)

    r = requests.get(f"{BASE}/v5/account/wallet-balance", params=params, timeout=10)
    data = r.json()

    return jsonify({
        "status": r.status_code,
        "bybit": data,
        "base": BASE,
        "key_preview": API_KEY[:4] + "***" if API_KEY else "GEEN KEY"
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
