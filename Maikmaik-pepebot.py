
from flask import Flask, request, jsonify
import os
import requests
import threading
import time
from pybit.unified_trading import HTTP

app = Flask(__name__)

# --- Config uit Render Environment Variables ---
BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
SELF_URL = os.environ.get("SELF_URL") or os.environ.get("RENDER_EXTERNAL_URL") or ""

SYMBOL = "PEPEUSDT"
CATEGORY = "spot"

# 【entity-Bybit¦canonical_name=BYBIT】 session
session = HTTP(
    testnet=False,
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET,
)

def send_telegram(message):
    """Stuur een bericht naar Telegram, als token + chat_id zijn ingesteld."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"Telegram niet ingesteld, bericht: {message}")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message
        }, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def get_free_balance(coin):
    """Haal free balance op voor een coin uit Unified account."""
    try:
        resp = session.get_wallet_balance(accountType="UNIFIED", coin=coin)
        coins = resp["result"]["list"][0]["coin"]
        for c in coins:
            if c["coin"] == coin:
                return float(c.get("availableToWithdraw") or c.get("walletBalance") or 0)
        return 0.0
    except Exception as e:
        print(f"Balance error {coin}: {e}")
        return 0.0

def get_price(symbol):
    """Haal laatste prijs op."""
    tickers = session.get_tickers(category=CATEGORY, symbol=symbol)
    return float(tickers["result"]["list"][0]["lastPrice"])

@app.route("/", methods=["GET"])
def home():
    return "Bot is online!", 200

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "alive", "time": time.time()}), 200

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True)
    if not data or "action" not in data:
        return jsonify({"error": "Geen action meegegeven, gebruik {'action': 'buy'} of {'action': 'sell'}"}), 400

    action = str(data["action"]).lower().strip()
    if action not in ["buy", "sell"]:
        return jsonify({"error": "Action moet 'buy' of 'sell' zijn"}), 400

    try:
        if action == "buy":
            usdt_free = get_free_balance("USDT")
            print(f"BUY signaal - USDT free: {usdt_free}")

            if usdt_free <= 1:
                msg = f"BUY genegeerd: te weinig USDT ({usdt_free})"
                send_telegram(msg)
                return jsonify({"status": msg}), 200

            price = get_price(SYMBOL)
            qty = int((usdt_free * 0.99) / price)

            if qty <= 0:
                return jsonify({"error": "Qty te klein"}), 400

            order = session.place_order(
                category=CATEGORY,
                symbol=SYMBOL,
                side="Buy",
                orderType="Market",
                qty=str(qty),
            )
            msg = f"✅ BUY uitgevoerd: {qty} PEPE voor ~{usdt_free:.2f} USDT @ {price}"
            print(msg)
            send_telegram(msg)
            return jsonify({"status": msg, "order": order}), 200

        if action == "sell":
            pepe_free = get_free_balance("PEPE")
            print(f"SELL signaal - PEPE free: {pepe_free}")

            if pepe_free <= 0:
                msg = f"SELL genegeerd: geen PEPE ({pepe_free})"
                send_telegram(msg)
                return jsonify({"status": msg}), 200

            qty = int(pepe_free)

            order = session.place_order(
                category=CATEGORY,
                symbol=SYMBOL,
                side="Sell",
                orderType="Market",
                qty=str(qty),
            )
            msg = f"✅ SELL uitgevoerd: {qty} PEPE verkocht"
            print(msg)
            send_telegram(msg)
            return jsonify({"status": msg, "order": order}), 200

    except Exception as e:
        msg = f"❌ Error bij {action}: {e}"
        print(msg)
        send_telegram(msg)
        return jsonify({"error": str(e)}), 500

# --- KEEP-ALIVE: Ping Render elke 5 minuten zodat hij niet slaapt ---
def keep_alive():
    """Ping zichzelf elke 5 minuten + stuur Telegram melding."""
    # Wacht even tot de server gestart is
    time.sleep(10)
    send_telegram("🤖 Bot gestart - keep-alive elke 5 min actief, Render blijft wakker")
    while True:
        try:
            if SELF_URL:
                url = SELF_URL.rstrip("/") + "/ping"
                r = requests.get(url, timeout=10)
                print(f"Keep-alive ping: {r.status_code} -> {url}")
                # Melding elke 5 min zoals je vroeg
                send_telegram(f"💓 Bot nog online - keep-alive ping OK ({r.status_code})")
            else:
                print("Keep-alive: geen SELF_URL ingesteld, sla ping over")
                send_telegram("💓 Bot nog online - keep-alive actief (geen SELF_URL, zet RENDER_EXTERNAL_URL / SELF_URL in Render)")
        except Exception as e:
            print(f"Keep-alive error: {e}")
            send_telegram(f"⚠️ Keep-alive ping mislukt: {e}")
        # 5 minuten = 300 seconden
        time.sleep(300)

# Start keep-alive thread 1x bij opstarten
threading.Thread(target=keep_alive, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
