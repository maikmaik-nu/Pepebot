import os
import math
import threading
import time
import requests
from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP

app = Flask(__name__)

# --- Instellingen ---
SYMBOOL = "PEPEUSDT"
INTERVAL = "15"

BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

session = HTTP(
    testnet=False,
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET
)

bot_state = {"in_positie": False}

def send_telegram(msg):
    try:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            print("Telegram niet ingesteld")
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def get_free_balance(coin):
    try:
        resp = session.get_wallet_balance(accountType="UNIFIED", coin=coin)
        coins = resp["result"]["list"][0]["coin"]
        for c in coins:
            if c["coin"] == coin:
                val = c.get("walletBalance") or c.get("availableToWithdraw") or 0
                print(f"Balance {coin}: {c}")
                return float(val)
        return 0.0
    except Exception as e:
        print(f"Balance error {coin}: {e}")
        return 0.0

def do_buy():
    try:
        usdt_free = get_free_balance("USDT")
        # Rente-op-rente: gebruik alles wat vrij is, minus beetje voor fees
        bedrag = float(usdt_free) * 0.99
        print(f"BUY signaal - USDT free: {usdt_free}, inzetten: {bedrag}")
        if bedrag <= 1:
            msg = f"BUY genegeerd: te weinig USDT ({usdt_free})"
            send_telegram(msg)
            return msg
        # Bybit Spot Market Buy: qty = aantal PEPE, NIET aantal USDT
        # Dus eerst prijs ophalen, dan omrekenen
        ticker = session.get_tickers(category="spot", symbol=SYMBOOL)
        price = float(ticker["result"]["list"][0]["lastPrice"])
        qty = int(math.floor((bedrag / price) / 1000) * 1000)
        print(f"BUY - prijs: {price}, qty PEPE: {qty}")
        if qty <= 0:
            msg = "BUY genegeerd: Qty te klein na afronding"
            send_telegram(msg)
            return msg
        resp = session.place_order(
            category="spot",
            symbol=SYMBOOL,
            side="Buy",
            orderType="Market",
            qty=str(qty)
        )
        print(f"BUY resp: {resp}")
        msg = f"BUY uitgevoerd: {round(bedrag, 2)} USDT -> PEPE"
        send_telegram(msg)
        bot_state["in_positie"] = True
        return msg
    except Exception as e:
        msg = f"Error bij buy: {e}"
        print(msg)
        send_telegram(msg)
        return msg

def do_sell():
    try:
        pepe_free = get_free_balance("PEPE")
        print(f"SELL signaal - PEPE free: {pepe_free}")
        if pepe_free <= 0:
            msg = f"SELL genegeerd: geen PEPE ({pepe_free})"
            send_telegram(msg)
            return msg
        qty = int(math.floor(float(pepe_free) / 1000) * 1000)
        if qty <= 0:
            msg = "SELL genegeerd: Qty te klein na afronding"
            send_telegram(msg)
            return msg
        resp = session.place_order(
            category="spot",
            symbol=SYMBOOL,
            side="Sell",
            orderType="Market",
            qty=str(qty)
        )
        print(f"SELL resp: {resp}")
        msg = f"SELL uitgevoerd: {qty} PEPE verkocht"
        send_telegram(msg)
        bot_state["in_positie"] = False
        return msg
    except Exception as e:
        msg = f"Error bij sell: {e}"
        print(msg)
        send_telegram(msg)
        return msg

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    action = str(data.get("action", "")).lower()
    print(f"Webhook ontvangen: {data}")
    if action == "buy":
        msg = do_buy()
    elif action == "sell":
        msg = do_sell()
    else:
        msg = f"Onbekende action: {action}"
    return jsonify({"status": msg}), 200

@app.route("/ping", methods=["GET"])
def ping():
    return "ok", 200

@app.route("/", methods=["GET"])
def home():
    return "Bot draait", 200

def keep_alive():
    try:
        send_telegram("\U0001f916 Bot gestart - Rente-op-rente AAN")
    except Exception:
        pass

if __name__ == "__main__":
    threading.Thread(target=keep_alive, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=
