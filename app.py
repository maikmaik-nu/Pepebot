import os
import time
import hmac
import hashlib
import requests
import threading
from flask import Flask, request, jsonify

app = Flask(__name__)

API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

BASE = "https://api.bybit.eu"
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

# ---------- CANDLES, MET FALLBACK + HARD STOP ----------
BASES = ["https://api.bybit.eu", "https://api.bybit.com"]

def get_klines():
    """Geeft (closes, highs, lows) terug, of None als prijs ophalen faalt.
    NOOIT prijs 0, NOOIT lege lijsten als 'geldige' data."""
    for base in BASES:
        try:
            r = requests.get(base + "/v5/market/kline", params={"category": "spot",
                "symbol": SYMBOL, "interval": INTERVAL, "limit": 200}, timeout=15)
            j = r.json()
            data = j.get("result", {}).get("list", [])
            if not data:
                print("Kline leeg via", base, ":", j)
                continue
            data = list(reversed(data))
            closes = [float(x[4]) for x in data]
            highs = [float(x[2]) for x in data]
            lows = [float(x[3]) for x in data]
            if len(closes) < 50 or closes[-1] <= 0:
                print("Kline ongeldig via", base)
                continue
            return closes, highs, lows
        except Exception as e:
            print("Kline fout via", base, ":", e)
            continue
    print("HARD STOP: geen prijs via eu én com - geen trade")
    return None

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
        elif trend[i-1] == -1 and closes[i] > final_upper[i]:
            trend[i] = 1
        else:
            trend[i] = trend[i-1]
    st_up = trend[-1] == 1
    return st_up

# ---------- COMBINATIE ----------
def check_signal():
    """Geeft (ut_up, st_up, price) terug, of (None, None, None) bij geen prijs."""
    result = get_klines()
    if result is None:
        return None, None, None
    closes, highs, lows = result
    if len(closes) < 50:
        return None, None, None
    closes = closes[:-1]
    highs = highs[:-1]
    lows = lows[:-1]
    ut_up, _ = ut_bot_exact(closes, highs, lows, 4.0, 14)
    st_up = supertrend_exact(closes, highs, lows, 10, 3.0)
    price = closes[-1]
    if price <= 0:
        return None, None, None
    return ut_up, st_up, price

# ---------- ORDER ----------
def place_order(side, qty):
    try:
        ts = int(time.time() * 1000)
        params = {"category": "spot", "symbol": SYMBOL, "side": side,
                  "orderType": "Market", "qty": str(qty)}
        sig = sign(params, ts)
        headers = {
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-TIMESTAMP": str(ts),
            "X-BAPI-RECV-WINDOW": "5000",
            "X-BAPI-SIGN": sig
        }
        r = requests.post(BASE + "/v5/order/create", json=params, headers=headers, timeout=15)
        print("Order:", side, qty, r.text)
        return r.json()
    except Exception as e:
        print("Order fout:", e)
        return None

# ---------- LOOP, NOOIT STOPPEN ----------
def bot_loop():
    global CHECK_RUNNING
    if CHECK_RUNNING:
        return
    CHECK_RUNNING = True
    print("Bot loop gestart, KEY LOADED:", bool(API_KEY and API_SECRET))
    while True:
        try:
            usdt, pepe = get_balance()
            if usdt is None:
                print("Geen saldo, probeer over 60 sec opnieuw - geen stop")
                time.sleep(60)
                continue
            ut_up, st_up, price = check_signal()
            if ut_up is None:
                print("Geen prijs - HARD STOP, geen trade, probeer over 60 sec opnieuw")
                time.sleep(60)
                continue
            print("Check: USDT", usdt, "PEPE", pepe, "UT", ut_up, "ST", st_up, "prijs", price)
            if ut_up and st_up and usdt > 1 and pepe == 0:
                qty = int((usdt * 0.999) / price) if price and price > 0 else 0
                if qty > 0:
                    place_order("Buy", qty)
                    send_msg("UUR - GEKOCHT PEPE voor " + str(usdt) + " USDT")
            if (not ut_up or not st_up) and pepe > 0:
                place_order("Sell", int(pepe))
                send_msg("UUR - VERKOCHT PEPE, alles")
            if int(time.time()) % 3600 < 60:
                send_msg("UUR - Bot leeft. USDT: " + str(usdt) + " PEPE: " + str(pepe))
        except Exception as e:
            print("Loop fout, gaat door:", e)
        time.sleep(60)

@app.route("/")
def home():
    return "Bot leeft", 200

@app.route("/ping")
def ping():
    return "pong", 200

@app.route("/test-key")
def test_key():
    usdt, pepe = get_balance()
    if usdt is None:
        return jsonify({"status": "ERROR", "usdt": None,
                        "key_start": API_KEY[:4] + "****" if API_KEY else ""})
    return jsonify({"status": "OK", "usdt": usdt, "pepe": pepe,
                    "key_start": API_KEY[:4] + "****" if API_KEY else ""})

@app.route("/start")
def start():
    threading.Thread(target=bot_loop, daemon=True).start()
    return "Bot gestart", 200

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
