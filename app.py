import os, time, hmac, hashlib, requests
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDC"
CATEGORY = "spot"
COIN = "USDC"

# Hoeveel USDC per trade - pas dit zelf aan
TRADE_AMOUNT_USDC = 10.0

def sign(params):
    timestamp = str(int(time.time() * 1000))
    recv = "5000"
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    to_sign = timestamp + API_KEY + recv + query
    signature = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": signature,
        "Content-Type": "application/json"
    }
    return headers

def bybit_get(path, params):
    headers = sign(params)
    r = requests.get(f"{BASE_URL}{path}", params=params, headers=headers, timeout=10)
    try:
        return r.status_code, r.json()
    except:
        return r.status_code, {"retCode": -1, "retMsg": "Lege body", "raw": r.text[:200]}

def bybit_post(path, params):
    headers = sign(params)
    r = requests.post(f"{BASE_URL}{path}", json=params, headers=headers, timeout=10)
    try:
        return r.status_code, r.json()
    except:
        return r.status_code, {"retCode": -1, "retMsg": "Lege body", "raw": r.text[:200]}

def get_balance():
    # Alleen SPOT, alleen USDC - dit is waar je geld staat op 【entity-bybit¦canonical_name=Bybit】.eu
    status, data = bybit_get("/v5/account/wallet-balance", {
        "accountType": "SPOT",
        "coin": COIN
    })
    if data.get("retCode") == 0:
        try:
            coins = data["result"]["list"][0]["coin"]
            for c in coins:
                if c["coin"] == COIN:
                    usdc = float(c["walletBalance"])
                if c["coin"] == "PEPE":
                    pepe = float(c["walletBalance"])
                else:
                    pepe = 0.0
            return usdc, pepe, data
        except Exception as e:
            return 0.0, 0.0, {"error": str(e), "raw": data}
    return 0.0, 0.0, data

def get_price():
    # Geen signature nodig voor prijs
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={
            "category": CATEGORY,
            "symbol": SYMBOL
        }, timeout=10)
        data = r.json()
        if data.get("retCode") == 0:
            return float(data["result"]["list"][0]["lastPrice"]), data
    except Exception as e:
        return 0.0, {"error": str(e)}
    return 0.0, data

@app.route("/")
def home():
    usdc, pepe, raw = get_balance()
    price, _ = get_price()
    return jsonify({
        "bot": "Pepebot-3 live",
        "symbol": SYMBOL,
        "base_url": BASE_URL,
        "USDC": usdc,
        "PEPE": pepe,
        "price": price,
        "in_position": pepe > 0,
        "retCode": raw.get("retCode")
    })

@app.route("/test-key")
def test_key():
    usdc, pepe, raw = get_balance()
    price, price_raw = get_price()
    return jsonify({
        "api_key_set": bool(API_KEY),
        "key_start": API_KEY[:4] + "****" if API_KEY else "",
        "accountType_used": "SPOT",
        "base_url": BASE_URL,
        "symbol": SYMBOL,
        "coin": COIN,
        "usdc": usdc,
        "pepe": pepe,
        "price": price,
        "balances": raw,
        "price_raw": price_raw,
        "status": "OK" if raw.get("retCode") == 0 else "ERROR"
    })

@app.route("/check")
def check():
    usdc, pepe, raw = get_balance()
    price, _ = get_price()
    return jsonify({
        "USDC": usdc,
        "PEPE": pepe,
        "price": price,
        "in_position": pepe > 0,
        "retCode": raw.get("retCode"),
        "raw": raw
    })

@app.route("/buy")
def buy():
    # Handmatig testen: open /buy om 1x te kopen met TRADE_AMOUNT_USDC
    usdc, pepe, raw = get_balance()
    if raw.get("retCode") != 0:
        return jsonify({"error": "Saldo ophalen faalt", "raw": raw}), 400
    if usdc < TRADE_AMOUNT_USDC:
        return jsonify({"error": "Te weinig USDC", "USDC": usdc}), 400

    price, _ = get_price()
    if price <= 0:
        return jsonify({"error": "Geen prijs"}), 400

    qty = str(int((TRADE_AMOUNT_USDC / price)))
    status, data = bybit_post("/v5/order/create", {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": "Buy",
        "orderType": "Market",
        "qty": qty
    })
    return jsonify({"qty": qty, "price": price, "result": data})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
