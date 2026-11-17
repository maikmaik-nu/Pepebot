import os, time, hmac, hashlib, requests
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
BASE = "https://api.bybit.eu"

print(f"KEY LOADED: {API_KEY[:4]}**** len={len(API_KEY)} secret_len={len(API_SECRET)}", flush=True)

def bybit_get(path, params):
    params = dict(params)
    params["api_key"] = API_KEY
    params["timestamp"] = str(int(time.time() * 1000))
    params["recv_window"] = "5000"
    # sorteren voor signature
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sign = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE}{path}?{query}&sign={sign}"
    r = requests.get(url, timeout=10)
    try:
        return r.status_code, r.json()
    except:
        return r.status_code, {"retCode": -1, "retMsg": "empty", "body": r.text[:200]}

def get_balances():
    for account in ["SPOT", "UNIFIED"]:
        status, data = bybit_get("/v5/account/wallet-balance", {"accountType": account})
        print(f"Saldo check {account}: http={status} retCode={data.get('retCode')} retMsg={data.get('retMsg')}", flush=True)
        if data.get("retCode") == 0:
            coins = {}
            for lst in data.get("result", {}).get("list", []):
                for c in lst.get("coin", []):
                    coins[c["coin"]] = float(c.get("walletBalance", 0))
            return coins, account, data
    return None, None, data

@app.route("/")
def home():
    return "Pepebot live"

@app.route("/test-key")
def test_key():
    coins, account, raw = get_balances()
    if coins is None:
        return jsonify({"key_start": API_KEY[:4] + "****", "status": "ERROR", "usdt": None, "pepe": None, "【entity-bybit¦canonical_name=Bybit】": raw})
    return jsonify({"key_start": API_KEY[:4] + "****", "status": "OK", "account": account, "usdt": coins.get("USDT"), "pepe": coins.get("PEPE")})

@app.route("/run")
def run():
    coins, account, raw = get_balances()
    if coins is None:
        return jsonify({"error": "geen saldo", "bybit": raw}), 401
    return jsonify({"status": "OK", "account": account, "usdt": coins.get("USDT"), "pepe": coins.get("PEPE")})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
