import os, time, hmac, hashlib, requests, threading
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"

print(f"START - api_key_set: {bool(API_KEY)} - key begint met: {API_KEY[:4] if API_KEY else 'LEEG'} - base: {BASE_URL}")

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

def get_balance():
    try:
        params = {"accountType": "SPOT", "coin": "USDT"}
        headers = sign(params)
        r = requests.get(f"{BASE_URL}/v5/account/wallet-balance", params=params, headers=headers, timeout=10)
        data = r.json()
        print(f"Balance raw: {data}")
        if data.get("retCode") == 0:
            lst = data["result"]["list"]
            # Bybit kan hier list of dict geven - allebei afvangen
            if isinstance(lst, dict):
                lst = [lst]
            coins = lst[0].get("coin", [])
            for c in coins:
                if c.get("coin") == "USDT":
                    return float(c.get("walletBalance", 0))
            return 0.0
        else:
            print(f"Balance fail: {data}")
            return 0.0
    except Exception as e:
        print(f"Balance fout: {e}")
        return 0.0

def get_price():
    try:
        # Prijs hoeft GEEN key - publieke endpoint
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        data = r.json()
        # FIX voor 'list' fout: result['list'] is een LIST
        lst = data["result"]["list"]
        if isinstance(lst, list) and len(lst) > 0:
            return float(lst[0].get("lastPrice", 0))
        return 0.0
    except Exception as e:
        print(f"Prijs fout: {e}")
        return 0.0

def bot_loop():
    print("Bot loop gestart - GAAT NU TRADEN")
    while True:
        try:
            geld = get_balance()
            prijs = get_price()
            print(f"Check: Geld {geld:.2f} prijs {prijs}")
            # hier komt je koop-logica
        except Exception as e:
            print(f"Loop fout: {e}")
        time.sleep(60)

@app.route("/")
def home():
    return "Pepebot-3 live"

@app.route("/test-key")
def test_key():
    geld = get_balance()
    prijs = get_price()
    return jsonify({
        "api_key_set": bool(API_KEY),
        "key_start": API_KEY[:4] if API_KEY else "LEEG",
        "base_url": BASE_URL,
        "USDT": geld,
        "prijs": prijs,
        "status": "OK" if geld > 0 and prijs > 0 else "CHECK"
    })

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
