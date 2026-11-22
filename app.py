import os
import time
import hmac
import hashlib
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# === CONFIG ===
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

def bybit_get(path, params=None):
    if params is None:
        params = {}
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    # Query string voor signature, gesorteerd
    query = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    sign_str = timestamp + BYBIT_API_KEY + recv_window + query
    signature = hmac.new(
        BYBIT_API_SECRET.encode(),
        sign_str.encode(),
        hashlib.sha256
    ).hexdigest()

    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": signature,
        "Content-Type": "application/json"
    }
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
    try:
        data = r.json()
    except:
        return r.status_code, {"retCode": -1, "retMsg": "Geen JSON terug", "raw": r.text[:200]}
    return r.status_code, data

def get_balance():
    # FIX: SPOT, niet UNIFIED. Je geld staat in Spot.
    status, data = bybit_get("/v5/account/wallet-balance", {
        "accountType": "SPOT"
    })

    ret_code = data.get("retCode", -1)
    if ret_code != 0:
        # Niet crashen, gewoon 0 teruggeven + foutcode
        return 0.0, 0.0, ret_code, data.get("retMsg", "")

    usdt = 0.0
    pepe = 0.0
    try:
        lst = data.get("result", {}).get("list", [])
        if not lst:
            return 0.0, 0.0, ret_code, "Lege list"
        coins = lst[0].get("coin", [])
        for c in coins:
            if c.get("coin") == "USDT":
                usdt = float(c.get("walletBalance", 0) or 0)
            if c.get("coin") == "PEPE":
                pepe = float(c.get("walletBalance", 0) or 0)
    except Exception as e:
        return 0.0, 0.0, ret_code, f"Parse fout: {e}"

    return usdt, pepe, ret_code, "OK"

def get_price():
    try:
        r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        data = r.json()
        lst = data.get("result", {}).get("list", [])
        if lst:
            return float(lst[0].get("lastPrice", 0) or 0)
    except:
        pass
    return 0.0

@app.route("/")
def home():
    usdt, pepe, retcode, msg = get_balance()
    price = get_price()
    in_position = pepe > 0 and price > 0
    return jsonify({
        "status": "live",
        "symbol": SYMBOL,
        "USDT": usdt,
        "PEPE": pepe,
        "price": price,
        "in_position": in_position,
        "retCode": retcode,
        "msg": msg
    })

@app.route("/test-key")
def test_key():
    usdt, pepe, retcode, msg = get_balance()
    return jsonify({
        "api_key_set": bool(BYBIT_API_KEY),
        "accountType_used": "SPOT",
        "balances": {"USDT": usdt, "PEPE": pepe},
        "retCode": retcode,
        "retMsg": msg
    })

@app.route("/check")
def check():
    usdt, pepe, retcode, msg = get_balance()
    price = get_price()
    in_position = pepe > 0 and price > 0
    print(f"CHECK: USDT={usdt} PEPE={pepe} price={price} in_position={in_position} retCode={retcode}", flush=True)
    return jsonify({
        "CHECK": f"USDT={usdt} PEPE={pepe} price={price} in_position={in_position} retCode={retcode}",
        "msg": msg
    })

@app.route("/telegram", methods=["POST"])
def telegram():
    # Zodat je geen 404 meer krijgt op /telegram
    return jsonify({"ok": True}), 200

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
