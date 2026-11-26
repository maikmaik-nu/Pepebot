import os, time, math, hmac, hashlib, threading, requests
from flask import Flask, jsonify
app = Flask(__name__)
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE_URL = "https://api.bybit.eu"
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SYMBOL = "PEPEUSDC"
TRADE_USDC = 100.0
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
    if len(lst) < 3: return [], [], []
    return [float(x[4]) for x in lst], [float(x[2]) for x in lst], [float(x[3]) for x in lst]

def signal():
    closes, highs, lows = candles()
    if len(closes) < 3: return False, False, 0
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr = sum(trs[-14:]) / 14 if len(trs) >= 14 else 0
    if atr == 0: return False, False, closes[-1]
    stops = []
    stop = 0
    prev_close = closes[0]
    for i in range(1, len(closes)):
        c = closes[i]
        # UT BOT 4 / 14
        if c > stop and prev_close > stop: stop = max(stop, c-4*atr)
        elif c < stop and prev_close < stop: stop = min(stop, c+4*atr)
        elif c > stop: stop = c-4*atr
        else: stop = c+4*atr
        stops.append(stop)
        prev_close = c
    ut_buy = closes[-1] > stops[-1] and closes[-2] <= stops[-2]
    ut_sell = closes[-1] < stops[-1] and closes[-2] >= stops[-2]
    return ut_buy, ut_sell, closes[-1]

def place_buy(price, amount):
    global bought_price, bought_qty
    total = math.floor((amount/price)/1000)*1000
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

def place_sell(price, amount):
    global bought_price, bought_qty
    total = math.floor(amount/1000)*1000
    if total <= 0: return {}
    done = 0; res = {}
    while done < total:
        q = math.floor(min(2500000, total-done)/1000)*1000
        if q <= 0: break
        res = post("/v5/order/create", {"category":"spot","symbol":SYMBOL,"side":"Sell","orderType":"Market","qty":str(int(q))})
        log(f"ORDER Sell qty {q} chunk {done+q}/{total} -> {res}")
        if res.get("retCode") != 0: break
        done += q
        time.sleep(1)
    bought_price = 0; bought_qty = 0
    return res

def check():
    try:
        b = balance(); usdc = b.get("USDC",0); pepe = b.get("PEPE",0)
        ut_buy, ut_sell, prijs = signal()
        log(f"CHECK: USDC={usdc} PEPE={pepe} PRICE={prijs} BUY={ut_buy} SELL={ut_sell}")
        # RENTE-OP-RENTE: tradet met hele USDC-saldo, dus 100 + winst
        if ut_buy and usdc >= 1 and pepe == 0:
            r = place_buy(prijs, usdc)
            tg(f"PEPE GEKOCHT: {bought_qty} voor {usdc} USDC @ {prijs}" if r.get("retCode")==0 else f"BUY AFGEWEZEN: {r}")
            return {"status":"bought","result":r}
        if ut_sell and pepe > 0:
            r = place_sell(prijs, pepe)
            tg(f"PEPE VERKOCHT: {pepe} @ {prijs}" if r.get("retCode")==0 else f"SELL AFGEWEZEN: {r}")
            return {"status":"sold","result":r}
        return {"status":"no-trade"}
    except Exception as e:
        log(f"CHECK FAIL {e}"); return {"status":"fail","error":str(e)}

def loop():
    while True: check(); time.sleep(60)

@app.route("/")
def home(): return "Pepebot-3 live"
@app.route("/health")
def health(): return "OK", 200
@app.route("/check")
def c(): return jsonify(check())
@app.route("/test-key")
def t(): return jsonify(get("/v5/account/wallet-balance", {"accountType":"UNIFIED"}))

threading.Thread(target=loop, daemon=True).start()
if __name__ == "__main__": app.run(host="0.0.0.0", port=int(os.getenv("PORT",10000)))
