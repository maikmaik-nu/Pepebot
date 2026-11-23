import os, time, hmac, hashlib, requests
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDC"  # <-- WAS PEPEUSDT
CATEGORY = "spot"

# ===== SETTINGS =====
TRADE_USDC = 20.0  # hoeveel USDC per trade
TAKE_PROFIT_PCT = 0.02
STOP_LOSS_PCT = 0.02

# ===== 【entity-BYBIT¦canonical_name=BYBIT】 SIGN =====
def bybit_get(path, params):
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
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
    return r.status_code, r.json()

def 【entity-bybit_post¦canonical_name=BYBIT】(path, params):
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
    r = requests.post(BASE_URL + path, json=params, headers=headers, timeout=10)
    return r.status_code, r.json()

# ===== BALANCE - NU USDC =====
def get_balance():
    last_retcode = 999
    last_data = {}
    try:
        # In 1 keer USDC + PEPE opvragen
        status, data = bybit_get("/v5/account/wallet-balance", {
            "accountType": "SPOT",
            "coin": "USDC,PEPE"
        })
        if data.get("retCode") == 0:
            coins = data["result"]["list"][0]["coin"]
            usdc = 0.0
            pepe = 0.0
            for c in coins:
                if c["coin"] == "USDC":
                    usdc = float(c.get("walletBalance", 0) or 0)
                if c["coin"] == "PEPE":
                    pepe = float(c.get("walletBalance", 0) or 0)
            return usdc, pepe, 0, data
        else:
            print(f"BALANCE FAIL SPOT: retCode {data.get('retCode')} - {data.get('retMsg')}")
            last_retcode = data.get("retCode")
            last_data = data
    except Exception as e:
        print(f"BALANCE ERROR: {e}")
        last_data = {"error": str(e)}

    return 0.0, 0.0, last_retcode, last_data

# ===== PRIJS =====
def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
        data = r.json()
        if data.get("retCode") == 0:
            return float(data["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print(f"PRICE ERROR: {e}")
    return 0.0

# ===== TRADING =====
def buy_pepe(usdc_amount):
    price = get_price()
    if price == 0:
        return {"ok": False, "error": "geen prijs"}
    qty = str(int(usdc_amount / price))
    status, data = 【entity-bybit_post¦canonical_name=BYBIT】("/v5/order/create", {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": "Buy",
        "orderType": "Market",
        "qty": qty
    })
    print(f"BUY: {data}")
    return data

def sell_pepe(qty):
    status, data = 【entity-bybit_post¦canonical_name=BYBIT】("/v5/order/create", {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": "Sell",
        "orderType": "Market",
        "qty": str(int(qty))
    })
    print(f"SELL: {data}")
    return data

def do_check():
    usdc, pepe, retcode, raw = get_balance()
    price = get_price()
    in_position = pepe > 0
    print(f"CHECK: USDC={usdc} PEPE={pepe} price={price} in_position={in_position} retCode={retcode}")

    if retcode != 0:
        return {"usdc": usdc, "pepe": pepe, "price": price, "in_position": in_position, "retCode": retcode, "action": "STOP - geen saldo / geen rechten", "raw": raw}

    if not in_position and usdc >= TRADE_USDC:
        result = buy_pepe(TRADE_USDC)
        return {"usdc": usdc, "pepe": pepe, "price": price, "in_position": in_position, "action": "BUY", "order": result}

    if in_position:
        return {"usdc": usdc, "pepe": pepe, "price": price, "in_position": in_position, "action": "HOLD - in positie", "raw": raw}

    return {"usdc": usdc, "pepe": pepe, "price": price, "in_position": in_position, "action": "WAIT - te weinig USDC", "raw": raw}

# ===== ROUTES =====
@app.route("/")
def home():
    try:
        result = do_check()
        return jsonify(result)
    except Exception as e:
        print(f"HOME ERROR: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/test-key")
def test_key():
    usdc, pepe, retcode, raw = get_balance()
    return jsonify({
        "api_key_set": bool(API_KEY),
        "accountType_used": "SPOT",
        "base_url": BASE_URL,
        "symbol": SYMBOL,
        "usdc": usdc,
        "pepe": pepe,
        "retCode": retcode,
        "balances": raw,
        "status": "OK" if retcode == 0 else "CHECK_BYBIT"
    })

@app.route("/check")
def check():
    result = do_check()
    return jsonify(result)

@app.route("/price")
def price_route():
    return jsonify({"symbol": SYMBOL, "price": get_price()})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
