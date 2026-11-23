import os, time, hmac, hashlib, threading, requests
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.environ.get("BYBIT_API_KEY", "")
API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDC"
CATEGORY = "spot"

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        print(f"Telegram fail: {e}")

def bybit_get(path, params):
    ts = str(int(time.time() * 1000))
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sign_str = ts + API_KEY + "5000" + query
    sign = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sign,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000"
    }
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
    return r.json()

def bybit_post(path, params):
    ts = str(int(time.time() * 1000))
    body = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sign_str = ts + API_KEY + "5000" + body
    sign = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sign,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000",
        "Content-Type": "application/json"
    }
    r = requests.post(BASE_URL + path, json=params, headers=headers, timeout=10)
    return r.json()

def get_balance():
    try:
        data = bybit_get("/v5/account/wallet-balance", {
            "accountType": "SPOT",
            "coin": "USDC"
        })
        coins = data["result"]["list"][0]["coin"]
        for c in coins:
            if c["coin"] == "USDC":
                return float(c["walletBalance"])
    except Exception as e:
        print(f"Balance fail: {e}")
    return 0.0

def get_pepe_balance():
    try:
        data = bybit_get("/v5/account/wallet-balance", {
            "accountType": "SPOT",
            "coin": "PEPE"
        })
        coins = data["result"]["list"][0]["coin"]
        for c in coins:
            if c["coin"] == "PEPE":
                return float(c["walletBalance"])
    except Exception as e:
        print(f"PEPE balance fail: {e}")
    return 0.0

def get_price():
    try:
        data = bybit_get("/v5/market/tickers", {
            "category": CATEGORY,
            "symbol": SYMBOL
        })
        return float(data["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print(f"Prijs fail: {e}")
        return 0.0

def bot_loop():
    while True:
        try:
            usdc = get_balance()
            pepe = get_pepe_balance()
            prijs = get_price()
            print(f"Prijs {prijs} - USDC {usdc} - PEPE {pepe}", flush=True)
            if usdc < 5:
                print(f"- Te weinig USDC om te kopen: {usdc}", flush=True)
            else:
                # Koop voor 10 USDC aan PEPE
                bedrag = 10
                qty = str(int((bedrag / prijs) / 1000) * 1000)
                res = bybit_post("/v5/order/create", {
                    "category": CATEGORY,
                    "symbol": SYMBOL,
                    "side": "Buy",
                    "orderType": "Market",
                    "qty": qty
                })
                print(f"Koop: {res}", flush=True)
                send_telegram(f"PEPE gekocht: {qty} voor {bedrag} USDC, prijs {prijs}")
        except Exception as e:
            print(f"Bot fout: {e}", flush=True)
        time.sleep(60)

@app.route("/")
def home():
    usdc = get_balance()
    pepe = get_pepe_balance()
    prijs = get_price()
    return jsonify({
        "api_key_set": bool(API_KEY),
        "usdc": usdc,
        "pepe": pepe,
        "prijs": prijs,
        "baseUSDCpepe": usdc
    })

@app.route("/test-key")
def test_key():
    return home()

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
