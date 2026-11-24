import os
import time
import hmac
import hashlib
import json
import threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = "https://api.bybit.eu"
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
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg},
            timeout=10
        )
    except Exception as e:
        log(f"Telegram fout: {e}")

def signed_headers(query_or_body):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    to_sign = ts + API_KEY + recv + query_or_body
    sign = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json"
    }

def get_balance():
    try:
        qs = "accountType=SPOT"
        headers = signed_headers(qs)
        r = requests.get(f"{BASE_URL}/v5/account/wallet-balance?{qs}", headers=headers, timeout=10)
        d = r.json()
        if d.get("retCode") != 0:
            log(f"Balance fail: {d}")
            return 0.0, 0.0
        coins = d["result"]["list"][0]["coin"]
        money = 0.0
        pepe = 0.0
        for c in coins:
            if c["coin"] in ("USDT", "USDC"):
                money += float(c.get("walletBalance") or 0)
            if c["coin"] == "PEPE":
                pepe = float(c.get("walletBalance") or 0)
        return money, pepe
    except Exception as e:
        log(f"Balance fout: {e}")
        return 0.0, 0.0

def get_price():
    try:
        r = requests.get(
            f"{BASE_URL}/v5/market/tickers",
            params={"category": "spot", "symbol": SYMBOL},
            timeout=10
        )
        d = r.json()
        lst = d["result"]["list"]
        return float(lst[0]["lastPrice"])
    except Exception as e:
        log(f"Prijs fout: {e}")
        return 0.0

def place_order(side, qty):
    try:
        body = {
            "category": "spot",
            "symbol": SYMBOL,
            "side": side,
            "orderType": "Market",
            "qty": str(qty)
        }
        body_str = json.dumps(body)
        headers = signed_headers(body_str)
        r = requests.post(f"{BASE_URL}/v5/order/create", headers=headers, data=body_str, timeout=10)
        log(f"ORDER {side} {qty}: {r.text[:300]}")
        return r.json()
    except Exception as e:
        log(f"Order fout: {e}")
        return None

def bot_loop():
    global running
    if running:
        return
    running = True
    log("Bot loop gestart - GAAT NU TRADEN")
    time.sleep(5)
    while True:
        try:
            money, pepe = get_balance()
            price = get_price()
            log(f"Check: Geld {money:.2f} PEPE {pepe:.0f} prijs {price}")
            if money > 1 and pepe == 0 and price > 0:
                buy_usd = min(money, 10)
                qty = int(buy_usd / price)
                if qty > 0:
                    log(f"KOOP NU: {qty} PEPE voor {buy_usd:.2f}")
                    place_order("Buy", qty)
                    send_tg(f"PEPE GEKOCHT: {qty} voor ~{buy_usd:.2f} prijs {price}")
        except Exception as e:
            log(f"Loop fout, gaat door: {e}")
        time.sleep(60)

@app.route("/")
def home():
    return "Pepebot-3 live"

@app.route("/test-key")
def test_key():
    try:
        money, pepe = get_balance()
        price = get_price()
        return jsonify({
            "ok": True,
            "geld": money,
            "PEPE": pepe,
            "prijs": price,
            "key_set": bool(API_KEY),
            "key_len": len(API_KEY)
        })
    except Exception as e:
        return jsonify({"ok": False, "fout": str(e)}), 200

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
