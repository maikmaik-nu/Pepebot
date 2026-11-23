import os, time, hmac, hashlib, requests, threading
from flask import Flask, jsonify

app = Flask(__name__)

# ===== CONFIG =====
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
TRADE_USDT = 10.0
CHECK_EVERY_SECONDS = 60

BOT_STATE = {
    "running": True,
    "last_check": "nog niet gestart",
    "last_action": "nog niks gedaan",
    "last_error": "",
    "trades": 0
}

# ===== SIGN =====
def sign(params):
    timestamp = str(int(time.time() * 1000))
    recv = "5000"
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    to_sign = timestamp + API_KEY + recv + query
    signature = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": signature,
        "Content-Type": "application/json"
    }

def bybit_get(path, params):
    headers = sign(params)
    r = requests.get(f"{BASE_URL}{path}", params=params, headers=headers, timeout=10)
    return r.json()

def bybit_post(path, params):
    headers = sign(params)
    r = requests.post(f"{BASE_URL}{path}", json=params, headers=headers, timeout=10)
    return r.json()

# ===== SALDO =====
def get_balances():
    usdt = 0.0
    pepe = 0.0
    last_error = {}
    try:
        data = bybit_get("/v5/account/wallet-balance", {"accountType": "SPOT", "coin": "USDT"})
        if data.get("retCode") == 0:
            coins = data["result"]["list"][0]["coin"]
            for c in coins:
                if c["coin"] == "USDT":
                    usdt = float(c["walletBalance"])
                if c["coin"] == "PEPE":
                    pepe = float(c["walletBalance"])
            return usdt, pepe, data
        else:
            last_error = data
            print(f"BALANCE FAIL SPOT: {data}")
    except Exception as e:
        last_error = {"error": str(e)}
        print(f"BALANCE EXCEPTION: {e}")
    return usdt, pepe, last_error

# ===== PRIJS + KLINES =====
def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        data = r.json()
        if data.get("retCode") == 0:
            return float(data["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print(f"Price error: {e}")
    return 0.0

def get_klines():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/kline", params={"category": "spot", "symbol": SYMBOL, "interval": "15", "limit": 200}, timeout=10)
        data = r.json()
        if data.get("retCode") == 0:
            closes = [float(x[4]) for x in data["result"]["list"]]
            closes.reverse()
            return closes
    except Exception as e:
        print(f"Kline error: {e}")
    return []

def get_signal(closes):
    if len(closes) < 20:
        return "None"
    avg20 = sum(closes[-20:]) / 20
    if closes[-1] > avg20:
        return "BUY"
    return "None"

# ===== BUY / SELL =====
def buy_pepe(usdt_amount, price):
    if price <= 0:
        return None
    qty = int(usdt_amount / price)
    if qty <= 0:
        return None
    print(f"BUY: {qty} PEPE voor {usdt_amount} USDT @ {price}")
    result = bybit_post("/v5/order/create", {"category": "spot", "symbol": SYMBOL, "side": "Buy", "orderType": "Market", "qty": str(qty)})
    print(f"BUY RESULT: {result}")
    return result

def sell_pepe(pepe_amount, price):
    if pepe_amount <= 0:
        return None
    qty = int(pepe_amount)
    print(f"SELL: {qty} PEPE @ {price}")
    result = bybit_post("/v5/order/create", {"category": "spot", "symbol": SYMBOL, "side": "Sell", "orderType": "Market", "qty": str(qty)})
    print(f"SELL RESULT: {result}")
    return result

# ===== BOT LOOP =====
def bot_loop():
    print("BOT LOOP GESTART")
    time.sleep(5)
    while True:
        try:
            usdt, pepe, raw = get_balances()
            price = get_price()
            closes = get_klines()
            signal = get_signal(closes)
            in_position = pepe > 0

            print(f"CHECK: USDT={usdt} PEPE={pepe} PRICE={price} SIGNAL={signal}")
            BOT_STATE["last_check"] = f"USDT={usdt} PEPE={pepe} PRICE={price} SIGNAL={signal}"
            BOT_STATE["last_error"] = ""

            if not in_position and signal == "BUY" and usdt >= TRADE_USDT and price > 0:
                result = buy_pepe(TRADE_USDT, price)
                if result and result.get("retCode") == 0:
                    BOT_STATE["last_action"] = f"GEKOCHT voor {TRADE_USDT} USDT @ {price}"
                    BOT_STATE["trades"] += 1
                else:
                    BOT_STATE["last_error"] = str(result)
            elif in_position:
                BOT_STATE["last_action"] = f"In positie: {pepe} PEPE @ {price}"

        except Exception as e:
            print(f"BOT LOOP ERROR: {e}")
            BOT_STATE["last_error"] = str(e)
        time.sleep(CHECK_EVERY_SECONDS)

threading.Thread(target=bot_loop, daemon=True).start()

# ===== PAGINA'S =====
@app.route("/")
def home():
    return "Pepebot-3 live - bot draait in achtergrond"

@app.route("/test-key")
def test_key():
    usdt, pepe, raw = get_balances()
    return jsonify({
        "api_key_set": bool(API_KEY),
        "accountType_used": "SPOT",
        "base_url": BASE_URL,
        "usdt": usdt,
        "pepe": pepe,
        "balances": raw,
        "status": "OK" if usdt > 0 or pepe > 0 else "CHECK_BYBIT"
    })

@app.route("/check")
def check():
    usdt, pepe, raw = get_balances()
    price = get_price()
    closes = get_klines()
    return jsonify({
        "USDT": usdt,
        "PEPE": pepe,
        "PRICE": price,
        "SYMBOL": SYMBOL,
        "SIGNAL": get_signal(closes),
        "in_position": pepe > 0,
        "BOT_STATE": BOT_STATE,
        "raw": raw
    })

@app.route("/sell")
def manual_sell():
    usdt, pepe, raw = get_balances()
    price = get_price()
    if pepe <= 0:
        return jsonify({"status": "geen PEPE om te verkopen", "PEPE": pepe})
    result = sell_pepe(pepe, price)
    return jsonify({"sold_pepe": pepe, "price": price, "result": result})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
