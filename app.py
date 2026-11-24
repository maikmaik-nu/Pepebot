import os
import time
import hmac
import hashlib
import requests
import threading
from flask import Flask, jsonify

app = Flask(__name__)

# MOET .com zijn, geen .eu
BASE = "https://api.bybit.com"

API_KEY = os.environ.get("BYBIT_API_KEY", "")
API_SECRET = os.environ.get("BYBIT_API_SECRET", "")

in_positie = False
laatste_check = "nog niet gecheckt"

def bybit_get(endpoint, params={}):
    # Juiste 【entity-Bybit V5¦canonical_name=Bybit】 signing - dit was fout in je oude code
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    payload = timestamp + API_KEY + recv_window + query
    sign = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sign,
        "X-BAPI-SIGN-TYPE": "2",
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window
    }
    url = BASE + endpoint
    if query:
        url += "?" + query
    try:
        r = requests.get(url, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        return {"retCode": -1, "retMsg": str(e)}

def check_loop():
    global in_positie, laatste_check
    while True:
        try:
            # 1. Balance ophalen
            bal_data = bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": "USDT"})
            usdt = 0.00
            if bal_data.get("retCode") == 0:
                for coin_info in bal_data["result"]["list"]:
                    for c in coin_info["coin"]:
                        if c["coin"] == "USDT":
                            usdt = float(c["walletBalance"])
            else:
                print(f"Bybit balance fout: {bal_data.get('retMsg')}")

            # 2. PEPE prijs ophalen - publiek, geen key nodig
            prijs = 0.0
            try:
                pr = requests.get(f"{BASE}/v5/market/tickers?category=spot&symbol=PEPEUSDT", timeout=10).json()
                if pr.get("retCode") == 0:
                    prijs = float(pr["result"]["list"][0]["lastPrice"])
            except Exception as e:
                print(f"Prijs fout: {e}")

            # 3. PEPE positie ophalen
            pepe = 0
            pos_data = bybit_get("/v5/position/list", {"category": "linear", "symbol": "PEPEUSDT"})
            if pos_data.get("retCode") == 0:
                for p in pos_data["result"]["list"]:
                    if float(p["size"]) > 0:
                        pepe = float(p["size"])
                        in_positie = True
            # Spot PEPE ook checken
            if pepe == 0 and bal_data.get("retCode") == 0:
                for coin_info in bal_data["result"]["list"]:
                    for c in coin_info["coin"]:
                        if c["coin"] == "PEPE":
                            pepe = float(c["walletBalance"])
                            if pepe > 0:
                                in_positie = True

            waarde = round(pepe * prijs, 2) if prijs > 0 else 0.00
            laatste_check = f"Check: USDT {usdt:.2f} PEPE {pepe} waarde {waarde:.2f} prijs {prijs} in_positie {in_positie}"
            print(laatste_check, flush=True)

        except Exception as e:
            print(f"Check fout: {e}", flush=True)

        time.sleep(60)

@app.route("/")
def home():
    return jsonify({"status": "online", "bot": "Pepebot", "base": BASE, "laatste_check": laatste_check, "in_positie": in_positie})

@app.route("/test-key")
def test_key():
    data = bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": "USDT"})
    if data.get("retCode") == 0:
        return jsonify({"ok": True, "message": "VERBONDE MET BYBIT!", "base": BASE, "result": data["result"]})
    else:
        return jsonify({"ok": False, "error": data.get("retMsg"), "retCode": data.get("retCode"), "base": BASE, "tip": "Key staat op verkeerde site, of IP restriction staat aan, of Read permissie ontbreekt"})

# Start check loop op achtergrond
threading.Thread(target=check_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
