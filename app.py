import os
import math
import time
import threading
import requests
from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP

app = Flask(__name__)

# --- Instellingen ---
SYMBOOL = "PEPEUSDT"
INTERVAL = "15"
STOP_LOSS_PCT = 3.0

BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

session = HTTP(
    testnet=False,
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET
)

bot_state = {
    "in_positie": False,
    "signaal": "geen",
    "laatste_candle": 0,
    "entry_prijs": 0.0,
    "actief": True
}

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
    for acc in ["SPOT", "UNIFIED", "FUND", "CONTRACT"]:
        try:
            resp = session.get_wallet_balance(accountType=acc, coin=coin)
            coins = resp["result"]["list"][0]["coin"]
            for c in coins:
                if c["coin"] == coin:
                    val = float(c.get("walletBalance") or 0)
                    print(f"Balance {coin} in {acc}: {val}")
                    if val > 0:
                        return val
        except Exception as e:
            print(f"Balance error {acc}: {e}")
    return 0.0

def get_prijs():
    resp = session.get_tickers(category="spot", symbol=SYMBOOL)
    return float(resp["result"]["list"][0]["lastPrice"])

def get_klines():
    resp = session.get_kline(category="spot", symbol=SYMBOOL, interval=INTERVAL, limit=100)
    # Bybit geeft nieuwste eerst, draai om: oud -> nieuw
    klines = list(reversed(resp["result"]["list"]))
    return klines

def supertrend_richting(klines, atr_period=10, multiplier=3.0):
    # Simpele, correcte Supertrend-berekening
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]

    # True Range + ATR
    tr_list = []
    for i in range(1, len(klines)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        tr_list.append(tr)

    atr = sum(tr_list[-atr_period:]) / atr_period

    # Laatste 2 candles bepalen de richting
    # Supertrend up als close > (hl2 - multiplier * atr)
    resultaten = []
    for i in range(len(klines) - 2, len(klines)):
        hl2 = (highs[i] + lows[i]) / 2
        upper = hl2 + multiplier * atr
        lower = hl2 - multiplier * atr
        if closes[i] > upper:
            resultaten.append("down")
        elif closes[i] < lower:
            resultaten.append("up")
        else:
            resultaten.append(resultaten[-1] if resultaten else "down")
    return resultaten

def do_buy():
    try:
        usdt_free = get_free_balance("USDT")
        print(f"BUY signaal - USDT free: {usdt_free}")
        if usdt_free < 5:
            msg = f"BUY genegeerd: te weinig USDT ({usdt_free})"
            send_telegram(msg)
            return msg

        prijs = get_prijs()
        bedrag = usdt_free * 0.99
        qty = math.floor((bedrag / prijs) / 1000) * 1000
        if qty <= 0:
            msg = "BUY genegeerd: Qty te klein"
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
        bot_state["in_positie"] = True
        bot_state["entry_prijs"] = prijs
        msg = f"BUY uitgevoerd: {qty} PEPE voor ~{bedrag:.2f} USDT @ {prijs}"
        send_telegram(msg)
        return msg
    except Exception as e:
        msg = f"Error bij buy: {e}"
        print(msg)
        send_telegram(msg)
        return msg

def do_sell(reden="signaal"):
    try:
        pepe_free = get_free_balance("PEPE")
        print(f"SELL signaal - PEPE free: {pepe_free} reden: {reden}")
        if pepe_free <= 0:
            msg = f"SELL genegeerd: geen PEPE ({pepe_free})"
            send_telegram(msg)
            return msg

        qty = math.floor(float(pepe_free) / 1000) * 1000
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
        bot_state["in_positie"] = False
        bot_state["entry_prijs"] = 0.0
        msg = f"SELL uitgevoerd: {qty} PEPE verkocht ({reden})"
        send_telegram(msg)
        return msg
    except Exception as e:
        msg = f"Error bij sell: {e}"
        print(msg)
        send_telegram(msg)
        return msg

def bot_loop():
    time.sleep(5)
    # Herstel positie na herstart
    try:
        if get_free_balance("PEPE") > 1000:
            bot_state["in_positie"] = True
            bot_state["entry_prijs"] = get_prijs()
    except Exception as e:
        print(f"Herstel fout: {e}")

    send_telegram("🤖 Bot gestart - Rente-op-rente AAN - Stop-loss -3% AAN")

    while True:
        try:
            if not bot_state["actief"]:
                time.sleep(10)
                continue

            klines = get_klines()
            if len(klines) < 20:
                time.sleep(30)
                continue

            bot_state["laatste_candle"] = int(klines[-1][0])
            richting = supertrend_richting(klines)

            # Stop-loss check
            if bot_state["in_positie"] and bot_state["entry_prijs"] > 0:
                prijs = get_prijs()
                verlies = (bot_state["entry_prijs"] - prijs) / bot_state["entry_prijs"] * 100
                if verlies >= STOP_LOSS_PCT:
                    bot_state["signaal"] = "sell (stop-loss)"
                    do_sell(f"stop-loss -{verlies:.2f}%")
                    time.sleep(30)
                    continue

            # Buy: flip van down -> up
            if len(richting) >= 2 and richting[-2] == "down" and richting[-1] == "up":
                bot_state["signaal"] = "buy"
                if not bot_state["in_positie"]:
                    do_buy()
            # Sell: flip van up -> down
            elif len(richting) >= 2 and richting[-2] == "up" and richting[-1] == "down":
                bot_state["signaal"] = "sell"
                if bot_state["in_positie"]:
                    do_sell("supertrend flip")
            else:
                bot_state["signaal"] = richting[-1]

            time.sleep(60)
        except Exception as e:
            print(f"Loop fout: {e}")
            time.sleep(30)

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    action = str(data.get("action", "")).lower()
    print(f"Webhook ontvangen: {data}")
    if action == "buy":
        msg = do_buy()
    elif action == "sell":
        msg = do_sell("webhook")
    else:
        msg = f"Onbekende action: {action}"
    return jsonify({"status": msg}), 200

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({
        "status": "ok",
        "signaal": bot_state["signaal"],
        "in_positie": bot_state["in_positie"],
        "laatste_candle": bot_state["laatste_candle"],
        "entry_prijs": bot_state["entry_prijs"]
    }), 200

@app.route("/start", methods=["GET", "POST"])
def start():
    bot_state["actief"] = True
    send_telegram("▶️ Bot gestart")
    return "Bot gestart", 200

@app.route("/stop", methods=["GET", "POST"])
def stop():
    bot_state["actief"] = False
    send_telegram("⏸️ Bot gestopt")
    return "Bot gestopt", 200

@app.route("/", methods=["GET"])
def home():
    return "Bot draait", 200

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
