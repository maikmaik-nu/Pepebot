import os, time, hmac, hashlib, json, requests, threading
from flask import Flask, jsonify
app = Flask(__name__)

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
BASE = "https://api.bybit.eu"
SYMBOL = "PEPEUSDC"

def tg(msg):
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
    except: pass

def req(method, path, params=None):
    params = params or {}
    ts = str(int(time.time()*1000))
    if method == "GET":
        q = "&".join([f"{k}={v}" for k,v in params.items()])
        s = hmac.new(API_SECRET.encode(), (ts+API_KEY+"5000"+q).encode(), hashlib.sha256).hexdigest()
        h = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": s}
        return requests.get(BASE+path+("?"+q if q else ""), headers=h, timeout=15).json()
    else:
        b = json.dumps(params)
        s = hmac.new(API_SECRET.encode(), (ts+API_KEY+"5000"+b).encode(), hashlib.sha256).hexdigest()
        h = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": s, "Content-Type": "application/json"}
        return requests.post(BASE+path, headers=h, data=b, timeout=15).json()

def saldo():
    r = req("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
    if r.get("retCode") != 0:
        r = req("GET", "/v5/account/wallet-balance", {"accountType": "SPOT"})
    u = p = 0.0
    try:
        for c in r["result"]["list"][0]["coin"]:
            if c["coin"] == "USDC": u = float(c["walletBalance"])
            if c["coin"] == "PEPE": p = float(c["walletBalance"])
    except: pass
    return u, p

def candles():
    r = req("GET", "/v5/market/kline", {"category": "spot", "symbol": SYMBOL, "interval": "15", "limit": 100})
    if r.get("retCode") != 0: return 0.0, []
    l = list(reversed(r["result"]["list"]))
    closes = [float(x[4]) for x in l]
    return closes[-1], closes

def is_up(closes):
    if len(closes) < 21: return False
    return closes[-1] > sum(closes[-21:-1])/20 and closes[-1] > closes[-2]

def loop():
    gekocht = False
    while True:
        try:
            usdc, pepe = saldo()
            prijs, closes = candles()
            print(f"Check: USDC {usdc:.2f} PEPE {pepe:.0f} prijs {prijs}")
            up = is_up(closes)
            print(f"UP={up} prijs {prijs}")
            if up and not gekocht and usdc >= 10:
                # OPLOSSING: quoteQty in plaats van qty
                r = req("POST", "/v5/order/create", {"category": "spot", "symbol": SYMBOL, "side": "Buy", "orderType": "Market", "quoteQty": "10"})
                print("BUY:", r)
                if r.get("retCode") == 0:
                    tg("PEPE GEKOCHT voor 10 USDC")
                    gekocht = True
                else:
                    tg(f"BUY afgewezen: {r}")
            if not up and gekocht and pepe > 0:
                r = req("POST", "/v5/order/create", {"category": "spot", "symbol": SYMBOL, "side": "Sell", "orderType": "Market", "qty": str(int(pepe))})
                print("SELL:", r)
                if r.get("retCode") == 0:
                    tg("PEPE VERKOCHT")
                    gekocht = False
            if pepe > 0: gekocht = True
            if pepe == 0: gekocht = False
        except Exception as e:
            print("Fout:", e)
        time.sleep(60)

@app.route("/")
def home():
    u, p = saldo()
    pr, _ = candles()
    return f"Live - USDC: {u:.2f} PEPE: {p:.0f} Prijs: {pr}"

@app.route("/debug-key")
def debug():
    return jsonify({"key_len": len(API_KEY), "key_start": API_KEY[:4], "base": BASE, "symbol": SYMBOL})

@app.route("/test-key")
def test():
    return jsonify(req("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"}))

if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
