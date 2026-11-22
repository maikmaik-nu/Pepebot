import os, time, hmac, hashlib, requests
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"

# ===== SETTINGS =====
TRADE_USDT = 20.0  # hoeveel USDT per trade
TAKE_PROFIT_PCT = 0.02  # 2% winst = verkopen
STOP_LOSS_PCT = 0.02  # 2% verlies = verkopen

# ===== 【entity-BYBIT¦canonical_name=Bybit】 SIGN =====
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

def bybit_post(path, params):
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

# ===== BALANCE =====
def get_balance():
    # Probeer SPOT eerst - daar staat je geld
    for account_type in ["SPOT", "UNIFIED"]:
        try:
            status, data = bybit_get("/v5/account/wallet-balance", {
                "accountType": account_type,
                "coin": "USDT"
            })
            if data.get("retCode") == 0:
                coins = data["result"]["list"][0]["coin"]
                usdt = 0.0
                pepe = 0.0
                for c in coins:
                    if c["coin"] == "USDT":
                        usdt = float(c.get("walletBalance", 0) or 0)
                    if c["coin"] == "PEPE":
                        pepe = float(c.get("walletBalance", 0) or 0)
                return usdt, pepe, 0, data
            else:
                print(f"BALANCE FAIL {account_type}: retCode {data.get('retCode')} - {data.get('retMsg')}")
                last_retcode = data.get("retCode")
                last_data = data
        except Exception as e:
            print(f"BALANCE ERROR {account_type}: {e}")
            last_retcode = 999
            last_data = {"error": str(e)}

    # Fallback: probeer PEPE apart op te halen voor SPOT
    try:
        status, data = bybit_get("/v5/account/wallet-balance", {
            "accountType": "SPOT",
            "coin": "PEPE"
        })
        if data.get("retCode") == 0:
            coins = data["result"]["list"][0]["coin"]
            for c in coins:
                if c["coin"] == "PEPE":
                    pepe_fallback = float(c.get("walletBalance", 0) or 0)
                    return 0.0, pepe_fallback, 0, data
    except Exception as e:
        pass

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
def buy_pepe(usdt_amount):
    price = get_price()
    if price == 0:
        return {"ok": False, "error": "geen prijs"}
    qty = str(int(usdt_amount / price))
    status, data = bybit_post("/v5/order/create", {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": "Buy",
        "orderType": "Market",
        "qty": qty
    })
    print(f"BUY: {data}")
    return data

def sell_pepe(qty):
    status, data = bybit_post("/v5/order/create", {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": "Sell",
        "orderType": "Market",
        "qty": str(int(qty))
    })
    print(f"SELL: {data}")
    return data

def do_check():
    usdt, pepe, retcode, raw = get_balance()
    price = get_price()
    in_position = pepe > 0
    print(f"CHECK: USDT={usdt} PEPE={pepe} price={price} in_position={in_position} retCode={retcode}")

    if retcode != 0:
        return {"usdt": usdt, "pepe": pepe, "price": price, "in_position": in_position, "retCode": retcode, "action": "STOP - geen saldo / geen rechten", "raw": raw}

    # Geen positie + genoeg USDT = kopen
    if not in_position and usdt >= TRADE_USDT:
        result = buy_pepe(TRADE_USDT)
        return {"usdt": usdt, "pepe": pepe, "price": price, "in_position": in_position, "action": "BUY", "order": result}

    # Wel positie = check winst/verlies - simpele versie, verkoopt alles als je pepe hebt en prijs > 0
    # Voor echte TP/SL moet je entry prijs opslaan, dat doen we in de volgende versie
    if in_position:
        return {"usdt": usdt, "pepe": pepe, "price": price, "in_position": in_position, "action": "HOLD - in positie", "raw": raw}

    return {"usdt": usdt, "pepe": pepe, "price": price, "in_position": in_position, "action": "WAIT - te weinig USDT", "raw": raw}

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
    usdt, pepe, retcode, raw = get_balance()
    return jsonify({
        "api_key_set": bool(API_KEY),
        "accountType_used": "SPOT",
        "base_url": BASE_URL,
        "symbol": SYMBOL,
        "usdt": usdt,
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
