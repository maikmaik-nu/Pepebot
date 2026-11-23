import os
import time
import hmac
import hashlib
import threading
import requests
from flask import Flask, jsonify

# ---------- CONFIG ----------
app = Flask(__name__)

API_KEY = os.environ.get("API_KEY", "")
API_SECRET = os.environ.get("API_SECRET", "")
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT", "")

BASE = "https://api.bybit.com"
SYMBOL = "PEPEUSDT"
INTERVAL = "15"

CHECK_RUNNING = False
LAST_MSG = 0

# ---------- CHAT MELDING, MAX 1x PER UUR ----------
def send_msg(text):
    global LAST_MSG
    if not TG_TOKEN or not TG_CHAT:
        return
    now = time.time()
    if now - LAST_MSG < 3600 and "UUR" not in text:
        return
    try:
        requests.post(
            "https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage",
            json={"chat_id": TG_CHAT, "text": text},
            timeout=10
        )
        LAST_MSG = now
    except Exception as e:
        print("TG fout:", e)

# ---------- HANDTEKENING ----------
def sign(params, timestamp):
    qs = "&".join([k + "=" + str(params[k]) for k in sorted(params)])
    to_sign = str(timestamp) + API_KEY + "5000" + qs
    return hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()

# ---------- SALDO, OUDE WERKENDE MANIER ----------
def get_balance():
    try:
        ts = int(time.time() * 1000)
        params = {"accountType": "SPOT"}
        sig = sign(params, ts)
        headers = {
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-TIMESTAMP": str(ts),
            "X-BAPI-RECV-WINDOW": "5000",
            "X-BAPI-SIGN": sig
        }
        r = requests.get(BASE + "/v5/account/wallet-balance",
                         params=params, headers=headers, timeout=15)
        j = r.json()
        if j.get("retCode") != 0:
            print("Saldo check SPOT:", j)
            return None, None
        coins = j["result"]["list"][0]["coin"]
        usdt = 0.0
        pepe = 0.0
        for c in coins:
            if c["coin"] == "USDT":
                usdt = float(c.get("walletBalance", 0) or 0)
            if c["coin"] == "PEPE":
                pepe = float(c.get("walletBalance", 0) or 0)
        return usdt, pepe
    except Exception as e:
        print("Saldo fout:", e)
        return None, None

# ---------- CANDLES ----------
def get_klines():
    try:
        r = requests.get(BASE + "/v5/market/kline", params={"category": "spot",
            "symbol": SYMBOL, "interval": INTERVAL, "limit": 200}, timeout=15)
        j = r.json()
        data = j.get("result", {}).get("list", [])
        data = list(reversed(data))
        closes = [float(x[4]) for x in data]
        highs = [float(x[2]) for x in data]
        lows = [float(x[3]) for x in data]
        return closes, highs, lows
    except Exception as e:
        print("Kline fout:", e)
        return [], [], []

# ---------- UT BOT, EXACT ZOALS OP GRAFIEK ----------
def ut_bot_exact(closes, highs, lows, factor=4.0, period=14):
    n = len(closes)
    if n < period + 5:
        return False, 0.0
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i],
                    abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    atr = [0.0] * n
    atr[period] = sum(tr[1:period+1]) / period
    for i in range(period+1, n):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
    trailing = [0.0] * n
    pos = [0] * n
    for i in range(1, n):
        nloss = factor * atr[i]
        prev_trail = trailing[i-1]
        if closes[i] > prev_trail and closes[i-1] > prev_trail:
            trailing[i] = max(prev_trail, closes[i] - nloss)
        elif closes[i] < prev_trail and closes[i-1] < prev_trail:
            trailing[i] = min(prev_trail, closes[i] + nloss)
        elif closes[i] > prev_trail:
            trailing[i] = closes[i] - nloss
        else:
            trailing[i] = closes[i] + nloss
        if closes[i-1] < prev_trail and closes[i] > trailing[i]:
            pos[i] = 1
        elif closes[i-1] > prev_trail and closes[i] < trailing[i]:
            pos[i] = -1
        else:
            pos[i] = pos[i-1]
    ut_up = pos[-1] == 1
    return ut_up, trailing[-1]

# ---------- SUPERTREND, EXACT 10 / 3 ----------
def supertrend_exact(closes, highs, lows, period=10, mult=3.0):
    n = len(closes)
    if n < period + 5:
        return False
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i],
                    abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    atr = [0.0] * n
    atr[period] = sum(tr[1:period+1]) / period
    for i in range(period+1, n):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
    final_upper = [0.0] * n
    final_lower = [0.0] * n
    trend = [1] * n
    for i in range(1, n):
        hl2 = (highs[i] + lows[i]) / 2
        basic_upper = hl2 + mult * atr[i]
        basic_lower = hl2 - mult * atr[i]
        if basic_upper < final_upper[i-1] or closes[i-1] > final_upper[i-1]:
            final_upper[i] = basic_upper
        else:
            final_upper[i] = final_upper[i-1]
        if basic_lower > final_lower[i-1] or closes[i-1] < final_lower[i-1]:
            final_lower[i] = basic_lower
        else:
            final_lower[i] = final_lower[i-1]
        if trend[i-1] == 1 and closes[i] < final_lower[i]:
            trend[i] = -1
        elif trend[i-1] == -
