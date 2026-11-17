import os, time, hmac, hashlib, requests
from flask import Flask, jsonify

app = Flask(__name__)
KEY = os.getenv("BYBIT_API_KEY", "")
SECRET = os.getenv("BYBIT_API_SECRET", "")

def call(base, acct):
    ts = str(int(time.time()*1000))
    qs = f"accountType={acct}&coin=PEPE,USDT"
    sig = hmac.new(SECRET.encode(), (ts+KEY+"5000"+qs).encode(), hashlib.sha256).hexdigest()
    r = requests.get(f"{base}/v5/account/wallet-balance?{qs}", headers={
        "X-BAPI-API-KEY": KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000",
        "X-BAPI-SIGN": sig}, timeout=10)
    return r.json()

def saldo():
    if not KEY or not SECRET:
        return {"status": "ERROR", "pepe": None, "usdt": None, "error": "Geen key in Render"}
    for base in ["https://api.bytick.com", "https://api.bybit.eu"]:
        for acct in ["UNIFIED", "SPOT"]:
            try:
                d = call(base, acct)
                if d.get("retCode") == 0:
                    coins = {c["coin"]: c["walletBalance"] for c in d["result"]["list"][0]["coin"]}
                    return {"status": "OK", "pepe": coins.get("PEPE"), "usdt": coins.get("USDT"), "via": f"{base}/{acct}"}
            except Exception:
                pass
    return {"status": "ERROR", "pepe": None, "usdt": None, "error": "10003 - key heeft geen toestemming op bytick/bybit.eu, maak nieuwe Live key met Read + Unified + Spot + Wallet"}

@app.route("/")
@app.route("/saldo")
@app.route("/balance")
@app.route("/status")
@app.route("/check")
def index():
    return jsonify(saldo())

@app.route("/health")
def health():
    return jsonify({"status": "LIVE"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
