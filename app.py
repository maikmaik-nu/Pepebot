import os
import time
import hmac
import hashlib
import threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# ================= CONFIG =================
BASE_URL = "https://api.bybit.eu"
CATEGORY = "spot"
SYMBOL = "PEPEUSDC"
COIN = "USDC"
BASE_COIN = "PEPE"

API_KEY = os.environ.get("BYBIT_API_KEY", "")
API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# --- TRADE INSTELLINGEN ---
TRADE_AMOUNT_USDC = 20.0      # hoeveel USDC per trade
TAKE_PROFIT_PCT = 3.0         # verkoop bij +3%
STOP_LOSS_PCT = -2.0          # verkoop bij -2%
CHECK_EVERY_SEC = 60          # elke 60 sec checken
MIN_USDC_TO_TRADE = 10.0

bot_status = {
    "draait": True,
    "laatste_prijs": 0.0,
    "usdc_saldo": 0.0,
    "pepe_saldo": 0.0,
    "in_positie": False,
    "koop_prijs": 0.0,
    "winst_pct": 0.0,
    "laatste_actie": "Gestart, wachten op saldo",
    "aantal_trades": 0
}

# ================= TELEGRAM =================
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print(f"Telegram fout: {e}")

# ================= 【entity-BYBIT¦canonical_name=Bybit】 =================
def _sign(params):
    timestamp = str(int(time.time() * 1000))
    params["api_key"] = API_KEY
    params["timestamp"] = timestamp
    params["recv_window"] = "5000"
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    sign = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query, sign

def bybit_get(path, params=None):
    if params is None:
        params = {}
    query, sign = _sign(params)
    url = f"{BASE_URL}{path}?{query}&sign={sign}"
    r = requests.get(url, timeout=15)
    return r.json()

def bybit_post(path, params=None):
    if params is None:
        params = {}
    query, sign = _sign(params)
    url = f"{BASE_URL}{path}?{query}&sign={sign}"
    r = requests.post(url, timeout=15)
    return r.json()

def get_balance(coin_name):
    try:
        data = bybit_get("/v5/account/wallet-balance", {"accountType": "SPOT", "coin": coin_name})
        if data.get("retCode") != 0:
            return 0.0, data
        coins = data["result"]["list"][0]["coin"]
        for c in coins:
            if c["coin"] == coin_name:
                return float(c["walletBalance"]), data
        return 0.0, data
    except Exception as e:
        return 0.0, {"error": str(e)}

def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
        data = r.json()
        return float(data["result"]["list"][0]["lastPrice"])
    except Exception:
        return 0.0

def plaats_order(side, qty):
    try:
        params = {
            "category": CATEGORY,
            "symbol": SYMBOL,
            "side": side,
            "orderType": "Market",
            "qty": str(qty)
        }
        data = bybit_post("/v5/order/create", params)
        print(f"Order {side} {qty}: {data}")
        return data
    except Exception as e:
        return {"error": str(e)}

# ================= TRADING LOGICA =================
def bot_loop():
    global bot_status
    time.sleep(5)
    send_telegram("Pepebot-3 is gestart - PEPE/USDC - volledige bot")
    print("Bot loop gestart")

    while True:
        try:
            if not bot_status["draait"]:
                time.sleep(10)
                continue

            prijs = get_price()
            usdc, _ = get_balance(COIN)
            pepe, _ = get_balance(BASE_COIN)

            bot_status["laatste_prijs"] = prijs
            bot_status["usdc_saldo"] = usdc
            bot_status["pepe_saldo"] = pepe

            # --- GEEN POSITIE: KIJK OF WE KUNNEN KOPEN ---
            if not bot_status["in_positie"]:
                if usdc >= MIN_USDC_TO_TRADE and prijs > 0:
                    qty = round(TRADE_AMOUNT_USDC / prijs)
                    if qty > 0:
                        res = plaats_order("Buy", qty)
                        if res.get("retCode") == 0:
                            bot_status["in_positie"] = True
                            bot_status["koop_prijs"] = prijs
                            bot_status["aantal_trades"] += 1
                            bot_status["laatste_actie"] = f"GEKOCHT {qty} PEPE voor {prijs}"
                            send_telegram(f"GEKOCHT: {qty} PEPE voor {prijs} - USDC saldo was {usdc}")
                        else:
                            bot_status["laatste_actie"] = f"Koop mislukt: {res.get('retMsg')}"
                else:
                    bot_status["laatste_actie"] = f"Te weinig USDC om te kopen: {usdc}"

            # --- WEL POSITIE: CHECK WINST / VERLIES ---
            else:
                if bot_status["koop_prijs"] > 0 and prijs > 0:
                    winst = ((prijs - bot_status["koop_prijs"]) / bot_status["koop_prijs"]) * 100
                    bot_status["winst_pct"] = round(winst, 2)

                    # Take profit of stop loss?
                    if winst >= TAKE_PROFIT_PCT or winst <= STOP_LOSS_PCT:
                        res = plaats_order("Sell", int(pepe))
                        if res.get("retCode") == 0:
                            reden = "Take Profit" if winst >= TAKE_PROFIT_PCT else "Stop Loss"
                            bot_status["laatste_actie"] = f"VERKOCHT ({reden}) - winst {winst:.2f}%"
                            send_telegram(f"VERKOCHT ({reden}): {pepe} PEPE - winst {winst:.2f}% - prijs {prijs}")
                            bot_status["in_positie"] = False
                            bot_status["koop_prijs"] = 0.0
                            bot_status["winst_pct"] = 0.0
                        else:
                            bot_status["laatste_actie"] = f"Verkoop mislukt: {res.get('retMsg')}"
                    else:
                        bot_status["laatste_actie"] = f"In positie - winst nu {winst:.2f}%"

            print(f"Prijs {prijs} - USDC {usdc} - PEPE {pepe} - {bot_status['laatste_actie']}")
            time.sleep(CHECK_EVERY_SEC)

        except Exception as e:
            print(f"Bot loop fout: {e}")
            bot_status["laatste_actie"] = f"Fout in loop: {e}"
            time.sleep(30)

# Start de bot in de achtergrond
threading.Thread(target=bot_loop, daemon=True).start()

# ================= WEB ROUTES =================
@app.route("/")
def home():
    saldo, _ = get_balance(COIN)
    prijs = get_price()
    return jsonify({
        "api_key_set": bool(API_KEY),
        "symbol": SYMBOL,
        "usdc": saldo,
        "prijs": prijs,
        "bot": bot_status
    })

@app.route("/test-key")
def test_key():
    saldo, raw = get_balance(COIN)
    return jsonify({
        "api_key_set": bool(API_KEY),
        "symbol": SYMBOL,
        "usdc_spot": saldo,
        "bybit_raw": raw
    })

@app.route("/status")
def status():
    return jsonify(bot_status)

@app.route("/stop")
def stop_bot():
    bot_status["draait"] = False
    bot_status["laatste_actie"] = "Bot gestopt via /stop"
    send_telegram("Bot gestopt via /stop")
    return jsonify({"ok": True, "bot": bot_status})

@app.route("/start")
def start_bot():
    bot_status["draait"] = True
    bot_status["laatste_actie"] = "Bot gestart via /start"
    send_telegram("Bot gestart via /start")
    return jsonify({"ok": True, "bot": bot_status})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
