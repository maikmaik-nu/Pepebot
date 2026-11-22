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
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")

TRADE_USDT = 10
CHECK_EVERY_SEC = 60
RECV_WINDOW = "5000"

# ===== CORRECTE V5 SIGNING =====
def _headers(timestamp, payload_str=""):
    msg = timestamp + API_KEY + RECV_WINDOW + payload_str
    sign = hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json"
    }

def bybit_get(path, params=None):
    if params is None:
        params = {}
    timestamp = str(int(time.time() * 1000))
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    headers = _headers(timestamp, query)
    try:
        r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
        return r.status_code, r.json()
    except Exception as e:
        return 0, {"error": str(e)}

def bybit_post(path, body):
    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(body, separators=(',', ':'))
    headers = _headers(timestamp, body_str)
    try:
        r = requests.post(BASE_URL + path, data=body_str, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

# ===== BALANCE =====
def get_balance():
    total_usdt = 0.0
    total_pepe = 0.0
    last_retcode = None
    last_raw = None
    for account_type in ["UNIFIED", "SPOT"]:
        status, data = bybit_get("/v5/account/wallet-balance", {
            "accountType": account_type,
            "coin": "USDT,PEPE"
        })
        last_raw = data
        if isinstance(data, dict):
            last_retcode = data.get("retCode")
        try:
            for item in data.get("result", {}).get("list", []):
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
    body = {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": side,
        "orderType": "Market",
        "qty": str(qty)
    }
    return bybit_post("/v5/order/create", body)

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
            if not in_position and usdt >= TRADE_USDT and price > 0:
                qty = int(TRADE_USDT / price)
                if qty > 0:
                    res = place_order("Buy", qty)
                    print(f"BUY: {res}", flush=True)
                    in_position = True
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
    return jsonify({"status": "live", "symbol": SYMBOL, "USDT": usdt, "PEPE": pepe, "in_position": in_position, "bybit_retCode": retcode})

@app.route("/test-key")
def test_key():
    usdt, pepe, retcode, raw = get_balance()
    return jsonify({
        "accountType_used": "UNIFIED+SPOT",
        "api_key_set": bool(API_KEY),
        "balances": {"PEPE": {"wallet": str(pepe)}, "USDT": {"wallet": str(usdt)}},
        "base_url": BASE_URL,
        "bybit_retCode": retcode,
        "bybit_retMsg": "success" if retcode == 0 else "check",
        "http_status": 200,
        "raw": raw
    })

@app.route("/balance")
def balance():
    usdt, pepe, retcode, _ = get_balance()
    return jsonify({"USDT": usdt, "PEPE": pepe, "retCode": retcode})

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
