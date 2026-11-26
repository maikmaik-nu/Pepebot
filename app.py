import os, time, hmac, hashlib, threading, requests
from flask import Flask, jsonify
import pandas as pd
import numpy as np

app = Flask(__name__)

API_KEY = os.getenv("【entity-BYBIT_API_KEY¦canonical_name=Bybit】", "")
API_SECRET = os.getenv("【entity-BYBIT_API_SECRET¦canonical_name=Bybit】", "")
BASE_URL = os.getenv("BASE_URL", "https://api.bybit.eu")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
SYMBOL = "PEPEUSDC"
TRADE_USDC = 10.0

def sign(params, timestamp):
    qs = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    raw = f"{timestamp}{API_KEY}5000{qs}"
    return hmac.new(API_SECRET.encode(), raw.encode(), hashlib.sha256).hexdigest()

def bybit_get(path, params={}):
    ts = str(int(time.time() * 1000))
    sig = sign(params, ts)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000"
    }
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
    return r.json()

def bybit_post(path, body={}):
    ts = str(int(time.time() * 1000))
    sig = sign(body, ts)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000",
        "Content-Type": "application/json"
    }
    r = requests.post(BASE_URL + path, json=body, headers=headers, timeout=10)
    return r.json()

def telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print("TELEGRAM FAIL", e)

def get_balance():
    # Eerst UNIFIED proberen, dan SPOT
    for acc in ["UNIFIED", "SPOT"]:
        try:
            d = bybit_get("/v5/account/wallet-balance", {"accountType": acc})
            coins = d.get("result", {}).get("list", [{}])[0].get("coin", [])
            bal = {c["coin"]: float(c.get("walletBalance", 0) or 0) for c in coins}
            if bal.get("USDC", 0) > 0 or bal.get("PEPE", 0) > 0:
                return bal
        except Exception as e:
            print(f"BALANCE FAIL {acc}", e)
    return {"USDC": 0, "PEPE": 0, "USDT": 0}

def get_qty_step():
    # FIX voor 'qtyStep' - crasht nooit meer
    try:
        d = bybit_get("/v5/market/instruments-info", {"category": "spot", "symbol": SYMBOL})
        lst = d.get("result", {}).get("list", [])
        if lst:
            lot = lst[0].get("lotSizeFilter", {}) or {}
            step = lot.get("qtyStep") or lot.get("basePrecision") or 1
            return float(step)
    except Exception as e:
        print("QTYSTEP FAIL", e)
    return 1

def get_candles():
    d = bybit_get("/v5/market/kline", {"category": "spot", "symbol": SYMBOL, "interval": "15", "limit": 200})
    lst = d.get("result", {}).get("list", [])
    lst = sorted(lst, key=lambda x: int(x[0]))
    df = pd.DataFrame(lst, columns=["time", "open", "high", "low", "close", "vol", "turnover"])
    for c in ["open", "high", "low", "close"]:
        df[c] = df[c].astype(float)
    return df

def calc_signals(df):
    # ATR 14
    df["tr"] = np.maximum(df["high"] - df["low"], np.maximum(abs(df["high"] - df["close"].shift(1)), abs(df["low"] - df["close"].shift(1))))
    df["atr"] = df["tr"].rolling(14).mean()
    # UT Bot - simpel: close boven / onder trailing stop
    n_loss = 1.0
    df["xATRTrailingStop"] = 0.0
    for i in range(1, len(df)):
        prev = df.iloc[i-1]
        cur_close = df.iloc[i]["close"]
        atr = df.iloc[i]["atr"] or 0
        if cur_close > prev["xATRTrailingStop"] and prev["close"] > prev["xATRTrailingStop"]:
            df.loc[df.index[i], "xATRTrailingStop"] = max(prev["xATRTrailingStop"], cur_close - n_loss * atr)
        elif cur_close < prev["xATRTrailingStop"] and prev["close"] < prev["xATRTrailingStop"]:
            df.loc[df.index[i], "xATRTrailingStop"] = min(prev["xATRTrailingStop"], cur_close + n_loss * atr)
        elif cur_close > prev["xATRTrailingStop"]:
            df.loc[df.index[i], "xATRTrailingStop"] = cur_close - n_loss * atr
        else:
            df.loc[df.index[i], "xATRTrailingStop"] = cur_close + n_loss * atr
    last = df.iloc[-1]
    prev = df.iloc[-2]
    ut_buy = last["close"] > last["xATRTrailingStop"] and prev["close"] <= prev["xATRTrailingStop"]
    ut_sell = last["close"] < last["xATRTrailingStop"] and prev["close"] >= prev["xATRTrailingStop"]
    # Supertrend 10,3 - simpel: close boven ATR-band = up
    st_up = last["close"] > (last["close"] - 3 * last["atr"])
    return ut_buy, ut_sell, st_up, float(last["close"])

def check_once():
    try:
        bal = get_balance()
        usdc = bal.get("USDC", 0)
        pepe = bal.get("PEPE", 0)
        df = get_candles()
        ut_buy, ut_sell, st_up, prijs = calc_signals(df)
        print(f"CHECK: USDC={usdc} PEPE={pepe} PRICE={prijs} UT_BUY={ut_buy} ST_UP={st_up}")
        if ut_buy and st_up and usdc >= TRADE_USDC and pepe == 0:
            qty_step = get_qty_step()
            qty = int(TRADE_USDC / prijs / qty_step) * qty_step
            qty = int(qty)
            print(f"ORDER Buy {SYMBOL} qty {qty} voor {TRADE_USDC} USDC @ {prijs}")
            res = bybit_post("/v5/order/create", {
                "category": "spot", "symbol": SYMBOL, "side": "Buy",
                "orderType": "Market", "qty": str(qty)
            })
            print("BUY RESULT", res)
            if res.get("retCode") == 0:
                telegram(f"PEPE GEKOCHT: {qty} voor {TRADE_USDC} USDC @ {prijs}")
            else:
                telegram(f"BUY AFGEWEZEN: {res}")
            return {"status": "bought", "qty": qty, "result": res}
        return {"status": "no-trade", "usdc": usdc, "prijs": prijs, "ut_buy": ut_buy, "st_up": st_up}
    except Exception as e:
        print("CHECK FAIL", e)
        return {"status": "fail", "error": str(e)}

def loop():
    while True:
        check_once()
        time.sleep(60)

@app.route("/")
def home():
    return "Pepebot-3 live"

@app.route("/debug-key")
def debug_key():
    return jsonify({"key_len": len(API_KEY), "key_start": API_KEY[:4] if API_KEY else "", "base_url": BASE_URL})

@app.route("/test-key")
def test_key():
    return jsonify(bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"}))

@app.route("/check")
def check():
    return jsonify(check_once())

threading.Thread(target=loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
