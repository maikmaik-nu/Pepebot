import os, time, hmac, hashlib, requests
from flask import Flask

app = Flask(__name__)

def clean(v):
    if not v:
        return ""
    return "".join(str(v).split()).strip().strip('"').strip("'")

API_KEY = clean(os.getenv("BYBIT_API_KEY", ""))
API_SECRET = clean(os.getenv("BYBIT_API_SECRET", ""))
BASE = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
INTERVAL = "15"
QTY_USDT = 20.0
FACTOR = 4.0
ATR_PERIOD = 14

print(f"KEY_START={API_KEY[:4]} KEY_LEN={len(API_KEY)} SECRET_LEN={len(API_SECRET)}", flush=True)

def sign_get(path, query):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    s = ts + API_KEY + recv + query
    sig = hmac.new(API_SECRET.encode(), s.encode(), hashlib.sha256).hexdigest()
    h = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sig,
        "X-BAPI-SIGN-TYPE": "2",
        "Content-Type": "application/json"
    }
    return h

def sign_post(path, body_str):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    s = ts + API_KEY + recv + body_str
    sig = hmac.new(API_SECRET.encode(), s.encode(), hashlib.sha256).hexdigest()
    h = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sig,
        "X-BAPI-SIGN-TYPE": "2",
        "Content-Type": "application/json"
    }
    return h

def get_balance_usdt():
    q = "accountType=UNIFIED&coin=USDT"
    try:
        r = requests.get(BASE + "/v5/account/wallet-balance?" + q, headers=sign_get("/v5/account/wallet-balance", q), timeout=10)
        print(f"BAL_RAW {r.status_code} {r.text[:300]}", flush=True)
        j = r.json()
        if j.get("retCode") != 0:
            return 0.0
        for coin in j["result"]["list"][0]["coin"]:
            if coin["coin"] == "USDT":
                return float(coin["walletBalance"])
        return 0.0
    except Exception as e:
        print(f"BAL_ERR {e}", flush=True)
        return 0.0

def get_balance_pepe():
    q = "accountType=UNIFIED&coin=PEPE"
    try:
        r = requests.get(BASE + "/v5/account/wallet-balance?" + q, headers=sign_get("/v5/account/wallet-balance", q), timeout=10)
        j = r.json()
        if j.get("retCode") != 0:
            return 0.0
        for coin in j["result"]["list"][0]["coin"]:
            if coin["coin"] == "PEPE":
                return float(coin["walletBalance"])
        return 0.0
    except:
        return 0.0

def get_klines():
    try:
        r = requests.get("https://api.bybit.com/v5/market/kline", params={"category": CATEGORY, "symbol": SYMBOL, "interval": INTERVAL, "limit": 100}, timeout=10)
        lst = r.json()["result"]["list"]
        lst = lst[::-1]
        return lst
    except Exception as e:
        print(f"KLINE_ERR {e}", flush=True)
        return []

def utbot_signal(klines):
    if len(klines) < ATR_PERIOD + 5:
        return "HOLD", 0.0, 0.0
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    atr = sum(trs[-ATR_PERIOD:]) / ATR_PERIOD
    # UTBOT trailing
    trailing = None
    direction = 1
    for i in range(len(closes)):
        if i < ATR_PERIOD:
            continue
        src = closes[i]
        atr_i = sum(trs[max(0, i-ATR_PERIOD):i]) / ATR_PERIOD if i >= ATR_PERIOD else atr
        upper = src + FACTOR * atr_i
        lower = src - FACTOR * atr_i
        if trailing is None:
            trailing = lower
            direction = 1
        if direction == 1:
            trailing = max(trailing, lower)
            if src < trailing:
                direction = -1
                trailing = upper
        else:
            trailing = min(trailing, upper)
            if src > trailing:
                direction = 1
                trailing = lower
    price = closes[-1]
    prev_close = closes[-2]
    # Signaal: crossover met trailing
    if direction == 1 and prev_close <= trailing:
        return "BUY", price, atr
    if direction == -1 and prev_close >= trailing:
        return "SELL", price, atr
    if direction == 1:
        return "BUY_HOLD", price, atr
    return "SELL_HOLD", price, atr

def place_order(side, qty_usdt, price):
    qty = int(qty_usdt / price)
    if qty <= 0:
        print("QTY_TOO_SMALL", flush=True)
        return None
    import json
    body = {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": side,
        "orderType": "Market",
        "qty": str(qty),
        "timeInForce": "GTC"
    }
    body_str = json.dumps(body)
    try:
        r = requests.post(BASE + "/v5/order/create", headers=sign_post("/v5/order/create", body_str), data=body_str, timeout=10)
        print(f"ORDER {side} {qty} -> {r.status_code} {r.text[:400]}", flush=True)
        return r.json()
    except Exception as e:
        print(f"ORDER_ERR {e}", flush=True)
        return None

@app.route("/")
def home():
    bal_usdt = get_balance_usdt()
    bal_pepe = get_balance_pepe()
    kl = get_klines()
    sig, price, atr = utbot_signal(kl)
    print(f"BAL_USDT={bal_usdt} BAL_PEPE={bal_pepe} SIG={sig} PRICE={price} ATR={atr}", flush=True)
    if bal_usdt == 0.0 and bal_pepe == 0.0:
        return f"BAL=0 SIG=HOLD PRICE={price} - geen saldo gevonden"
    # Alleen traden als er saldo is
    if sig == "BUY" and bal_usdt >= QTY_USDT and bal_pepe == 0.0:
        place_order("Buy", QTY_USDT, price)
        return f"BUY geplaatst BAL={bal_usdt} PRICE={price}"
    if sig == "SELL" and bal_pepe > 0:
        # verkoop alles in USDT waarde
        qty_usdt = bal_pepe * price
        place_order("Sell", qty_usdt, price)
        return f"SELL geplaatst BAL_PEPE={bal_pepe} PRICE={price}"
    return f"BAL_USDT={bal_usdt} BAL_PEPE={bal_pepe} SIG={sig} PRICE={price} ATR={atr} - geen actie"

@app.route("/health")
def health():
    return "ok"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
