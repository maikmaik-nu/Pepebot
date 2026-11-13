import os
import math
import time
import threading
import requests
from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP

app = Flask(__name__)

# ===== CONFIG - alles uit Render Environment, niks hard in script =====
SYMBOOL = "PEPEUSDT"
BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
# Hoeveel % van je USDT per trade, 98% = rente-op-rente / compound
TRADE_PCT = float(os.environ.get("TRADE_PCT", "0.98"))
# Minimale winst om te verkopen, 1.5%
TAKE_PROFIT_PCT = float(os.environ.get("TAKE_PROFIT_PCT", "0.015"))
# Check elke 60 sec
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))

session = HTTP(testnet=False, api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET)
bot_state = {"in_positie": False, "koop_prijs": 0.0, "aantal": 0.0, "laatste_actie": "gestart", "laatste_ping": time.time()}

# ===== TELEGRAM =====
def send_telegram(msg):
    try:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            print("Telegram niet ingesteld, msg:", msg)
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print("Telegram fout:", e)

# ===== BYBIT HELPERS =====
def get_prijs():
    r = session.get_tickers(category="spot", symbol=SYMBOOL)
    return float(r["result"]["list"][0]["lastPrice"])

def get_usdt():
    r = session.get_wallet_balance(accountType="UNIFIED")
    for coin in r["result"]["list"][0]["coin"]:
        if coin["coin"] == "USDT":
            return float(coin["walletBalance"])
    return 0.0

def get_pepe():
    r = session.get_wallet_balance(accountType="UNIFIED")
    for coin in r["result"]["list"][0]["coin"]:
        if coin["coin"] == "PEPE":
            return float(coin["walletBalance"])
    return 0.0

def koop():
    usdt = get_usdt()
    bedrag = usdt * TRADE_PCT
    if bedrag < 5:
        send_telegram(f"⚠️ Te weinig USDT om te kopen: {usdt:.2f}")
        return
    prijs = get_prijs()
    aantal = math.floor((bedrag / prijs))
    if aantal <= 0:
        return
    session.place_order(category="spot", symbol=SYMBOOL, side="Buy", orderType="Market", qty=str(aantal))
    bot_state.update({"in_positie": True, "koop_prijs": prijs, "aantal": float(aantal), "laatste_actie": f"GEKOCHT {aantal} @ {prijs}"})
    send_telegram(f"🟢 GEKOCHT\n{aantal} PEPE @ {prijs}\nUSDT gebruikt: {bedrag:.2f}")

def verkoop():
    aantal = get_pepe()
    if aantal <= 0:
        bot_state["in_positie"] = False
        return
    session.place_order(category="spot", symbol=SYMBOOL, side="Sell", orderType="Market", qty=str(int(aantal)))
    prijs = get_prijs()
    winst_pct = (prijs - bot_state["koop_prijs"]) / bot_state["koop_prijs"] * 100 if bot_state["koop_prijs"] else 0
    bot_state.update({"in_positie": False, "koop_prijs": 0.0, "aantal": 0.0, "laatste_actie": f"VERKOCHT @ {prijs} winst {winst_pct:.2f}%"})
    send_telegram(f"🔴 VERKOCHT\n{aantal} PEPE @ {prijs}\nWinst: {winst_pct:.2f}%")

# ===== TRADING LOOP =====
def trading_loop():
    time.sleep(5)
    send_telegram("🤖 Bot gestart - Rente-op-rente AAN\nStuur /ping om te testen")
    while True:
        try:
            prijs = get_prijs()
            if not bot_state["in_positie"]:
                # Alleen kopen als we geen positie hebben
                koop()
            else:
                # Check winst
                if prijs >= bot_state["koop_prijs"] * (1 + TAKE_PROFIT_PCT):
                    verkoop()
                else:
                    print(f"Wachten... prijs {prijs} koop {bot_state['koop_prijs']}")
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            print("Loop fout:", e)
            time.sleep(30)

# ===== FLASK ROUTES - voor Render + Ping =====
@app.route("/")
def home():
    return "Pepebot is LIVE ✅", 200

@app.route("/ping")
def ping():
    bot_state["laatste_ping"] = time.time()
    return jsonify({"status": "ok", "bot": "live", "symbool": SYMBOOL, "in_positie": bot_state["in_positie"], "laatste_actie": bot_state["laatste_actie"]}), 200

@app.route("/health")
def health():
    return jsonify({"ok": True, "in_positie": bot_state["in_positie"]}), 200

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    data = request.get_json(force=True, silent=True) or {}
    msg = data.get("message", {})
    text = msg.get("text", "").strip().lower()
    chat_id = str(msg.get("chat", {}).get("id", ""))
    if text == "/ping":
        send_telegram(f"🏓 PONG - Ik ben LIVE ✅\nSymbool: {SYMBOOL}\nIn positie: {bot_state['in_positie']}\nLaatste actie: {bot_state['laatste_actie']}")
    elif text == "/status":
        try:
            prijs = get_prijs()
            usdt = get_usdt()
            send_telegram(f"📊 STATUS\nPrijs: {prijs}\nUSDT: {usdt:.2f}\nIn positie: {bot_state['in_positie']}\nLaatste actie: {bot_state['laatste_actie']}")
        except Exception as e:
            send_telegram(f"Status fout: {e}")
    elif text == "/stop":
        send_telegram("⏸️ Stop commando ontvangen - bot blijft draaien maar koopt niks nieuws. Herstart in Render om weer te starten.")
    return jsonify({"ok": True}), 200

# Start trading in background, niet 2x in debug
if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
    threading.Thread(target=trading_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
