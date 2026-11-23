import os
import time
import hmac
import hashlib
import requests
import threading
import json
from decimal import Decimal, ROUND_DOWN
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
LOOP_STARTED = False
LAST_HEARTBEAT = 0
QTY_STEP = None

# ---------- TELEGRAM ----------
def _send_telegram(text):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text},
            timeout=10,
        )
    except Exception as e:
        print("Telegram fout:", e)

def send_msg(text):
    # Koop / verkoop = ALTIJD doorsturen
    _send_telegram(text)

def send_heartbeat(usdt, pepe):
    global LAST_HEARTBEAT
    now = time.time()
    if now - LAST_HEARTBEAT < 3600:
        return
    LAST_HEARTBEAT = now
    _send_telegram(f"UUR - Bot leeft. USDT: {usdt} PEPE: {pepe}")

# ---------- BYBIT SIGN ----------
def sign(params, ts):
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    payload = str(ts) + API_KEY + "5000" + query
    return hmac.new(
        API_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()

def headers_for(params, ts):
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": str(ts),
        "X-BAPI-RECV-WINDOW": "5000",
        "X-BAPI-SIGN": sign(params, ts),
        "Content-Type": "application/json",
    }

# ---------- QTY ----------
def get_qty_step():
    global QTY_STEP
    if QTY_STEP:
        return QTY_STEP
    try:
        r = requests.get(
            BASE + "/v5/market/instruments-info",
            params={"category": "spot", "symbol": SYMBOL},
            timeout=15,
        )
        j = r.json()
        lot = j["result"]["list"][0]["lotSizeFilter"]
        QTY_STEP = Decimal(str(lot["qtyStep"]))
        print("qtyStep:", QTY_STEP, "min:", lot.get("minOrderQty"))
        return QTY_STEP
    except Exception as e:
        print("qtyStep fout, fallback 100000:", e)
        QTY_STEP = Decimal("100000")
        return QTY_STEP

def round_qty(raw):
    step = get_qty_step()
    q = (Decimal(str(raw)) / step).quantize(
        Decimal("1"), rounding=ROUND_DOWN
    ) * step
    return q

# ---------- SALDO ----------
def get_balance_for(account_type):
    ts = int(time.time() * 1000)
    params = {"accountType": account_type}
    r = requests.get(
        BASE + "/v5/account/wallet-balance",
        params=params,
        headers=headers_for(params, ts),
        timeout=15,
    )
    j = r.json()
    if j.get("retCode") != 0:
        print(f"Saldo check {account_type}:", j)
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

def get_balance():
    try:
        usdt, pepe = get_balance_for("SPOT")
        if usdt is not None:
            return usdt, pepe
        print("SPOT faalde, probeer UNIFIED")
        usdt, pepe = get_balance_for("UNIFIED")
        return usdt, pepe
    except Exception as e:
        print("Saldo fout:", e)
        return None, None

# ---------- CANDLES ----------
def get_klines():
    try:
        r = requests.get(
            BASE + "/v5/market/kline",
            params={
                "category": "spot",
                "symbol": SYMBOL,
                "interval": INTERVAL,
                "limit": 200,
            },
            timeout=15,
        )
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

# ---------- UT BOT ----------
def ut_bot_exact(closes, highs, lows, factor=4.0, period=14):
    n = len(closes)
    if n < period + 5:
        return False, 0.0
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    atr = [0.0] * n
    atr[period] = sum(tr[1:period + 1]) / period
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    trailing = [0.0] * n
    pos = [0] * n
    for i in range(1, n):
        nloss = factor * atr[i]
        prev_trail = trailing[i - 1]
        if closes[i] > prev_trail and closes[i - 1] > prev_trail:
            trailing[i] = max(prev_trail, closes[i] - nloss)
        elif closes[i] < prev_trail and closes[i - 1] < prev_trail:
            trailing[i] = min(prev_trail, closes[i] + nloss)
        elif closes[i] > prev_trail:
            trailing[i] = closes[i] - nloss
        else:
            trailing[i] = closes[i] + nloss
        if closes[i - 1] < prev_trail and closes[i] > trailing[i]:
            pos[i] = 1
        elif closes[i - 1] > prev_trail and closes[i] < trailing[i]:
            pos[i] = -1
        else:
            pos[i] = pos[i - 1]
    ut_up = pos[-1] == 1
    return ut_up, trailing[-1]

# ---------- SUPERTREND ----------
def supertrend_exact(closes, highs, lows, period=10, mult=3.0):
    n = len(closes)
    if n < period + 5:
        return False
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    atr = [0.0] * n
    atr[period] = sum(tr[1:period + 1]) / period
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    final_upper = [0.0] * n
    final_lower = [0.0] * n
    trend = [1] * n
    for i in range(1, n):
        hl2 = (highs[i] + lows[i]) / 2
        basic_upper = hl2 + mult * atr[i]
        basic_lower = hl2 - mult * atr[i]
        if basic_upper < final_upper[i - 1] or closes[i - 1] > final_upper[i - 1]:
            final_upper[i] = basic_upper
        else:
            final_upper[i] = final_upper[i - 1]
        if basic_lower > final_lower[i - 1] or closes[i - 1] < final_lower[i - 1]:
            final_lower[i] = basic_lower
        else:
            final_lower[i] = final_lower[i - 1]
        if trend[i - 1] == 1 and closes[i] < final_lower[i]:
            trend[i] = -1
        elif trend[i - 1] == -1 and closes[i] > final_upper[i]:
            trend[i] = 1
        else:
            trend[i] = trend[i - 1]
    st_up = trend[-1] == 1
    return st_up

# ---------- COMBINATIE ----------
def check_signal():
    closes, highs, lows = get_klines()
    if len(closes) < 50:
        return False, False, 0
    closes = closes[:-1]
    highs = highs[:-1]
    lows = lows[:-1]
    ut_up, _ = ut_bot_exact(closes, highs, lows, 4.0, 14)
    st_up = supertrend_exact(closes, highs, lows, 10, 3.0)
    return ut_up, st_up, closes[-1]

# ---------- ORDER ----------
def place_order(side, qty):
    try:
        ts = int(time.time() * 1000)
        params = {
            "category": "spot",
            "symbol": SYMBOL,
            "side": side,
            "orderType": "Market",
            "qty": str(qty),
        }
        r = requests.post(
            BASE + "/v5/order/create",
            json=params,
            headers=headers_for(params, ts),
            timeout=15,
        )
        print("Order:", side, qty, r.text)
        return r.json()
    except Exception as e:
        print("Order fout:", e)
        return None

# ---------- 1 CHECK ----------
def do_one_check():
    usdt, pepe = get_balance()
    if usdt is None:
        print("Geen saldo, probeer over 60 sec opnieuw - geen stop")
        return
    ut_up, st_up, price = check_signal()
    print(f"Check: USDT {usdt} PEPE {pepe} UT {ut_up} ST {st_up} prijs {price}")
    if price <= 0:
        return

    if ut_up and st_up and usdt > 1 and pepe == 0:
        raw_qty = (usdt * 0.999) / price
        qty = round_qty(raw_qty)
        if qty > 0:
            place_order("Buy", qty)
            send_msg(f"UUR - GEKOCHT PEPE voor {usdt} USDT")

    if (not ut_up or not st_up) and pepe > 0:
        qty = round_qty(pepe)
        if qty > 0:
            place_order("Sell", qty)
            send_msg("UUR - VERKOCHT PEPE, alles")

    if int(time.time()) % 3600 < 60:
        send_heartbeat(usdt, pepe)

# ---------- LOOP ----------
def bot_loop():
    global CHECK_RUNNING
    if CHECK_RUNNING:
        return
    CHECK_RUNNING = True
    print("Bot loop gestart, KEY LOADED:", bool(API_KEY and API_SECRET))
    while True:
        try:
            do_one_check()
        except Exception as e:
            print("Loop fout, gaat door:", e)
        time.sleep(60)

def start_loop_once():
    global LOOP_STARTED
    if LOOP_STARTED:
        return
    LOOP_STARTED = True
    threading.Thread(target=bot_loop, daemon=True).start()

# ---------- ROUTES ----------
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
        return jsonify({
            "status": "ERROR",
            "usdt": None,
            "key_start": API_KEY[:4] + "****" if API_KEY else ""
        })
    return jsonify({
        "status": "OK",
        "usdt": usdt,
        "pepe": pepe,
        "key_start": API_KEY[:4] + "****" if API_KEY else ""
    })

@app.route("/check")
def check():
    # Geen nieuwe loop, gewoon 1 check
    threading.Thread(target=do_one_check, daemon=True).start()
    return "Check gestart, kijk in Logs", 200

@app.route("/webhook", methods=["POST"])
def webhook():
    threading.Thread(target=do_one_check, daemon=True).start()
    return jsonify({"ok": True})

start_loop_once()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
