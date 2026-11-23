import os
import time
import hmac
import hashlib
import threading
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# --- Config ---
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
INTERVAL = "15"
CHECK_EVERY_SEC = 60

API_KEY = os.environ.get("BYBIT_API_KEY", "")
API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

_bot_started = False
_last_telegram = 0

# --- Telegram ---
def send_telegram(msg):
    global _last_telegram
    now = time.time()
    # max 1x per uur voor normale meldingen, behalve start/stop
    if "Bot gestart" not in msg and "FOUT" not in msg:
        if now - _last_telegram < 3600:
            return
    _last_telegram = now
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"Telegram (niet ingesteld): {msg}", flush=True)
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print(f"Telegram fail: {e}", flush=True)

# --- Bybit sign ---
def sign(timestamp, api_key, recv_window, query_string):
    # Bybit V5: timestamp + api_key + recvWindow + query_string
    to_sign = f"{timestamp}{api_key}{recv_window}{query_string}"
    return hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()

def bybit_get(path, params):
    if not API_KEY or not API_SECRET:
        return {"retCode": -1, "retMsg": "Geen API key ingesteld"}
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    # params sorteren zoals Bybit het wil
    query = "&".join([f"{k}={params[k]}" for k in sorted(params.keys())])
    signature = sign(timestamp, API_KEY, recv_window, query)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json"
    }
    try:
        r = requests.get(BASE_URL + path + "?" + query, headers=headers, timeout=15)
        try:
            return r.json()
        except:
            return {"retCode": -1, "retMsg": f"Lege body, HTTP {r.status_code}"}
    except Exception as e:
        return {"retCode": -1, "retMsg": str(e)}

def get_balance():
    # Alleen SPOT, coin per coin - geen USDT,PEPE meer
    result = {"USDC": 0.0, "USDT": 0.0, "PEPE": 0.0, "raw": None, "error": None}
    for coin in ["USDC", "USDT", "PEPE"]:
        data = bybit_get("/v5/account/wallet-balance", {"accountType": "SPOT", "coin": coin})
        result["raw"] = data
        if data.get("retCode") != 0:
            result["error"] = f"Balance fail: retCode={data.get('retCode')} retMsg={data.get('retMsg')} full={data}"
            print(result["error"], flush=True)
            return result
        # Fix voor die 'list' fout: result kan list of dict zijn
        res = data.get("result", {})
        if isinstance(res, list):
            if len(res) > 0:
                res = res[0]
            else:
                res = {}
        lst = res.get("list", [])
        if isinstance(lst, list) and len(lst) > 0:
            first = lst[0]
            coins = first.get("coin", [])
            for c in coins:
                if isinstance(c, dict) and c.get("coin") == coin:
                    try:
                        result[coin] = float(c.get("walletBalance", 0) or 0)
                    except:
                        result[coin] = 0.0
        elif isinstance(res, dict):
            # fallback als Bybit een andere vorm teruggeeft
            coins = res.get("coin", [])
            if isinstance(coins, list):
                for c in coins:
                    if isinstance(c, dict) and c.get("coin") == coin:
                        try:
                            result[coin] = float(c.get("walletBalance", 0) or 0)
                        except:
                            result[coin] = 0.0
    return result

def get_price():
    try:
        r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        j = r.json()
        lst = j.get("result", {}).get("list", [])
        if lst:
            return float(lst[0].get("lastPrice", 0))
    except Exception as e:
        print(f"Prijs fail: {e}", flush=True)
    return 0.0

def get_qty_step():
    # PEPE qtyStep ophalen, zodat we niet met int() afknippen
    try:
        r = requests.get(BASE_URL + "/v5/market/instruments-info", params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        j = r.json()
        lst = j.get("result", {}).get("list", [])
        if lst:
            lot = lst[0].get("lotSizeFilter", {})
            return float(lot.get("qtyStep", 1))
    except:
        pass
    return 1.0

def round_qty(qty, step):
    if step <= 0:
        return qty
    # netjes afronden op qtyStep, niet int() afknippen
    return int(qty / step) * step

# --- Bot loop ---
def bot_loop():
    print("Bot loop gestart", flush=True)
    send_telegram("Bot gestart, saldo check loopt")
    while True:
        try:
            bal = get_balance()
            if bal.get("error"):
                print("Geen saldo, probeer over 60 sec opnieuw - geen stop", flush=True)
                time.sleep(CHECK_EVERY_SEC)
                continue

            usdc = bal["USDC"]
            usdt = bal["USDT"]
            pepe = bal["PEPE"]
            # Gebruik welke er staat - USDC eerst, anders USDT
            cash = usdc if usdc > 0 else usdt
            cash_coin = "USDC" if usdc > 0 else "USDT"
            price = get_price()

            print(f"Prijs {price} - USDC {usdc} - USDT {usdt} - PEPE {pepe} - Gebruikt: {cash_coin}", flush=True)

            if price <= 0:
                time.sleep(CHECK_EVERY_SEC)
                continue

            # Hier komt je UT Bot + Supertrend logica
            # Voor nu: alleen loggen, geen trade, tot saldo goed is
            if cash < 5:
                print(f"Te weinig {cash_coin} om te kopen: {cash}", flush=True)

            # TODO: als UT + Supertrend allebei omhoog staan EN cash >= 5:
            # qty = round_qty((cash * 0.999) / price, get_qty_step())
            # dan pas kopen via /v5/order/create

        except Exception as e:
            print(f"Loop fout: {e}", flush=True)
        time.sleep(CHECK_EVERY_SEC)

# --- Routes ---
@app.route("/")
def home():
    return "Bot leeft"

@app.route("/ping")
def ping():
    return jsonify({"ok": True})

@app.route("/test-key")
def test_key():
    bal = get_balance()
    return jsonify({
        "api_key_set": bool(API_KEY),
        "usdc": bal["USDC"],
        "usdt": bal["USDT"],
        "pepe": bal["PEPE"],
        "prijs": get_price(),
        "error": bal.get("error"),
        "base": BASE_URL
    })

@app.route("/status")
def status():
    bal = get_balance()
    return jsonify({
        "usdc": bal["USDC"],
        "usdt": bal["USDT"],
        "pepe": bal["PEPE"],
        "prijs": get_price(),
        "error": bal.get("error")
    })

# Start loop maar 1x, ook met Gunicorn
if not _bot_started:
    _bot_started = True
    threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
