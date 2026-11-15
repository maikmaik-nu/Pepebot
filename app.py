
# Pepebot - FINAL - 1x goed
# GEEN STOP-LOSS - Start met 100 - Rente-op-rente AAN
# Alleen UT Bot / Supertrend voor BUY / SELL
# Bot: pepebot-3.onrender.com

import os, time, requests
from flask import Flask, request, jsonify

app = Flask(__name__)

BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"

in_positie = False

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram niet ingesteld")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def get_price():
    try:
        r = requests.get("https://api.bybit.com/v5/market/tickers",
                          params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
        return float(r["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print(f"Prijs error: {e}")
        return 0

def get_free_usdt():
    try:
        import hmac, hashlib
        ts = str(int(time.time()*1000))
        params = f"accountType=UNIFIED&coin=USDT&api_key={BYBIT_API_KEY}&recv_window=5000&timestamp={ts}"
        sign = hmac.new(BYBIT_API_SECRET.encode(), params.encode(), hashlib.sha256).hexdigest()
        url = f"https://api.bybit.com/v5/account/wallet-balance?accountType=UNIFIED&coin=USDT&timestamp={ts}&api_key={BYBIT_API_KEY}&recv_window=5000&sign={sign}"
        r = requests.get(url, timeout=10).json()
        coins = r["result"]["list"][0]["coin"]
        for c in coins:
            if c["coin"] == "USDT":
                return float(c.get("walletBalance") or 0)
        return 0.0
    except Exception as e:
        print(f"Saldo error: {e}")
        return 0.0

def get_pepe_balance():
    try:
        import hmac, hashlib
        ts = str(int(time.time()*1000))
        params = f"accountType=UNIFIED&coin=PEPE&api_key={BYBIT_API_KEY}&recv_window=5000&timestamp={ts}"
        sign = hmac.new(BYBIT_API_SECRET.encode(), params.encode(), hashlib.sha256).hexdigest()
        url = f"https://api.bybit.com/v5/account/wallet-balance?accountType=UNIFIED&coin=PEPE&timestamp={ts}&api_key={BYBIT_API_KEY}&recv_window=5000&sign={sign}"
        r = requests.get(url, timeout=10).json()
        coins = r["result"]["list"][0]["coin"]
        for c in coins:
            if c["coin"] == "PEPE":
                return float(c.get("walletBalance") or 0)
        return 0.0
    except Exception as e:
        print(f"PEPE saldo error: {e}")
        return 0.0

def do_buy():
    global in_positie
    usdt = get_free_usdt()
    print(f"BUY signaal - USDT free: {usdt}")
    if usdt < 10:
        send_telegram(f"❌ BUY gegenereerd: te weinig USDT ({usdt:.2f})")
        return
    # RENTE-OP-RENTE: gebruik alles wat vrij is, min 2% voor fees
    bedrag = usdt * 0.98
    prijs = get_price()
    if prijs <= 0:
        send_telegram("❌ BUY mislukt: geen prijs")
        return
    qty = int((bedrag / prijs) // 1000 * 1000)
    print(f"BUY: inzetten {bedrag:.2f} USDT, qty {qty}, prijs {prijs}")
    send_telegram(f"🟢 BUY {SYMBOL}\nBedrag: {bedrag:.2f} USDT\nQty: {qty}\nPrijs: {prijs}\nRente-op-rente AAN - GEEN stop-loss")
    in_positie = True

def do_sell():
    global in_positie
    pepe = get_pepe_balance()
    print(f"SELL signaal - PEPE: {pepe}")
    if pepe < 1000:
        send_telegram(f"❌ SELL gegenereerd: te weinig PEPE ({pepe})")
        return
    qty = int(pepe // 1000 * 1000)
    print(f"SELL: qty {qty}")
    send_telegram(f"🔴 SELL {SYMBOL}\nQty: {qty}\nGEEN stop-loss - alleen UT Bot signaal")
    in_positie = False

# GEEN STOP-LOSS FUNCTIE IN DIT SCRIPT - BEWUST WEGGELATEN

@app.route("/")
def home():
    return "Pepebot FINAL - GEEN stop-loss - Rente-op-rente AAN"

@app.route("/ping")
def ping():
    return jsonify({"status": "Pong", "in_positie": in_positie, "stop_loss": "UIT"})

@app.route("/status")
def status():
    return jsonify({"usdt": get_free_usdt(), "prijs": get_price(), "in_positie": in_positie, "stop_loss": "UIT"})

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    signaal = str(data.get("signaal", data.get("action", ""))).upper()
    print(f"Webhook: {signaal} - In positie: {in_positie}")
    if "BUY" in signaal and not in_positie:
        do_buy()
    elif "SELL" in signaal and in_positie:
        do_sell()
    return jsonify({"ok": True, "signaal": signaal})

@app.route("/telegram", methods=["POST"])
def telegram():
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("message", {}).get("text", "")
    if text == "/ping":
        send_telegram("PONG - Ik ben LIVE - GEEN stop-loss")
    elif text == "/status":
        send_telegram(f"Status: USDT {get_free_usdt():.2f} - In positie: {in_positie} - Stop-loss: UIT")
    return jsonify({"ok": True})

if __name__ == "__main__":
    send_telegram("🤖 Bot gestart - Rente-op-rente AAN - GEEN stop-loss - Start met 100")
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
