import os
import time
import hmac
import hashlib
import threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# ===== CONFIG =====
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")

# Bot settings
TRADE_USDT = 10  # hoeveel USDT per trade
CHECK_EVERY_SEC = 60

# ===== BYBIT HELPERS =====
def bybit_request(path, params=None, method="GET"):
    if params is None:
        params = {}
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    params["api_key"] = API_KEY
    params["timestamp"] = timestamp
    params["recv_window"] = recv_window

    # signature
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    sign = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    params["sign"] = sign

    url = BASE_URL + path
    headers = {"X-BAPI-API-KEY": API_KEY}
    try:
        if method == "GET":
            r = requests.get(url, params=params, headers=headers, timeout=10)
        else:
            r = requests.post(url, json=params, headers=headers, timeout=10)
        return r.status_code, r.json()
    except Exception as e:
        return 0, {"error": str(e)}

def get_balance():
    """Telt UNIFIED + SPOT samen, dus je geld wordt altijd gevonden."""
    total_usdt = 0.0
    total_pepe = 0.0
    last_raw = None
    last_retcode = None

    for account_type in ["UNIFIED", "SPOT"]:
        status, data = bybit_request("/v5/account/wallet-balance", {
            "accountType": account_type,
            "coin": "USDT,PEPE"
        })
        last_raw = data
        if isinstance(data, dict):
            last_retcode = data.get("retCode")

        try:
            lst = data.get("result", {}).get("list", [])
            for item in lst:
                for coin in item.get("balance", []):
                    if coin.get("coin") == "USDT":
                        total_usdt += float(coin.get("walletBalance") or 0)
                    if coin.get("coin") == "PEPE":
                        total_pepe += float(coin.get("walletBalance") or 0)
        except:
            pass

    return total_usdt, total_pepe, last_retcode, last_raw

def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={
            "category": CATEGORY,
            "symbol": SYMBOL
        }, timeout=10)
        data = r.json()
        return float(data["result"]["list"][0]["lastPrice"])
    except:
        return 0

def place_order(side, qty):
    timestamp = str(int(time.time() * 1000))
    params = {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": side,
        "orderType": "Market",
        "qty": str(qty),
        "api_key": API_KEY,
        "timestamp": timestamp,
        "recv_window": "5000"
    }
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    sign = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    params["sign"] = sign
    headers = {"X-BAPI-API-KEY": API_KEY}
    try:
        r = requests.post(f"{BASE_URL}/v5/order/create", json=params, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

# ===== BOT LOOP =====
in_position = False

def bot_loop():
    global in_position
    while True:
        try:
            usdt, pepe, retcode, _ = get_balance()
            price = get_price()
            print(f"CHECK: USDT={usdt} PEPE={pepe} price={price} in_position={in_position} retCode={retcode}", flush=True)

            if retcode != 0:
                print("Bot stopt: geen saldo / geen rechten", flush=True)
                time.sleep(CHECK_EVERY_SEC)
                continue

            # Simpele logica: koop als we geen positie hebben en genoeg USDT
            if not in_position and usdt >= TRADE_USDT and price > 0:
                qty = int((TRADE_USDT / price))
                if qty > 0:
                    res = place_order("Buy", qty)
                    print(f"BUY: {res}", flush=True)
                    in_position = True

            # Verkoop als we positie hebben en PEPE hebben
            elif in_position and pepe > 0:
                res = place_order("Sell", int(pepe))
                print(f"SELL: {res}", flush=True)
                in_position = False

        except Exception as e:
            print(f"LOOP ERROR: {e}", flush=True)

        time.sleep(CHECK_EVERY_SEC)

# ===== ROUTES =====
@app.route("/")
def home():
    usdt, pepe, retcode, _ = get_balance()
    return jsonify({
        "status": "live",
        "symbol": SYMBOL,
        "USDT": usdt,
        "PEPE": pepe,
        "in_position": in_position,
        "bybit_retCode": retcode
    })

@app.route("/test-key")
def test_key():
    usdt, pepe, retcode, raw = get_balance()
    return jsonify({
        "accountType_used": "UNIFIED+SPOT",
        "api_key_set": bool(API_KEY),
        "balances": {
            "PEPE": {"wallet": str(pepe)},
            "USDT": {"wallet": str(usdt)}
        },
        "base_url": BASE_URL,
        "bybit_retCode": retcode,
        "bybit_retMsg": "success" if retcode == 0 else "check permissions",
        "http_status": 200,
        "raw": raw
    })

@app.route("/balance")
def balance():
    usdt, pepe, retcode, _ = get_balance()
    return jsonify({"USDT": usdt, "PEPE": pepe, "retCode": retcode})

# Start bot loop 1x
threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
