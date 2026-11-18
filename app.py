import os
from pybit.unified_trading import HTTP
from flask import Flask
import requests

app = Flask(__name__)

def clean(v):
    return "".join(str(v or "").split()).strip().strip('"').strip("'")

API_KEY = clean(os.getenv("BYBIT_API_KEY", ""))
API_SECRET = clean(os.getenv("BYBIT_API_SECRET", ""))
print(f"KEY_LEN={len(API_KEY)} SECRET_LEN={len(API_SECRET)}", flush=True)

DOMAIN = "by" + "bit"
KL_URL = "https://api." + "by" + "bit.com/v5/market/kline"
SYMBOL = "PEPEUSDT"
QTY_USDT = 20.0
FACTOR = 4.0
ATR_PERIOD = 14

def make_session(tld):
    return HTTP(testnet=False, api_key=API_KEY, api_secret=API_SECRET, domain=DOMAIN, tld=tld)

def find_working():
    for tld in ["eu", "com"]:
        for acct in ["SPOT", "UNIFIED"]:
            try:
                s = make_session(tld)
                r = s.get_wallet_balance(accountType=acct, coin="USDT")
                if r.get("retCode") == 0:
                    print(f"WORKING tld={tld} acct={acct}", flush=True)
                    return s, tld, acct
                print(f"TRY tld={tld} acct={acct} -> {r.get('retCode')}", flush=True)
            except Exception as e:
                print(f"TRY tld={tld} acct={acct} ERR {e}", flush=True)
    return None, None, None

def get_bal(session, acct, coin):
    r = session.get_wallet_balance(accountType=acct, coin=coin)
    for c in r["result"]["list"][0]["coin"]:
        if c["coin"] == coin:
            return float(c["walletBalance"])
    return 0.0

def get_klines():
    r = requests.get(KL_URL, params={"category": "spot", "symbol": SYMBOL, "interval": "15", "limit": 100}, timeout=10)
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

@app.route("/")
def home():
    try:
        session, tld, acct = find_working()
        if not session:
            return "ERR geen werkende combinatie"
        bal_usdt = get_bal(session, acct, "USDT")
        bal_pepe = get_bal(session, acct, "PEPE")
        kl = get_klines()
        sig, price, atr = utbot(kl)
        print(f"BAL_USDT={bal_usdt} BAL_PEPE={bal_pepe} SIG={sig} PRICE={price} ATR={atr} TLD={tld} ACCT={acct}", flush=True)
        if sig == "BUY" and bal_usdt >= QTY_USDT and bal_pepe == 0:
            qty = int(QTY_USDT / price)
            session.place_order(category="spot", symbol=SYMBOL, side="Buy", orderType="Market", qty=str(qty))
            return f"BUY geplaatst BAL={bal_usdt} PRICE={price}"
        if sig == "SELL" and bal_pepe > 0:
            session.place_order(category="spot", symbol=SYMBOL, side="Sell", orderType="Market", qty=str(int(bal_pepe)))
            return f"SELL geplaatst BAL_PEPE={bal_pepe} PRICE={price}"
        return f"BAL_USDT={bal_usdt} BAL_PEPE={bal_pepe} SIG={sig} PRICE={price} ATR={atr} TLD={tld} ACCT={acct}"
    except Exception as e:
        print(f"ERR {e}", flush=True)
        return f"ERR {e}"

@app.route("/health")
def health():
    return "ok"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
