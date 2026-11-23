import os, time, hmac, hashlib, threading, requests
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = "https://api.bybit.eu"
API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()

SYMBOL = "PEPEUSDT"
BUY_AMOUNT_USDT = 10.0  # koop voor 10 USDT per keer
CHECK_EVERY = 60  # elke 60 sec

state = {"prijs": 0.0, "usdt": 0.0, "pepe": 0.0, "signaal": "NONE", "last_error": ""}

def sign(query):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    to_sign = ts + API_KEY + recv + query
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sig,
        "Content-Type": "application/json"
    }

def get_prijs():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers?category=spot&symbol={SYMBOL}", timeout=10)
        data = r.json()
        return float(data["result"]["list"][0]["lastPrice"])
    except Exception as e:
        state["last_error"] = f"PRIJS FAIL: {e}"
        print(state["last_error"])
        return 0.0

def get_spot_balance(coin):
    try:
        query = f"accountType=SPOT&coin={coin}"
        headers = sign(query)
        r = requests.get(f"{BASE_URL}/v5/account/wallet-balance?{query}", headers=headers, timeout=10)
        data = r.json()
        if data.get("retCode") != 0:
            print(f"Saldo fout {coin} SPOT:", data)
            state["last_error"] = str(data)
            return 0.0
        return float(data["result"]["list"][0]["coin"][0]["walletBalance"])
    except Exception as e:
        print(f"Saldo fout {coin}:", e)
        return 0.0

def buy_pepe(usdt_amount):
    try:
        # qty = hoeveel PEPE voor X USDT
        prijs = get_prijs()
        if prijs <= 0:
            return False
        qty = int((usdt_amount / prijs))  # PEPE heeft 0 decimalen nodig, int is veilig
        if qty <= 0:
            return False
        body = f'{{"category":"spot","symbol":"{SYMBOL}","side":"Buy","orderType":"Market","qty":"{qty}"}}'
        # sign voor POST: timestamp + key + recv + body
        ts = str(int(time.time() * 1000))
        recv = "5000"
        to_sign = ts + API_KEY + recv + body
        sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
            "X-BAPI-SIGN": sig,
            "Content-Type": "application/json"
        }
        r = requests.post(f"{BASE_URL}/v5/order/create", headers=headers, data=body, timeout=10)
        print("BUY:", r.text)
        return r.json().get("retCode") == 0
    except Exception as e:
        print("BUY FAIL:", e)
        return False

def bot_loop():
    while True:
        try:
            prijs = get_prijs()
            usdt = get_spot_balance("USDT")
            pepe = get_spot_balance("PEPE")

            state["prijs"] = prijs
            state["usdt"] = usdt
            state["pepe"] = pepe

            print(f"Prijs {prijs} - USDT {usdt} - PEPE {pepe}")

            # SIMPELE STRATEGIE: als je meer dan 10 USDT hebt, koop 1x
            # Wil je verkopen, dan pas je dit later aan
            if prijs > 0 and usdt >= BUY_AMOUNT_USDT:
                state["signaal"] = "BUY"
                ok = buy_pepe(BUY_AMOUNT_USDT)
                print("Gekocht:" if ok else "Koop mislukt")
            else:
                state["signaal"] = "WACHTEN - te weinig USDT" if usdt < BUY_AMOUNT_USDT else "WACHTEN"

        except Exception as e:
            print("BOT ERROR:", e)
            state["last_error"] = str(e)

        time.sleep(CHECK_EVERY)

@app.route("/")
def home():
    return jsonify({"status": "live", "bot": "pepebot-3", **state})

@app.route("/test-key")
def test_key():
    return jsonify({
        "key_loaded": bool(API_KEY and API_SECRET),
        "base_url": BASE_URL,
        "prijs": get_prijs(),
        "USDT": get_spot_balance("USDT"),
        "PEPE": get_spot_balance("PEPE"),
        "signaal": state["signaal"],
        "last_error": state["last_error"]
    })

# start bot in background
threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
