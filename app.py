import os, time, hmac, hashlib, requests
from flask import Flask

app = Flask(__name__)

API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
BASE = "https://api.bybit.eu"  # EU ONLY, geen .com
SYMBOL = "PEPEUSDT"
UTBOT_FACTOR = 4.0
UTBOT_ATR = 14
INTERVAL = "15"

def sign(params):
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()

def auth_get(path, extra={}):
    ts = str(int(time.time()*1000))
    p = {"api_key": API_KEY, "timestamp": ts, "recv_window": "5000", **extra}
    p["sign"] = sign(p)
    r = requests.get(BASE + path, params=p, timeout=15)
    return r.json()

def auth_post(path, extra={}):
    ts = str(int(time.time()*1000))
    p = {"api_key": API_KEY, "timestamp": ts, "recv_window": "5000", **extra}
    p["sign"] = sign(p)
    r = requests.post(BASE + path, data=p, timeout=15)
    return r.json()

def get_balance():
    d = auth_get("/v5/account/wallet-balance", {"accountType": "SPOT", "coin": "USDT"})
    print("BAL SPOT USDT ->", d, flush=True)
    try:
        return float(d["result"]["list"][0]["coin"][0]["walletBalance"])
    except Exception:
        return 0.0

def get_klines():
    r = requests.get(BASE + "/v5/market/kline", params={"category": "spot", "symbol": SYMBOL, "interval": INTERVAL, "limit": "100"}, timeout=15).json()
    closes = [float(x[4]) for x in r["result"]["list"]][::-1]
    return closes

def utbot_signal(closes):
    # UTBOT 4 / 14 - simpele ATR trailing versie
    import statistics
    if len(closes) < 30:
        return "HOLD"
    trs = [abs(closes[i]-closes[i-1]) for i in range(1, len(closes))]
    atr = sum(trs[-UTBOT_ATR:]) / UTBOT_ATR
    # trailing stop: prijs boven vorig slot + factor*atr = BUY, eronder = SELL
    if closes[-1] > closes[-2] + UTBOT_FACTOR * atr * 0.1:
        return "BUY"
    if closes[-1] < closes[-2] - UTBOT_FACTOR * atr * 0.1:
        return "SELL"
    return "HOLD"

@app.route("/")
def home():
    bal = get_balance()
    closes = get_klines()
    sig = utbot_signal(closes)
    print(f"BAL={bal} SIG={sig} PRICE={closes[-1]}", flush=True)
    if bal > 5 and sig == "BUY":
        # compound: volledige USDT balans gebruiken
        price = closes[-1]
        qty = str(int((bal * 0.99) / price))
        res = auth_post("/v5/order/create", {"category": "spot", "symbol": SYMBOL, "side": "Buy", "orderType": "Market", "qty": qty})
        print("BUY ->", res, flush=True)
        return f"BUY {qty} BAL={bal}"
    if sig == "SELL":
        # verkoop alle PEPE
        b = auth_get("/v5/account/wallet-balance", {"accountType": "SPOT", "coin": "PEPE"})
        try:
            pepe = float(b["result"]["list"][0]["coin"][0]["walletBalance"])
        except Exception:
            pepe = 0
        if pepe > 0:
            res = auth_post("/v5/order/create", {"category": "spot", "symbol": SYMBOL, "side": "Sell", "orderType": "Market", "qty": str(int(pepe))})
            print("SELL ->", res, flush=True)
            return f"SELL {pepe}"
    return f"OK BAL={bal} SIG={sig} PRICE={closes[-1]}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
