import os
import time
import hmac
import hashlib
import json
import threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# ================= CONFIG =================
BASE_URL = os.getenv("BASE_URL", "https://api.bybit.eu")
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SYMBOL = "PEPEUSDC"
# GEEN stop-loss, GEEN Supertrend
# Rente-op-rente: gebruik 98% van je hele USDC saldo

STATUS = {
    "in_positie": False,
    "koop_prijs": 0.0,
    "laatste_signaal": "geen",
    "laatste_check": "",
    "laatste_fout": ""
}

# ================= TELEGRAM =================
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram niet ingesteld: " + msg, flush=True)
        return
    try:
        url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print("Telegram fout: " + str(e), flush=True)

# ================= BYBIT =================
def get_price():
    try:
        url = BASE_URL + "/v5/market/tickers"
        r = requests.get(url, params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        data = r.json()
        lst = data.get("result", {}).get("list", [])
        if not lst:
            print("Prijs fout: lege list, response=" + str(data), flush=True)
            return 0.0, data
        return float(lst[0].get("lastPrice", 0)), data
    except Exception as e:
        print("Prijs fout: " + str(e), flush=True)
        return 0.0, {"error": str(e)}

def get_klines(limit=100):
    try:
        url = BASE_URL + "/v5/market/kline"
        r = requests.get(url, params={"category": "spot", "symbol": SYMBOL, "interval": "15", "limit": str(limit)}, timeout=10)
        data = r.json()
        lst = list(reversed(data.get("result", {}).get("list", [])))
        candles = []
        for c in lst:
            candles.append({"open": float(c[1]), "high": float(c[2]), "low": float(c[3]), "close": float(c[4])})
        return candles
    except Exception as e:
        print("Klines fout: " + str(e), flush=True)
        return []

def signed_headers(query_string):
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    sign_str = timestamp + API_KEY + recv_window + query_string
    sig = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    return {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": timestamp, "X-BAPI-RECV-WINDOW": recv_window, "X-BAPI-SIGN": sig}

def get_balance():
    try:
        query = "accountType=SPOT"
        headers = signed_headers(query)
        url = BASE_URL + "/v5/account/wallet-balance"
        r = requests.get(url, params={"accountType": "SPOT"}, headers=headers, timeout=10)
        data = r.json()
        lst = data.get("result", {}).get("list", [])
        usdc = 0.0
        pepe = 0.0
        if lst:
            for c in lst[0].get("coin", []):
                if c.get("coin") == "USDC":
                    usdc = float(c.get("walletBalance", 0))
                if c.get("coin") == "PEPE":
                    pepe = float(c.get("walletBalance", 0))
        return usdc, pepe, data
    except Exception as e:
        print("Balance fail: " + str(e), flush=True)
        return 0.0, 0.0, {"error": str(e)}

def place_order(side, qty):
    try:
        timestamp = str(int(time.time() * 1000))
        recv_window = "5000"
        body = {"category": "spot", "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(int(qty))}
        body_str = json.dumps(body)
        sign_str = timestamp + API_KEY + recv_window + body_str
        sig = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
        headers = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": timestamp, "X-BAPI-RECV-WINDOW": recv_window, "X-BAPI-SIGN": sig, "Content-Type": "application/json"}
        r = requests.post(BASE_URL + "/v5/order/create", headers=headers, data=body_str, timeout=10)
        print("Order " + side + " response: " + r.text, flush=True)
        return r.json()
    except Exception as e:
        print("Order fail: " + str(e), flush=True)
        return {"error": str(e)}

# ================= UT BOT ALLEEN - GEEN SUPERTREND =================
def calc_atr(candles, period):
    if len(candles) < period + 1:
        return 0.0
    tr_list = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)
    if len(tr_list) < period:
        return 0.0
    return sum(tr_list[-period:]) / period

def calc_ut_bot(candles):
    # UT Bot 4, 14 - key=1, atr_period=14, op 15min
    if len(candles) < 20:
        return "neutraal"
    atr = calc_atr(candles, 14)
    if atr == 0:
        return "neutraal"
    last = candles[-1]
    prev = candles[-2]
    # Buy als prijs omhoog breekt, Sell als prijs omlaag breekt
    if last["close"] > prev["close"] + (1.0 * atr * 0.5):
        return "buy"
    if last["close"] < prev["close"] - (1.0 * atr * 0.5):
        return "sell"
    return "hold"

# ================= BOT LOOP =================
def bot_loop():
    print("Bot loop gestart - UT Bot ALLEEN - GEEN Supertrend - GEEN stop-loss", flush=True)
    send_telegram("Bot gestart - UT Bot ALLEEN - GEEN Supertrend - GEEN stop-loss - Rente-op-rente AAN - Start met 100")
    while True:
        try:
            price, _ = get_price()
            usdc, pepe, _ = get_balance()
            candles = get_klines(100)
            ut = calc_ut_bot(candles)

            log = "Check: Geld " + str(usdc) + " PEPE " + str(pepe) + " prijs " + str(price) + " UT:" + ut
            print(log, flush=True)
            STATUS["laatste_check"] = log

            if price == 0:
                print("Niet traden: prijs 0", flush=True)
            else:
                # Als we al PEPE hebben, zitten we in positie
                if pepe > 0 and not STATUS["in_positie"]:
                    STATUS["in_positie"] = True
                    if STATUS["koop_prijs"] == 0:
                        STATUS["koop_prijs"] = price

                # KOOP: UT = buy, nog geen positie, genoeg USDC
                if not STATUS["in_positie"] and ut == "buy":
                    # Rente-op-rente: 98% van alles wat je hebt
                    bedrag = usdc * 0.98
                    if bedrag >= 5:
                        qty = bedrag / price
                        if qty > 0:
                            print("KOOP: " + str(int(qty)) + " PEPE voor " + str(round(bedrag, 2)) + " USDC", flush=True)
                            res = place_order("Buy", qty)
                            if res.get("retCode") == 0:
                                STATUS["in_positie"] = True
                                STATUS["koop_prijs"] = price
                                STATUS["laatste_signaal"] = "buy"
                                send_telegram("GEKOCHT: " + str(int(qty)) + " PEPE voor " + str(round(bedrag, 2)) + " USDC op " + str(price))
                            else:
                                print("Koop mislukt: " + str(res), flush=True)

                # VERKOOP: UT = sell, wel in positie
                if STATUS["in_positie"] and ut == "sell" and pepe > 0:
                    print("VERKOOP: " + str(int(pepe)) + " PEPE", flush=True)
                    res = place_order("Sell", pepe)
                    if res.get("retCode") == 0:
                        STATUS["in_positie"] = False
                        STATUS["koop_prijs"] = 0.0
                        STATUS["laatste_signaal"] = "sell"
                        send_telegram("VERKOCHT: " + str(int(pepe)) + " PEPE op " + str(price))

                # GEEN stop-loss hier - expres weggelaten

        except Exception as e:
            print("Loop fout: " + str(e), flush=True)
            STATUS["laatste_fout"] = str(e)
        time.sleep(60)

# ================= ROUTES =================
@app.route("/")
def home():
    return "Pepebot-3 draait - UT Bot ALLEEN!"

@app.route("/test-key")
def test_key():
    try:
        price, price_data = get_price()
        usdc, pepe, bal_data = get_balance()
        candles = get_klines(20)
        ut = calc_ut_bot(candles) if candles else "geen data"
        return jsonify({
            "BASE_URL": BASE_URL,
            "KEY_INGESTELD": bool(API_KEY),
            "SECRET_INGESTELD": bool(API_SECRET),
            "TELEGRAM_INGESTELD": bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID),
            "SYMBOL": SYMBOL,
            "PEPEUSDC_prijs": price,
            "USDC_saldo": usdc,
            "PEPE_saldo": pepe,
            "STATUS": STATUS,
            "UT_BOT": ut,
            "SUPERTREND": "UIT - expres verwijderd",
            "STOP_LOSS": "UIT - expres verwijderd",
            "prijs_raw": price_data,
            "saldo_raw": bal_data
        })
    except Exception as e:
        return jsonify({"FOUT": str(e)})

@app.route("/status")
def status():
    return jsonify(STATUS)

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
