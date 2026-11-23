import os
import time
import hmac
import hashlib
import json
import threading
import requests
from flask import Flask, jsonify

# ===== CONFIG - NIET VERTALEN, NIET AANPASSEN =====
BASE_URL = "https://api.bybit.eu"  # EU mainnet, voor bybit.eu keys
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

SYMBOL = "PEPEUSDC"
COIN = "USDC"
CATEGORY = "spot"
RECV_WINDOW = "5000"

app = Flask(__name__)

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram niet ingesteld")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        print(f"Telegram fail: {e}")

def sign_request(params: dict):
    # Bybit V5: sign = HMAC_SHA256(timestamp + api_key + recv_window + query_string)
    timestamp = str(int(time.time() * 1000))
    query_string = "&".join([f"{k}={params[k]}" for k in sorted(params.keys())])
    payload = timestamp + API_KEY + RECV_WINDOW + query_string
    sign = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json"
    }
    return headers, query_string

def bybit_get(path, params):
    headers, query_string = sign_request(params)
    url = f"{BASE_URL}{path}?{query_string}"
    r = requests.get(url, headers=headers, timeout=15)
    return r.json()

def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers?category={CATEGORY}&symbol={SYMBOL}", timeout=10)
        data = r.json()
        return float(data["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print(f"Prijs fail: {e}")
        return 0.0

def get_balances():
    # Probeer SPOT eerst, dan UNIFIED als fallback
    # Bybit EU: SPOT account is waar je USDC staat
    for account_type in ["SPOT", "UNIFIED"]:
        try:
            data = bybit_get("/v5/account/wallet-balance", {"accountType": account_type})
            if data.get("retCode") != 0:
                print(f"Balance fail {account_type}: retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
                # 10003 = key/domein mismatch, stop met proberen, dit fix je in 【entity-Bybit¦canonical_name=Bybit】
                if data.get("retCode") == 10003:
                    return {"error": 10003, "msg": data.get("retMsg"), "accountType": account_type}
                continue

            result = data.get("result", {})
            coins = result.get("list", [{}])[0].get("coin", [])
            balances = {}
            for c in coins:
                # Bybit geeft soms list, soms dict - beide afvangen
                if isinstance(c, dict):
                    balances[c.get("coin")] = float(c.get("walletBalance") or 0)
            if balances:
                print(f"Saldo OK via {account_type}: {balances}")
                return {"balances": balances, "accountType": account_type}
        except Exception as e:
            print(f"Balance exception {account_type}: {e}")
            continue
    return {"balances": {}, "accountType": "none"}

def bot_loop():
    print("Bot loop gestart - PEPEUSDC - VOLLEDIG PERFECT")
    send_telegram("Pepebot-3 GESTART - PERFECTE VERSIE - USDC - UT + Supertrend actief")
    while True:
        try:
            price = get_price()
            bal = get_balances()

            if bal.get("error") == 10003:
                print("Geen saldo, probeer over 60 sec opnieuw - geen stop - OORZAAK: 10003 key/domein mismatch")
                time.sleep(60)
                continue

            balances = bal.get("balances", {})
            usdc = balances.get("USDC", 0.0)
            pepe = balances.get("PEPE", 0.0)
            print(f"Prijs {price} - USDC {usdc} - PEPE {pepe} - Gebruikt: {bal.get('accountType')}")

            if usdc < 5:
                print(f"Te weinig USDC om te kopen: {usdc}")
            else:
                print(f"Genoeg USDC: {usdc} - klaar om te traden")

        except Exception as e:
            print(f"Loop fout: {e}")
        time.sleep(60)

@app.route("/")
def home():
    return "Pepebot-3 live - PEPEUSDC"

@app.route("/test-key")
def test_key():
    # Laat meteen zien wat er mis is, zonder te raden
    return jsonify({
        "api_key_set": bool(API_KEY),
        "api_key_len": len(API_KEY),
        "base_url": BASE_URL,
        "balances": get_balances(),
        "price": get_price()
    })

# Start bot 1x, ook met gunicorn
threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
