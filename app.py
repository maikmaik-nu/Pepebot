import os, time, hmac, hashlib, requests, json
from flask import Flask, jsonify, request

app = Flask(__name__)

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE_URL = "https://api.bybit.eu"
CATEGORY = "spot"
SYMBOL = "PEPEUSDT"
TRADE_USDT = float(os.getenv("TRADE_USDT", "90"))

def sign(params):
    params["api_key"] = API_KEY
    params["timestamp"] = str(int(time.time() * 1000))
    params["recv_window"] = "5000"
    s = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hmac.new(API_SECRET.encode(), s.encode(), hashlib.sha256).hexdigest()

def bybit_get(path, params):
    params["sign"] = sign(params)
    headers = {"X-BAPI-API-KEY": API_KEY}
    try:
        r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
        return r.status_code, r.json()
    except Exception as e:
        return 0, {"error": str(e)}

def bybit_post(path, body):
    body["api_key"] = API_KEY
    body["timestamp"] = str(int(time.time() * 1000))
    body["recv_window"] = "5000"
    s = "&".join(f"{k}={body[k]}" for k in sorted(body))
    body["sign"] = hmac.new(API_SECRET.encode(), s.encode(), hashlib.sha256).hexdigest()
    headers = {"X-BAPI-API-KEY": API_KEY, "Content-Type": "application/json"}
    try:
        r = requests.post(BASE_URL + path, json=body, headers=headers, timeout=10)
        return r.status_code, r.json()
    except Exception as e:
        return 0, {"error": str(e)}

def get_balance():
    status, data = bybit_get("/v5/account/wallet-balance", {"accountType": CATEGORY, "coin": "USDT"})
    usdt = 0.0
    pepe = 0.0
    # PEPE apart ophalen, 【entity-Bybit¦canonical_name=Bybit】 wil 1 coin per call
    _, data2 = bybit_get("/v5/account/wallet-balance", {"accountType": CATEGORY, "coin": "PEPE"})
    for d in [data, data2]:
        try:
            for c in d["result"]["list"][0]["coin"]:
                if c["coin"] == "USDT":
                    usdt = float(c.get("walletBalance", 0))
                if c["coin"] == "PEPE":
                    pepe = float(c.get("walletBalance", 0))
        except:
            pass
    return usdt, pepe, data.get("retCode"), data

def get_price():
    try:
        r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
        return float(r.json()["result"]["list"][0]["lastPrice"])
    except:
        return 0.0

@app.route("/")
def home():
    usdt, pepe, retcode, _ = get_balance()
    price = get_price()
    in_pos = pepe > 0
    return f"USDT={usdt} PEPE={pepe} price={price} in_position={in_pos} retCode={retcode}"

@app.route("/test-key")
def test_key():
    usdt, pepe, retcode, full = get_balance()
    return jsonify({
        "api_key_set": bool(API_KEY),
        "api_secret_set": bool(API_SECRET),
        "base_url": BASE_URL,
        "category": CATEGORY,
        "symbol": SYMBOL,
        "balances": {"USDT": usdt, "PEPE": pepe},
        "price": get_price(),
        "retCode": retcode,
        "retMsg": full.get("retMsg"),
        "full_bybit_response": full
    })

@app.route("/balance")
def balance():
    usdt, pepe, retcode, full = get_balance()
    return jsonify({"USDT": usdt, "PEPE": pepe, "retCode": retcode, "retMsg": full.get("retMsg")})

@app.route("/buy")
def buy():
    usdt, pepe, retcode, _ = get_balance()
    if usdt < 10:
        return jsonify({"error": "te weinig USDT", "USDT": usdt})
    price = get_price()
    if price <= 0:
        return jsonify({"error": "geen prijs"})
    qty = str(int((TRADE_USDT / price)))
    status, data = bybit_post("/v5/order/create", {
        "category": CATEGORY, "symbol": SYMBOL, "side": "Buy",
        "orderType": "Market", "qty": qty
    })
    return jsonify({"qty": qty, "price": price, "bybit": data})

@app.route("/sell")
def sell():
    usdt, pepe, retcode, _ = get_balance()
    if pepe <= 0:
        return jsonify({"error": "geen PEPE om te verkopen", "PEPE": pepe})
    qty = str(int(pepe))
    status, data = bybit_post("/v5/order/create", {
        "category": CATEGORY, "symbol": SYMBOL, "side": "Sell",
        "orderType": "Market", "qty": qty
    })
    return jsonify({"qty": qty, "bybit": data})

@app.route("/check")
def check():
    usdt, pepe, retcode, _ = get_balance()
    price = get_price()
    return jsonify({"USDT": usdt, "PEPE": pepe, "price": price, "in_position": pepe > 0, "retCode": retcode})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
