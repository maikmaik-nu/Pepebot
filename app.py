import os, time, hmac, hashlib, requests, threading
from flask import Flask, jsonify

app = Flask(__name__)

BASE = "https://api.bybit.eu"
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()

SYMBOL = "PEPEUSDC"
CATEGORY = "spot"
BUY_AMOUNT_USDC = 10.0  # <-- met hoeveel hij per keer koopt, pas dit aan als je wil

def sign(params, is_post=False):
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    if is_post:
        import json
        query = json.dumps(params, separators=(',', ':'))
    else:
        query = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    payload = timestamp + API_KEY + recv_window + query
    signature = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": signature,
        "Content-Type": "application/json"
    }

def get_balance(coin):
    try:
        params = {"accountType": "SPOT", "coin": coin}
        r = requests.get(f"{BASE}/v5/account/wallet-balance", params=params, headers=sign(params), timeout=10)
        data = r.json()
        if data.get("retCode") != 0:
            print(f"Balance fail {coin}: {data}", flush=True)
            return 0.0
        lst = data.get("result", {}).get("list", [])
        if not lst:
            return 0.0
        for c in lst[0].get("coin", []):
            if c.get("coin") == coin:
                return float(c.get("walletBalance", 0) or 0)
        return 0.0
    except Exception as e:
        print(f"Balance fail {coin}: {e}", flush=True)
        return 0.0

def get_price():
    try:
        r = requests.get(f"{BASE}/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
        data = r.json()
        lst = data.get("result", {}).get("list", [])
        if not lst:
            print(f"Prijs fout: {data}", flush=True)
            return 0.0
        return float(lst[0].get("lastPrice", 0) or 0)
    except Exception as e:
        print(f"Prijs fout: {e}", flush=True)
        return 0.0

def buy_pepe(amount_usdc):
    try:
        price = get_price()
        if price == 0:
            print("Kopen mislukt: prijs 0", flush=True)
            return {"error": "prijs 0"}
        qty = amount_usdc / price
        # PEPE heeft veel decimalen, afronden naar hele PEPE
        qty = int(qty)
        if qty <= 0:
            print("Kopen mislukt: qty 0", flush=True)
            return {"error": "qty 0"}
        params = {
            "category": CATEGORY,
            "symbol": SYMBOL,
            "side": "Buy",
            "orderType": "Market",
            "qty": str(qty)
        }
        r = requests.post(f"{BASE}/v5/order/create", json=params, headers=sign(params, is_post=True), timeout=10)
        data = r.json()
        print(f"KOOP ORDER: {qty} PEPE voor ~{amount_usdc} USDC -> {data}", flush=True)
        return data
    except Exception as e:
        print(f"Koop fout: {e}", flush=True)
        return {"error": str(e)}

def bot_loop():
    already_bought = False
    while True:
        try:
            usdc =
