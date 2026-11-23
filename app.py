import os, time, hmac, hashlib, threading, requests, math
from flask import Flask, jsonify
from datetime import datetime

app = Flask(__name__)

# ============ CONFIG ============
BASE_URL = "https://api.bybit.eu"
API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
INTERVAL = "15"  # 15 min candles voor Supertrend + UT Bot
BUY_AMOUNT_USDT = 15.0
SELL_PERCENT = 100  # verkoop 100% van positie bij sell signaal
CHECK_EVERY = 60

# Supertrend settings
ATR_PERIOD = 10
ATR_MULT = 3.0
# UT Bot settings
UT_KEY = 1.0
UT_ATR_PERIOD = 10

state = {
    "prijs": 0.0, "usdt": 0.0, "pepe": 0.0,
    "signaal": "NONE", "positie": "GEEN",
    "entry_prijs": 0.0, "winst_pct": 0.0,
    "supertrend": "NONE", "utbot": "NONE",
    "last_trade": "nog geen", "last_error": "",
    "laatste_check": ""
}

# ============ TELEGRAM ============
def telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print("TELEGRAM FAIL:", e)

# ============ 【entity-BYBIT¦canonical_name=BYBIT】 SIGN ============
def sign_headers(query_or_body):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    to_sign = ts + API_KEY + recv + query_or_body
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sig,
        "Content-Type": "application/json"
    }

def get_prijs():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers?category={CATEGORY}&symbol={SYMBOL}", timeout=10)
        data = r.json()
        return float(data["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print("PRIJS FAIL:", e)
        return 0.0

def get_spot_balance(coin):
    try:
        query = f"accountType=SPOT&coin={coin}"
        headers = sign_headers(query)
        r = requests.get(f"{BASE_URL}/v5/account/wallet-balance?{query}", headers=headers, timeout=10)
        data = r.json()
        if data.get("retCode") != 0:
            print(f"Saldo fout {coin} SPOT:", data)
            state["last_error"] = str(data)
            return 0.0
        coins = data["result"]["list"][0]["coin"]
        for c in coins:
            if c["coin"] == coin:
                return float(c["walletBalance"])
        return 0.0
    except Exception as e:
        print(f"Saldo fout {coin}:", e)
        return 0.0

def get_klines(limit=200):
    try:
        r = requests.get(f"{BASE_URL}/v5/market/kline?category={CATEGORY}&symbol={SYMBOL}&interval={INTERVAL}&limit={limit}", timeout=10)
        data = r.json()
        klines = data["result"]["list"]
        # Bybit geeft nieuwste eerst, draai om naar oud -> nieuw
        klines = list(reversed(klines))
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        return closes, highs, lows
    except Exception as e:
        print("KLINES FAIL:", e)
        return [], [], []

# ============ INDICATORS ============
def calc_atr(highs, lows, closes, period):
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    if len(trs) < period:
        return [0]*len(closes)
    atr = [sum(trs[:period])/period]
    for i in range(period, len(trs)):
        atr.append((atr[-1]*(period-1) + trs[i]) / period)
    return [0]*(len(closes)-len(atr)) + atr

def calc_supertrend(highs, lows, closes, atr_period=10, mult=3.0):
    atr = calc_atr(highs, lows, closes, atr_period)
    n = len(closes)
    upper = [0]*n
    lower = [0]*n
    trend = [1]*n  # 1 = up, -1 = down
    st_line = [0]*n
    for i in range(1, n):
        hl2 = (highs[i]+lows[i])/2
        upper[i] = hl2 + mult*atr[i]
        lower[i] = hl2 - mult*atr[i]
        if closes[i] > upper[i-1]:
            trend[i] = 1
        elif closes[i] < lower[i-1]:
            trend[i] = -1
        else:
            trend[i] = trend[i-1]
            if trend[i] == 1 and lower[i] < lower[i-1]:
                lower[i] = lower[i-1]
            if trend[i] == -1 and upper[i] > upper[i-1]:
                upper[i] = upper[i-1]
        st_line[i] = lower[i] if trend[i]==1 else upper[i]
    return trend, st_line

def calc_utbot(closes, highs, lows, key=1.0, atr_period=10):
    atr = calc_atr(highs, lows, closes, atr_period)
    n = len(closes)
    # trailing stop zoals UT Bot
    xATRTrailingStop = [0]*n
    for i in range(1, n):
        if closes[i] > xATRTrailingStop[i-1] and closes[i-1] > xATRTrailingStop[i-1]:
            xATRTrailingStop[i] = max(xATRTrailingStop[i-1], closes[i] - key*atr[i])
        elif closes[i] < xATRTrailingStop[i-1] and closes[i-1] < xATRTrailingStop[i-1]:
            xATRTrailingStop[i] = min(xATRTrailingStop[i-1], closes[i] + key*atr[i])
        else:
            xATRTrailingStop[i] = closes[i] - key*atr[i] if closes[i] > xATRTrailingStop[i-1] else closes[i] + key*atr[i]
    # signalen
    signals = ["NONE"]*n
    pos = 0
    for i in range(1, n):
        if closes[i-1] < xATRTrailingStop[i-1] and closes[i] > xATRTrailingStop[i]:
            signals[i] = "BUY"
            pos = 1
        elif closes[i-1] > xATRTrailingStop[i-1] and closes[i] < xATRTrailingStop[i]:
            signals[i] = "SELL"
            pos = -1
        else:
            signals[i] = "BUY" if pos==1 else ("SELL" if pos==-1 else "NONE")
    return signals, xATRTrailingStop

# ============ ORDERS ============
def create_order(side, qty):
    try:
        # qty moet integer zijn voor PEPE, geen decimalen
        qty = int(float(qty))
        if qty <= 0:
            print("Qty te klein")
            return False
        body = f'{{"category":"{CATEGORY}","symbol":"{SYMBOL}","side":"{side}","orderType":"Market","qty":"{qty}"}}'
        headers = sign_headers(body)
        r = requests.post(f"{BASE_URL}/v5/order/create", headers=headers, data=body, timeout=10)
        print(f"{side}:", r.text)
        return r.json().get("retCode") == 0
    except Exception as e:
        print(f"{side} FAIL:", e)
        return False

# ============ BOT LOOP ============
def bot_loop():
    telegram("🤖 Pepebot-3 gestart op Bybit EU - SPOT - UT Bot + Supertrend")
    while True:
        try:
            prijs = get_prijs()
            usdt = get_spot_balance("USDT")
            pepe = get_spot_balance("PEPE")
            closes, highs, lows = get_klines(200)

            state["prijs"] = prijs
            state["usdt"] = usdt
            state
