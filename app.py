import os, time, hmac, hashlib, threading, requests
from flask import Flask

app = Flask(__name__)

# ===== CONFIG - 【entity-BYBIT EU¦canonical_name=Bybit】 =====
BASE_URL = "https://api.bybit.eu"
API_KEY = os.environ.get("【entity-BYBIT_API_KEY¦canonical_name=Bybit】", "").strip()
API_SECRET = os.environ.get("【entity-BYBIT_API_SECRET¦canonical_name=Bybit】", "").strip()
SYMBOL = "PEPEUSDT"
COIN = "USDT"
RECV_WINDOW = "5000"

# Voorkomt dat gunicorn de bot 2x start
_bot_started = False
_bot_lock = threading.Lock()

def sign(timestamp, api_key, recv_window, query_string):
    param_str = str(timestamp) + api_key + str(recv_window) + query_string
    return hmac.new(
        API_SECRET.encode("utf-8"),
        param_str.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

def bybit_get(path, params):
    timestamp = int(time.time() * 1000)
    # Query moet GESORTEERD zijn, anders klopt je signature niet
    query = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    signature = sign(timestamp, API_KEY, RECV_WINDOW, query)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": str(timestamp),
        "X-BAPI-RECV-WINDOW": str(RECV_WINDOW),
        "Content-Type": "application/json"
    }
    r = requests.get(f"{BASE_URL}{path}", params=params, headers=headers, timeout=10)
    print(f"【entity-BYBIT¦canonical_name=Bybit】 {path} {params} -> {r.status_code} {r.text[:600]}")
    return r.json()

def get_saldo():
    # 1. SPOT - hier staat je 100 USDT
    try:
        data = bybit_get("/v5/account/wallet-balance", {"accountType": "SPOT", "coin": COIN})
        if data.get("retCode") == 0:
            coins = data["result"]["list"][0]["coin"]
            for c in coins:
                print(f"Saldo SPOT {c['coin']}: {c.get('walletBalance')}")
            return coins, None

        print(f"Saldo fout SPOT: {data}")
        # 2. Als SPOT faalt, probeer UNIFIED als fallback - maar stop NOOIT
        data2 = bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": COIN})
        if data2.get("retCode") == 0:
            coins = data2["result"]["list"][0]["coin"]
            for c in coins:
                print(f"Saldo UNIFIED {c['coin']}: {c.get('walletBalance')}")
            return coins, None

        return None, data2

    except Exception as e:
        print(f"Saldo fout: {e}")
        return None, {"error": str(e)}

def get_price():
    try:
        # Prijs heeft GEEN signature nodig - public endpoint
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        data = r.json()
        if data.get("retCode") == 0:
            return float(data["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print(f"Prijs fout: {e}")
    return 0

def bot_loop():
    print(f"Bot loop gestart, KEY LOADED: {bool(API_KEY)} SECRET LOADED: {bool(API_SECRET)}")
    while True:
        try:
            saldo, err = get_saldo()
            price = get_price()
            print(f"CHECK: price={price} saldo_ok={saldo is not None} err={err}")

            if saldo is None:
                print("Geen saldo, probeer over 60 sec opnieuw - geen stop")
            else:
                # Hier komt later je trade-logica
                # Voor nu: alleen checken, niks kopen/verkopen
                usdt = 0
                for c in saldo:
                    if c["coin"] == COIN:
                        usdt = float(c.get("walletBalance", 0))
                print(f"USDT in SPOT: {usdt} | PEPE prijs: {price}")

        except Exception as e:
            print(f"Bot loop fout (geen stop): {e}")

        time.sleep(60)

def start_bot_once():
    global _bot_started
    with _bot_lock:
        if not _bot_started:
            _bot_started = True
            threading.Thread(target=bot_loop, daemon=True).start()

@app.route("/")
def home():
    return "Pepebot live - Bybit EU - SPOT"

@app.route("/saldo")
def saldo_route():
    s, err = get_saldo()
    return {"saldo": s, "error": err, "symbol": SYMBOL}

@app.route("/test-key")
def test_key():
    s, err = get_saldo()
    return {
        "api_key_set": bool(API_KEY),
        "api_secret_set": bool(API_SECRET),
        "base_url": BASE_URL,
        "accountType_used": "SPOT",
        "balances": s,
        "error": err,
        "price": get_price()
    }

@app.route("/health")
def health():
    return {"status": "ok", "key_loaded": bool(API_KEY)}

start_bot_once()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
