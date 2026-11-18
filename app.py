import os, time, hmac, hashlib, requests, json
from flask import Flask

app = Flask(__name__)

def clean(v):
    return "".join(str(v or "").split()).strip().strip('"').strip("'")

API_KEY = clean(os.getenv("BYBIT_API_KEY", ""))
API_SECRET = clean(os.getenv("BYBIT_API_SECRET", ""))
BASE = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
QTY_USDT = 20.0
FACTOR = 4.0
ATR_PERIOD = 14

print(f"KEY_LEN={len(API_KEY)} SECRET_LEN={len(API_SECRET)}", flush=True)

def headers_for(q):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    s = ts + API_KEY + recv + q
    sig = hmac.new(API_SECRET.encode(), s.encode(), hashlib.sha256).hexdigest()
    return {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "X-BAPI-SIGN": sig, "X-BAPI-SIGN-TYPE": "2", "Content-Type": "application/json"}

def get_bal(coin):
    q = f"accountType=SPOT&coin={coin}"
    r = requests.get(BASE + "/v5/account/wallet-balance?" + q, headers=headers_for(q), timeout=10)
    print(f"BAL_RAW {r.status_code} {r.text[:200]}", flush=True)
    j = r.json()
    if j.get("retCode") != 0:
        return 0.0
    for c in j["result"]["list"][0]["coin"]:
        if c["coin"] == coin:
            return float(c["walletBalance"])
    return 0.0

def get_klines():
    r = requests.get("https://api.bybit.com/v5/market/kline", params={"category": "spot", "symbol": SYMBOL, "interval": "15", "limit": 100}, timeout=10)
    return r.json()["result"]["list"][::-1]

def utbot(kl):
    closes = [float(k[4]) for k in kl]
    highs = [float(k[2]) for k in kl]
    lows = [float(k[3]) for k in kl]
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr = sum(trs[-ATR_PERIOD:]) / ATR_PERIOD
    trailing = None
    direction = 1
    for i in range(len(closes)):
        if i < ATR_PERIOD:
            continue
        atr_i = sum(trs[max(0, i-ATR_PERIOD):i]) / ATR_PERIOD
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
    body = {"category": "spot", "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty), "timeInForce": "GTC"}
    body_str = json.dumps(body)
    ts = str(int(time.time() * 1000))
    recv = "5000"
    s = ts + API_KEY + recv + body_str
    sig = hmac.new(API_SECRET.encode(), s.encode(), hashlib.sha256).hexdigest()
    h = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "X-BAPI-SIGN": sig, "X-BAPI-SIGN-TYPE": "2", "Content-Type": "application/json"}
    r = requests.post(BASE + "/v5/order/create", headers=h, data=body_str, timeout=10)
    print(f"ORDER {side} {qty} -> {r.status_code} {r.text[:300]}", flush=True)
    return r.json()

@app.route("/")
def home():
    try:
        bal_usdt = get_bal("USDT")
        bal_pepe = get_bal("PEPE")
        kl = get_klines()
        sig, price, atr = utbot(kl)
        print(f"BAL_USDT={bal_usdt} BAL_PEPE={bal_pepe} SIG={sig} PRICE={price} ATR={atr}", flush=True)
        if sig == "BUY" and bal_usdt >= QTY_USDT and bal_pepe == 0:
            place_order("Buy", QTY_USDT, price)
            return f"BUY geplaatst BAL={bal_usdt} PRICE={price}"
        if sig == "SELL" and bal_pepe > 0:
            place_order("Sell", bal_pepe * price, price)
            return f"SELL geplaatst BAL_PEPE={bal_pepe} PRICE={price}"
        return f"BAL_USDT={bal_usdt} BAL_PEPE={bal_pepe} SIG={sig} PRICE={price} ATR={atr}"
    except Exception as e:
        print(f"ERR {e}", flush=True)
        return f"ERR {e}"

@app.route("/health")
def health():
    return "ok"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
