import os, time, hmac, hashlib, requests, threading
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE = os.getenv("BASE", "https://api.bybit.eu")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SYMBOL = "PEPEUSDC"
INTERVAL = "15"
CHECK_RUNNING = False
LAST_MSG_TIME = 0

def send_msg(msg):
    global LAST_MSG_TIME
    is_trade = "KOOP" in msg or "VERKOOP" in msg
    now = time.time()
    if not is_trade and now - LAST_MSG_TIME < 3600:
        return
    if not is_trade:
        LAST_MSG_TIME = now
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        print("Telegram fout:", e, flush=True)

def signed_headers(params, ts):
    recv = "5000"
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    to_sign = str(ts) + API_KEY + recv + query
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": str(ts),
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sig,
        "Content-Type": "application/json"
    }

def get_balance():
    try:
        ts = int(time.time() * 1000)
        params = {"accountType": "SPOT"}
        r = requests.get(f"{BASE}/v5/account/wallet-balance", params=params, headers=signed_headers(params, ts), timeout=15)
        data = r.json()
        if data.get("retCode") != 0:
            print("Balance fail:", data, flush=True)
            return None
        lst = data.get("result", {}).get("list", [])
        if not lst:
            return None
        coins = lst[0].get("coin", [])
        out = {"USDC": 0.0, "PEPE": 0.0}
        for c in coins:
            if c.get("coin") in out:
                try:
                    out[c.get("coin")] = float(c.get("walletBalance", 0) or 0)
                except:
                    out[c.get("coin")] = 0.0
        return out
    except Exception as e:
        print("Balance fout:", e, flush=True)
        return None

def get_klines():
    try:
        r = requests.get(f"{BASE}/v5/market/kline", params={"category": "spot", "symbol": SYMBOL, "interval": INTERVAL, "limit": 200}, timeout=15)
        lst = r.json().get("result", {}).get("list", [])
        if not lst:
            return None, None, None
        lst = list(reversed(lst))
        closes = [float(x[4]) for x in lst]
        highs = [float(x[2]) for x in lst]
        lows = [float(x[3]) for x in lst]
        return closes, highs, lows
    except Exception as e:
        print("Klines fout:", e, flush=True)
        return None, None, None

def get_price():
    try:
        r = requests.get(f"{BASE}/v5/market/tickers", params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        lst = r.json().get("result", {}).get("list", [])
        if lst:
            p = float(lst[0].get("lastPrice", 0))
            if p > 0:
                return p
    except Exception as e:
        print("Prijs fout:", e, flush=True)
    return 0.0

def ut_bot(closes, highs, lows, factor=4.0, period=14):
    n = len(closes)
    if n < period + 5:
        return False
    tr = [0.0] * n
    for i in range(1, n):
        tr[i] = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
    atr = [0.0] * n
    atr[period] = sum(tr[1:period+1]) / period
    for i in range(period+1, n):
        atr[i] = (atr[i-1] * (period - 1) + tr[i]) / period
    trailing = [0.0] * n
    pos = [0] * n
    for i in range(1, n):
        nloss = factor * atr[i]
        prev = trailing[i-1]
        if closes[i] > prev and closes[i-1] > prev:
            trailing[i] = max(prev, closes[i] - nloss)
        elif closes[i] < prev and closes[i-1] < prev:
            trailing[i] = min(prev, closes[i] + nloss)
        elif closes[i] > prev:
            trailing[i] = closes[i] - nloss
        else:
            trailing[i] = closes[i] + nloss
        if closes[i-1] < prev and closes[i] > trailing[i]:
            pos[i] = 1
        elif closes[i-1] > prev and closes[i] < trailing[i]:
            pos[i] = -1
        else:
            pos[i] = pos[i-1]
    return pos[-1] == 1

def check_signal():
    closes, highs, lows = get_klines()
    if not closes or len(closes) < 50:
        return False, 0.0
    closes = closes[:-1]
    highs = highs[:-1]
    lows = lows[:-1]
    ut_up = ut_bot(closes, highs, lows, 4.0, 14)
    price = get_price()
    return ut_up, price

def place_order(side, qty):
    try:
        ts = int(time.time() * 1000)
        params = {"category": "spot", "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty)}
        r = requests.post(f"{BASE}/v5/order/create", json=params, headers=signed_headers(params, ts), timeout=15)
        print("Order:", side, qty, r.text, flush=True)
        return r.json()
    except Exception as e:
        print("Order fout:", e, flush=True)
        return None

def bot_loop():
    global CHECK_RUNNING
    if CHECK_RUNNING:
        return
    CHECK_RUNNING = True
    print("Bot loop gestart", flush=True)
    send_msg("Pepebot gestart - UT Bot alleen - PEPEUSDC")
    while True:
        try:
            bal = get_balance()
            if bal is None:
                print("Geen saldo, over 60 sec opnieuw", flush=True)
                time.sleep(60)
                continue
            usdc = bal.get("USDC", 0.0)
            pepe = bal.get("PEPE", 0.0)
            ut_up, price = check_signal()
            if price <= 0:
                print(f"Check: USDC {usdc} PEPE {pepe} UT {ut_up} prijs 0 - SKIP", flush=True)
                time.sleep(60)
                continue
            print(f"Check: USDC {usdc} PEPE {pepe} UT {ut_up} prijs {price}", flush=True)
            if ut_up and usdc > 1 and pepe == 0:
                qty = int(usdc / price)
                if qty > 0:
                    res = place_order("Buy", qty)
                    if res and res.get("retCode") == 0:
                        send_msg(f"KOOP: {qty} PEPE @ {price}")
                    else:
                        send_msg(f"KOOP MISLUKT: {res}")
            elif not ut_up and pepe > 0:
                qty = int(pepe)
                if qty > 0:
                    res = place_order("Sell", qty)
                    if res and res.get("retCode") == 0:
                        send_msg(f"VERKOOP: {qty} PEPE @ {price}")
                    else:
                        send_msg(f"VERKOOP MISLUKT: {res}")
        except Exception as e:
            print("Loop fout, gaat door:", e, flush=True)
        time.sleep(60)

@app.route("/")
def home():
    return "Bot leeft", 200

@app.route("/ping")
def ping():
    return "pong", 200

@app.route("/test-key")
def test_key():
    bal = get_balance()
    if bal is None:
        return jsonify({"status": "ERROR"})
    return jsonify({"status": "OK", "usdc": bal.get("USDC"), "pepe": bal.get("PEPE")})

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
