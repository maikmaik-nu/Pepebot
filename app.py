import os, time, hmac, hashlib, threading, requests
from flask import Flask

# === JOUW INSTELLINGEN - PAS DIT AAN ===
BUY_PRICE = 0.00000800   # koop als PEPE onder dit komt
SELL_PRICE = 0.00001000  # verkoop als PEPE boven dit komt
TRADE_USDT = 10.0        # hoeveel USDT per trade
# ========================================

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE = "https://api.bybit.com"
SYMBOL = "PEPEUSDT"

app = Flask(__name__)
@app.route("/")
def home():
    return "Pepebot draait"

def sign_request(params_str, ts, recv):
    msg = ts + API_KEY + recv + params_str
    return hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

def get_prijs():
    try:
        r = requests.get(f"{BASE}/v5/market/tickers", params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        data = r.json()
        return float(data["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print(f"PRIJS FAIL: {e}", flush=True)
        return 0.0

def get_saldo(coin):
    try:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        params = f"accountType=UNIFIED&coin={coin}"
        headers = {
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
            "X-BAPI-SIGN": sign_request(params, ts, recv)
        }
        r = requests.get(f"{BASE}/v5/account/wallet-balance?{params}", headers=headers, timeout=10)
        data = r.json()
        if data.get("retCode") != 0:
            print(f"Saldo fout {coin}: {data}", flush=True)
            return 0.0
        for c in data["result"]["list"][0]["coin"]:
            if c["coin"] == coin:
                return float(c["walletBalance"])
        return 0.0
    except Exception as e:
        print(f"Saldo fout {coin}: {e}", flush=True)
        return 0.0

def place_order(side, qty):
    try:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        body = f'{{"category":"spot","symbol":"{SYMBOL}","side":"{side}","orderType":"Market","qty":"{qty}"}}'
        headers = {
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
            "X-BAPI-SIGN": sign_request(body, ts, recv),
            "Content-Type": "application/json"
        }
        r = requests.post(f"{BASE}/v5/order/create", headers=headers, data=body, timeout=10)
        print(f"ORDER {side} {qty}: {r.json()}", flush=True)
        return r.json()
    except Exception as e:
        print(f"ORDER FAIL: {e}", flush=True)
        return None

def bot_loop():
    while True:
        try:
            prijs = get_prijs()
            usdt = get_saldo("USDT")
            pepe = get_saldo("PEPE")
            print(f"Prijs {prijs} - USDT {usdt} - PEPE {pepe}", flush=True)

            if prijs == 0:
                time.sleep(30)
                continue

            # KOOP LOGICA
            if prijs <= BUY_PRICE and usdt >= TRADE_USDT:
                qty = int(TRADE_USDT / prijs)
                print(f"KOOP! Prijs {prijs} <= {BUY_PRICE}", flush=True)
                place_order("Buy", str(qty))

            # VERKO
