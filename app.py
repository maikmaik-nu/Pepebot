import os, time, hmac, hashlib, requests, json
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
if len(API_SECRET) < 30:
    print("FOUT: SECRET TE KORT - plak de lange Bybit secret in Render", flush=True)

def headers_for(query_or_body):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    s = ts + API_KEY + recv + query_or_body
    sig = hmac.new(API_SECRET.encode(), s.encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sig,
        "X-BAPI-SIGN-TYPE": "2",
        "Content-Type": "application/json"
    }

def get_balance(coin):
    q = f"accountType=UNIFIED&coin={coin}"
    try:
        r = requests.get(BASE + "/v5/account/wallet-balance?" + q, headers=headers_for(q), timeout=10)
        print(f"BAL_RAW {r.status_code} {r.text[:200]}", flush=True)
        j = r.json()
        if j.get("retCode") != 0:
            return 0.0
        for c in j["result"]["list"][0]["coin"]:
            if c["coin"] == coin:
                return float(c["walletBalance"])
        return 0.0
    except Exception as e:
        print(f"BAL_ERR {e}", flush=True)
        return 0.0

def get_klines():
    try:
        r = requests.get("https://api.bybit.com/v5/market/kline", params={"category": CATEGORY, "symbol": SYMBOL, "interval": INTERVAL, "limit": 100}, timeout=10)
        return r.json()["result"]["list"][::-1]
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
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr = sum(trs[-ATR_PERIOD:]) / ATR_PERIOD
    trailing = None
    direction = 1
    for i in range(len(closes)):
        if i < ATR_PERIOD:
            continue
        atr_i = sum(trs[max(0, i-ATR_PERIOD):i]) / ATR_PERIOD if i >= ATR_PERIOD else atr
        upper = closes[i] + FACTOR * atr_i
        lower = closes[i] - FACTOR * atr_i
        if trailing is None:
            trailing = lower
            direction = 1
        if direction == 1:
            trailing = max(trailing, lower)
            if closes[i] < trailing:
                direction = -1
                trailing = upper
        else:
            trailing = min(trailing, upper)
            if closes[i] > trailing:
                direction = 1
                trailing = lower
    price = closes[-1]
    prev = closes[-2]
    if direction == 1 and prev <= trailing:
        return "BUY", price, atr
    if direction == -1 and prev >= trailing:
        return "SELL", price, atr
    return ("BUY_HOLD" if direction == 1 else "SELL_HOLD"), price, atr

def place_order(side, qty_usdt, price):
    qty = int(qty_usdt / price)
    if qty <= 0:
        print("QTY_TOO_SMALL", flush=True)
        return None
    body = {"category": CATEGORY, "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty), "timeInForce": "GTC"}
    body_str = json.dumps(body)
    try:
        r = requests.post(BASE + "/v5/order/create", headers=headers_for(body_str), data=body_str, timeout=10)
        print(f"ORDER {side} {qty} -> {r.status_code} {r.text[:400]}", flush=True)
        return r.json()
    except Exception as e:
        print(f"ORDER_ERR {e}", flush=True)
        return None

@app.route("/")
def home():
    bal_usdt = get_balance("USDT")
    bal_pepe = get_balance("PEPE")
    kl = get_klines()
    sig, price, atr = utbot_signal(kl)
    print(f"BAL_USDT={bal_usdt} BAL_PEPE={bal_pepe} SIG={sig} PRICE={price} ATR={atr}", flush=True)
    if bal_usdt == 0.0 and bal_pepe == 0.0:
        return f"BAL=0 SIG=HOLD PRICE={price} - check SECRET_LEN in logs"
    if sig == "BUY" and bal_usdt >= QTY_USDT and bal_pepe == 0.0:
        place_order("Buy", QTY_USDT, price)
        return f"BUY geplaatst BAL={bal_usdt} PRICE={price}"
    if sig == "SELL" and bal_pepe > 0:
        place_order("Sell", bal_pepe * price, price)
        return f"SELL geplaatst BAL_PEPE={bal_pepe} PRICE={price}"
    return f"BAL_USDT={bal_usdt} BAL_PEPE={bal_pepe} SIG={sig} PRICE={price} ATR={atr} - geen actie"

@app.route("/health")
def health():
    return "ok"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
