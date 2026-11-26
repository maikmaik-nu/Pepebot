import os, time, math, hmac, hashlib, threading, requests
from flask import Flask, jsonify
import pandas as pd
import numpy as np

app = Flask(__name__)
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE_URL = "https://api.bybit.eu"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SYMBOL = "PEPEUSDC"
TRADE_USDC = 10.0
bought_price = 0
bought_qty = 0

def log(m): print(m, flush=True)
def sign(p, t):
    q = "&".join(f"{k}={v}" for k, v in sorted(p.items()))
    return hmac.new(API_SECRET.encode(), f"{t}{API_KEY}5000{q}".encode(), hashlib.sha256).hexdigest()
def get(path, params={}):
    t = str(int(time.time()*1000))
    h = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sign(params, t), "X-BAPI-TIMESTAMP": t, "X-BAPI-RECV-WINDOW": "5000"}
    return requests.get(BASE_URL+path, params=params, headers=h, timeout=10).json()
def post(path, body={}):
    t = str(int(time.time()*1000))
    h = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sign(body, t), "X-BAPI-TIMESTAMP": t, "X-BAPI-RECV-WINDOW": "5000", "Content-Type": "application/json"}
    return requests.post(BASE_URL+path, json=body, headers=h, timeout=10).json()
def tg(m):
    if TELEGRAM_TOKEN and TELEGRAM_CHAT_ID:
        try: requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": m}, timeout=10)
        except: pass

def balance():
    for acc in ["UNIFIED", "SPOT"]:
        try:
            d = get("/v5/account/wallet-balance", {"accountType": acc})
            coins = d.get("result", {}).get("list", [{}])[0].get("coin", [])
            b = {c["coin"]: float(c.get("walletBalance", 0) or 0) for c in coins}
            if b.get("USDC", 0) > 0 or b.get("PEPE", 0) > 0: return b
        except Exception as e: log(f"BALANCE FAIL {e}")
    return {"USDC": 0, "PEPE": 0}

def candles():
    d = get("/v5/market/kline", {"category": "spot", "symbol": SYMBOL, "interval": "15", "limit": 200})
    lst = sorted(d.get("result", {}).get("list", []), key=lambda x: int(x[0]))
    df = pd.DataFrame(lst, columns=["time","open","high","low","close","vol","turnover"])
    for c in ["open","high","low","close"]: df[c] = df[c].astype(float)
    return df

def signal(df):
    df["tr"] = np.maximum(df["high"]-df["low"], np.maximum(abs(df["high"]-df["close"].shift(1)), abs(df["low"]-df["close"].shift(1))))
    df["atr"] = df["tr"].rolling(14).mean()
    df["stop"] = 0.0
    for i in range(1, len(df)):
        p = df.iloc[i-1]; c = df.iloc[i]["close"]; a = df.iloc[i]["atr"] or 0
        if c > p["stop"] and p["close"] > p["stop"]: df.loc[df.index[i],"stop"] = max(p["stop"], c-a)
        elif c < p["stop"] and p["close"] < p["stop"]: df.loc[df.index[i],"stop"] = min(p["stop"], c+a)
        elif c > p["stop"]: df.loc[df.index[i],"stop"] = c-a
        else: df.loc[df.index[i],"stop"] = c+a
    l = df.iloc[-1]; p = df.iloc[-2]
    ut = l["close"] > l["stop"] and p["close"] <= p["stop"]
    st = l["close"] > (l["close"] - 3*l["atr"])
    return ut, st, float(l["close"])

def place_buy(price):
    global bought_price, bought_qty
    total = math.floor((TRADE_USDC/price)/1000)*1000
    if total <= 0: return {}
    done = 0; res = {}
    while done < total:
        q = math.floor(min(2500000, total-done)/1000)*1000
        if q <= 0: break
        res = post("/v5/order/create", {"category":"spot","symbol":SYMBOL,"side":"Buy","orderType":"Market","qty":str(int(q))})
        log(f"ORDER Buy qty {q} chunk {done+q}/{total} -> {res}")
        if res.get("retCode") != 0: break
        done += q
        if bought_price == 0: bought_price = price
        bought_qty = done
        time.sleep(1)
    return res

def check():
    try:
        b = balance(); usdc = b.get("USDC",0); pepe = b.get("PEPE",0)
        df = candles(); ut, st, prijs = signal(df)
        log(f"CHECK: USDC={usdc} PEPE={pepe} PRICE={prijs} UTBOT={'buy' if ut else 'sell'}")
        if ut and st and usdc >= TRADE_USDC and pepe == 0:
            r = place_buy(prijs)
            tg(f"PEPE GEKOCHT: {bought_qty} voor {TRADE_USDC} USDC @ {prijs}" if r.get("retCode")==0 else f"BUY AFGEWEZEN: {r}")
            return {"status":"bought","result":r}
        return {"status":"no-trade"}
    except Exception as e:
        log(f"CHECK FAIL {e}"); return {"status":"fail","error":str(e)}

def loop():
    while True: check(); time.sleep(60)

@app.route("/")
def home(): return "Pepebot-3 live"
@app.route("/check")
def c(): return jsonify(check())
@app.route("/test-key")
def t(): return jsonify(get("/v5/account/wallet-balance", {"accountType":"UNIFIED"}))

threading.Thread(target=loop, daemon=True).start()
if __name__ == "__main__": app.run(host="0.0.0.0", port=int(os.getenv("PORT",10000)))
