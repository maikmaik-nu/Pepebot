import os, time, hmac, hashlib, threading, requests, json
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = os.getenv("BASE_URL", "https://api.bybit.eu")
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
SYMBOL = "PEPEUSDT"
running = False

def log(msg):
    print(msg, flush=True)

def send_tg(msg):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
    except Exception as e:
        log(f"Telegram fout: {e}")

def make_sign(timestamp, recv_window, payload_str):
    msg = timestamp + API_KEY + recv_window + payload_str
    return hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

def get_balance():
    try:
        if not API_KEY or not API_SECRET:
            log("Balance fout: geen API key in Render")
            return 0.0, 0.0
        ts = str(int(time.time() * 1000))
        recv = "5000"
        query = "accountType=SPOT"
        sign = make_sign(ts, recv, query)
        headers = {
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
            "X-BAPI-SIGN": sign
        }
        r = requests.get(f"{BASE_URL}/v5/account/wallet-balance",
            headers=headers, params={"accountType": "SPOT"}, timeout=10)
        d = r.json()
        if d.get("retCode") != 0:
            log(f"Balance fail: {d}")
            return 0.0, 0.0
        coins = d["result"]["list"][0]["coin"]
        usdt = usdc = pepe = 0.0
        for c in coins:
            if c["coin"] == "USDT":
                usdt = float(c.get("walletBalance") or 0)
            if c["coin"] == "USDC":
                usdc = float(c.get("walletBalance") or 0)
            if c["coin"] == "PEPE":
                pepe = float(c.get("walletBalance") or 0)
        return usdt + usdc, pepe
    except Exception as e:
        log(f"Balance fout: {e}")
        return 0.0, 0.0

def get_closes():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/kline",
            params={"category": "spot", "symbol": SYMBOL, "interval": "15", "limit": 200}, timeout=10)
        d = r.json()
        if d.get("retCode") != 0:
            log(f"Klines fail: {d}")
            return None
        closes = [float(x[4]) for x in d["result"]["list"]]
        closes.reverse()
        return closes[:-1]
    except Exception as e:
        log(f"Klines fout: {e}")
        return None

def supertrend_up(closes):
    try:
        if len(closes) < 50:
            return False
        return closes[-1] > sum(closes[-50:]) / 50
    except Exception:
        return False

def place_order(side, qty):
    try:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        body = {
            "category": "spot",
            "symbol": SYMBOL,
            "side": side,
            "orderType": "Market",
            "qty": str(qty)
        }
        body_str = json.dumps(body)
        sign = make_sign(ts, recv, body_str)
        headers = {
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
            "X-BAPI-SIGN": sign,
            "Content-Type": "application/json"
        }
        r = requests.post(f"{BASE_URL}/v5/order/create",
            headers=headers, data=body_str, timeout=10)
        log(f"ORDER {side} {qty}: {r.text[:300]}")
        return r.json() if r.text.startswith("{") else None
    except Exception as e:
        log(f"Order fout: {e}")
        return None

def bot_loop():
    global running
    if running:
        return
    running = True
    log("Bot loop gestart")
    while True:
        try:
            money, pepe = get_balance()
            closes = get_closes()
            if closes is None:
                log("Geen klines, over 60 sec opnieuw")
                time.sleep(60)
                continue
            price = closes[-1]
            up = supertrend_up(closes)
            log(f"Check: Geld {money:.2f} PEPE {pepe:.0f} UP={up} prijs {price}")
            if up and money > 1 and pepe == 0:
                buy_usd = min(money, 10)
                qty = int(buy_usd / price)
                if qty > 0:
                    place_order("Buy", qty)
                    send_tg(f"PEPE GEKOCHT: {qty} voor ~{buy_usd:.2f} prijs {price}")
            elif not up and pepe > 0:
                place_order("Sell", int(pepe))
                send_tg(f"PEPE VERKOCHT: {pepe:.0f} prijs {price}")
        except Exception as e:
            log(f"Loop fout, gaat door: {e}")
        time.sleep(60)

@app.route("/")
def home():
    return "Pepebot-3 live"

@app.route("/ping")
def ping():
    return "pong"

@app.route("/test-key")
def test_key():
    try:
        money, pepe = get_balance()
        return jsonify({"ok": True, "geld_USDT_USDC": money, "PEPE": pepe, "base": BASE_URL})
    except Exception as e:
        return jsonify({"ok": False, "fout": str(e)}), 200

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
