import os, time, hmac, hashlib, requests, threading
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
TIMEFRAME = "15"
USE_USDT = 95  # hou over voor fees, niet je hele 100.11
DRY_RUN = True  # zet op False als je echt wil traden

# ---------- 【entity-Bybit¦canonical_name=Bybit】 sign ----------
def sign(params):
    timestamp = str(int(time.time() * 1000))
    recv = "5000"
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    to_sign = timestamp + API_KEY + recv + query
    signature = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": signature,
        "Content-Type": "application/json"
    }

def get_balance():
    params = {"accountType": "SPOT", "coin": "USDT"}
    r = requests.get(f"{BASE_URL}/v5/account/wallet-balance", params=params, headers=sign(params), timeout=10)
    data = r.json()
    if data.get("retCode") != 0:
        return 0.0, 0.0, data
    coins = data["result"]["list"][0]["coin"]
    usdt = pepe = 0.0
    for c in coins:
        if c["coin"] == "USDT":
            usdt = float(c["walletBalance"])
        if c["coin"] == "PEPE":
            pepe = float(c["walletBalance"])
    return usdt, pepe, data

def get_price():
    params = {"category": CATEGORY, "symbol": SYMBOL}
    r = requests.get(f"{BASE_URL}/v5/market/tickers", params=params, timeout=10)
    data = r.json()
    return float(data["result"]["list"][0]["lastPrice"])

def get_klines():
    params = {"category": CATEGORY, "symbol": SYMBOL, "interval": TIMEFRAME, "limit": 200}
    r = requests.get(f"{BASE_URL}/v5/market/kline", params=params, timeout=10)
    data = r.json()
    # [start, open, high, low, close, volume, turnover]
    closes = [float(x[4]) for x in reversed(data["result"]["list"])]
    highs = [float(x[2]) for x in reversed(data["result"]["list"])]
    lows = [float(x[3]) for x in reversed(data["result"]["list"])]
    return closes, highs, lows

# ---------- Supertrend 10,3 + UT Bot 4,14 ----------
def supertrend(closes, highs, lows, period=10, mult=3):
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr = sum(trs[-period:]) / period
    hl2 = (highs[-1] + lows[-1]) / 2
    upper = hl2 + mult * atr
    lower = hl2 - mult * atr
    return "green" if closes[-1] > lower else "red"

def ut_bot(closes, period=4, mult=14):
    trs = []
    for i in range(1, len(closes)):
        trs.append(abs(closes[i]-closes[i-1]))
    atr = sum(trs[-period:]) / period if trs else 0
    if len(closes) < 2:
        return "sell"
    return "buy" if closes[-1] > closes[-2] else "sell"

def check_signal():
    closes, highs, lows = get_klines()
    st = supertrend(closes, highs, lows, 10, 3)
    ut = ut_bot(closes, 4, 14)
    buy = (st == "green" and ut == "buy")
    sell = (st == "red")
    return buy, sell, st, ut, closes[-1]

# ---------- Order ----------
def place_order(side, qty):
    if DRY_RUN:
        print(f"DRY RUN {side} {qty} PEPE")
        return {"dry_run": True, "side": side, "qty": qty}
    params = {"category": CATEGORY, "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty)}
    r = requests.post(f"{BASE_URL}/v5/order/create", json=params, headers=sign(params), timeout=10)
    return r.json()

# ---------- Auto loop ----------
in_position = False

def auto_check():
    global in_position
    while True:
        try:
            usdt, pepe, raw_bal = get_balance()
            price = get_price()
            buy, sell, st, ut, close = check_signal()
            print(f"AUTO CHECK: USDT={usdt} PEPE={pepe} PRICE={price} ST={st} UT={ut} BUY={buy} SELL={sell}")

            if buy and not in_position:
                qty = USE_USDT / price
                print(f"WOULD BUY
