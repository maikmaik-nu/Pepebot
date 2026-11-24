import os, time, hmac, hashlib, threading, requests, math, json
from flask import Flask, jsonify
from datetime import datetime

app = Flask(__name__)

BASE_URL = "https://api.bybit.eu"
API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SYMBOL = "PEPEUSDC"
CATEGORY = "spot"
INTERVAL = "15"
BUY_AMOUNT_USDC = 15.0
CHECK_EVERY = 60

ATR_PERIOD = 10
ATR_MULT = 3.0
UT_KEY = 1.0
UT_ATR_PERIOD = 10

state = {
    "prijs": 0.0, "usdc": 0.0, "pepe": 0.0,
    "signaal": "NONE", "positie": "GEEN",
    "entry_prijs": 0.0, "winst_pct": 0.0,
    "supertrend": "NONE", "utbot": "NONE",
    "last_trade": "nog geen", "last_error": "",
    "laatste_check": "", "bot_status": "gestart"
}

def telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print("TELEGRAM FAIL:", e)

def sign_headers(payload_str):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    to_sign = ts + API_KEY + recv + payload_str
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv, "X-BAPI-SIGN": sig,
        "Content-Type": "application/json"
    }

def build_query(params):
    return "&".join([f"{k}={params[k]}" for k in sorted(params)])

def get_prijs():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
        data = r.json()
        if data.get("retCode") != 0:
            print("PRIJS FAIL:", data)
            return 0.0
        lst = data.get("result", {}).get("list", [])
        if not lst:
            return 0.0
        return float(lst[0].get("lastPrice", 0))
    except Exception as e:
        print("PRIJS FAIL:", e)
        return 0.0

def get_spot_balance(coin):
    try:
        query = build_query({"accountType": "SPOT", "coin": coin})
        headers = sign_headers(query)
        r = requests.get(f"{BASE_URL}/v5/account/wallet-balance?{query}", headers=headers, timeout=10)
        data = r.json()
        if data.get("retCode") != 0:
            print(f"Saldo fout {coin} SPOT:", data)
            state["last_error"] = f"{coin}: {data.get('retCode')} {data.get('retMsg')}"
            return None
        lst = data.get("result", {}).get("list", [])
        if not lst:
            return 0.0
        for c in lst[0].get("coin", []):
            if c.get("coin") == coin:
                return float(c.get("walletBalance", 0))
        return 0.0
    except Exception as e:
        print(f"Saldo fout {coin}:", e)
        return None

def get_klines(limit=200):
    try:
        r = requests.get(f"{BASE_URL}/v5/market/kline", params={"category": CATEGORY, "symbol": SYMBOL, "interval": INTERVAL, "limit": limit}, timeout=10)
        data = r.json()
        if data.get("retCode") != 0:
            print("KLINES FAIL:", data)
            return [], [], []
        klines = list(reversed(data.get("result", {}).get("list", [])))
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        return closes, highs, lows
    except Exception as e:
        print("KLINES FAIL:", e)
        return [], [], []

def calc_atr(highs, lows, closes, period):
    if len(closes) < period + 2:
        return [0.0]*len(closes)
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    atr = [sum(trs[:period])/period]
    for i in range(period, len(trs)):
        atr.append((atr[-1]*(period-1) + trs[i]) / period)
    return [0.0]*(len(closes)-len(atr)) + atr

def calc_supertrend(highs, lows, closes, atr_period=10, mult=3.0):
    atr = calc_atr(highs, lows, closes, atr_period)
    n = len(closes)
    upper = [0.0]*n
    lower = [0.0]*n
    trend = [1]*n
    for i in range(1, n):
        hl2 = (highs[i]+lows[i])/2
        upper[i] = hl2 + mult*atr[i]
        lower[i] = hl2 - mult*atr[i]
        if closes[i] > upper
