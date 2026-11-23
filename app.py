import os, time, hmac, hashlib, threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

BASE = "https://api.bybit.eu"
SYMBOL = "PEPEUSDC"
COIN_BUY = "USDC"
COIN_SELL = "PEPE"
MIN_BUY = 10.0  # minimaal 10 USDC om te kopen

# ---------- KEYS ----------
def get_keys():
    key = (os.getenv("BYBIT_API_KEY") or os.getenv("BYBIT_KEY") or os.getenv("API_KEY") or "").strip().strip('"').strip("'")
    secret = (os.getenv("BYBIT_API_SECRET") or os.getenv("BYBIT_SECRET") or os.getenv("API_SECRET") or "").strip().strip('"').strip("'")
    return key, secret

def api_key_set():
    k, s = get_keys()
    return bool(k and s)

# ---------- BYBIT ----------
def bybit_get(path, params):
    key, secret = get_keys()
    ts = str(int(time.time() * 1000))
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sign_str = ts + key + "5000" + query
    sign = hmac.new(secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": key,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000",
        "X-BAPI-SIGN": sign,
    }
    r = requests.get(BASE + path, params=params, headers=headers, timeout=10)
    return r.json()

def bybit_post(path, params):
    key, secret = get_keys()
    ts = str(int(time.time() * 1000))
    body = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sign_str = ts + key + "5000" + body
    sign = hmac.new(secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": key,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000",
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json",
    }
    r = requests.post(BASE + path, json=params, headers=headers, timeout=10)
    return r.json()

def get_balance(coin):
    try:
        data = bybit_get("/v5/account/wallet-balance", {"accountType": "SPOT", "coin": coin})
        print(f"BALANCE RAW {coin}: {data}", flush=True)
        if data.get("retCode") != 0:
            print(f"Balance fail {coin}: retCode={data.get('retCode')} retMsg={data.get('retMsg')}", flush=True)
            return 0.0
        coins = data["result"]["list"][0]["coin"]
        for c in coins:
            if c["coin"] == coin:
                return float(c["walletBalance"])
    except Exception as e:
        print(f"Balance fail {coin}: {e}", flush=True)
    return 0.0

def get_price():
    try:
        r = requests.get(BASE + "/v5/market/tickers", params={"category": "spot", "symbol": SYMBOL}, timeout=10).json()
        return float(r["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print(f"Price fail: {e}", flush=True)
        return 0.0

def buy_pepe(usdc_amount, price):
    qty = int(usdc_amount / price)
    if qty <= 0:
        return None
    order = {
        "category": "spot",
        "symbol": SYMBOL,
        "side": "Buy",
        "orderType": "Market",
        "qty": str(qty),
    }
    res = bybit_post("/v5/order/create", order)
    print(f"BUY: {res}", flush=True)
    return res

# ---------- BOT LOOP ----------
def bot_loop():
    print("Bot loop gestart", flush=True)
    while True:
        try:
            if not api_key_set():
                print("GEEN API KEY in Environment - check BYBIT_API_KEY / BYBIT_API_SECRET", flush=True)
                time.sleep(60)
                continue

            price = get_price()
            usdc = get_balance(COIN_BUY)
            pepe = get_balance(COIN_SELL)
            print(f"CHECK: USDC={usdc} PEPE={pepe} price={price}", flush=True)

            if price == 0 or usdc == 0:
                print("Geen saldo, probeer over 60 sec opnieuw - geen stop", flush=True)
                time.sleep(60)
                continue

            # Simpele strategie: koop als we geen PEPE hebben en genoeg USDC
            if pepe == 0 and usdc >= MIN_BUY:
                print(f"Kopen voor {usdc} USDC...", flush=True)
                buy_pepe(usdc, price)
            else:
                print("Wachten - al PEPE in bezit of te weinig USDC", flush=True)

        except Exception as e:
            print(f"Loop fout: {e}", flush=True)

        time.sleep(60)

# ---------- WEB ----------
@app.route("/")
def home():
    return jsonify({"status": "live", "symbol": SYMBOL, "api_key_set": api_key_set()})

@app.route("/test-key")
def test_key():
    usdc = get_balance(COIN_BUY)
    pepe = get_balance(COIN_SELL)
    price = get_price()
    return jsonify({
        "api_key_set": api_key_set(),
        "usdc": usdc,
        "pepe": pepe,
        "prijs": price,
        "baseUSDCpepe": usdc,
    })

# Start bot 1x, ook onder gunicorn
_thread_started = False
def start_thread():
    global _thread_started
    if not _thread_started:
        _thread_started = True
        t = threading.Thread(target=bot_loop, daemon=True)
        t.start()

start_thread()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
