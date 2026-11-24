import os, time, hmac, hashlib, threading, requests
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = os.getenv("BASE_URL", "https://api.bybit.eu")
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
SYMBOL = "PEPEUSDC"
COIN = "PEPE"
CASH = "USDC"

prices = []
last_status = {"status": "starting"}

def sign(params):
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return qs + f"&sign={sig}"

def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        data = r.json()
        lst = data.get("result", {}).get("list", [])
        if not lst:
            return 0.0, data
        return float(lst[0].get("lastPrice", 0)), data
    except Exception as e:
        return 0.0, {"error": str(e)}

def get_balance(coin):
    try:
        ts = str(int(time.time() * 1000))
        params = {"api_key": API_KEY, "timestamp": ts, "accountType": "SPOT", "coin": coin}
        url = f"{BASE_URL}/v5/account/wallet-balance?{sign(params)}"
        r = requests.get(url, headers={"X-BAPI-API-KEY": API_KEY}, timeout=10)
        data = r.json()
        lst = data.get("result", {}).get("list", [])
        if not lst:
            return 0.0, data
        coins = lst[0].get("coin", [])
        for c in coins:
            if c.get("coin") == coin:
                return float(c.get("walletBalance", 0) or 0), data
        return 0.0, data
    except Exception as e:
        return 0.0, {"error": str(e)}

def get_ut_signal():
    # simpel UT-signaal op basis van laatste prijzen
    if len(prices) < 3:
        return "hold"
    if prices[-1] > prices[-2] > prices[-3]:
        return "buy"
    if prices[-1] < prices[-2] < prices[-3]:
        return "sell"
    return "hold"

def bot_loop():
    global last_status
    while True:
        try:
            price, _ = get_price()
            cash, _ = get_balance(CASH)
            pepe, _ = get_balance(COIN)
            if price > 0:
                prices.append(price)
                prices[:] = prices[-100:]
            sig = get_ut_signal()
            last_status = {"price": price, "cash": cash, "pepe": pepe, "signal": sig, "symbol": SYMBOL}
            print(f"Check: Geld {cash} PEPE {pepe} prijs {price} UT:{sig}", flush=True)
        except Exception as e:
            print(f"BOT ERROR: {e}", flush=True)
        time.sleep(60)

@app.route("/")
def home():
    return jsonify(last_status)

@app.route("/test-key")
def test_key():
    price, price_raw = get_price()
    cash, cash_raw = get_balance(CASH)
    pepe, pepe_raw = get_balance(COIN)
    return jsonify({
        "symbol": SYMBOL,
        "price": price,
        "USDC": cash,
        "PEPE": pepe,
        "price_raw": price_raw,
        "cash_raw": cash_raw,
        "pepe_raw": pepe_raw
    })

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
