import os
import time
import hmac
import hashlib
import json
import requests

BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")

TRADE_USDT = 10
CHECK_EVERY_SEC = 60
STATE_FILE = "bot_state.json"

# FIX 1: geen .netrc crash meer
session = requests.Session()
session.trust_env = False

def sign(ts, payload=""):
    msg = ts + API_KEY + "5000" + payload
    return hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

def bybit_get(path, params):
    ts = str(int(time.time() * 1000))
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    h = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": sign(ts, query), "Content-Type": "application/json"}
    try:
        r = session.get(BASE_URL + path, params=params, headers=h, timeout=10)
        return r.json()
    except Exception as e:
        return {"retCode": -1, "error": str(e)}

def bybit_post(path, body):
    ts = str(int(time.time() * 1000))
    body_str = json.dumps(body, separators=(',', ':'))
    h = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": sign(ts, body_str), "Content-Type": "application/json"}
    try:
        r = session.post(BASE_URL + path, data=body_str, headers=h, timeout=10)
        return r.json()
    except Exception as e:
        return {"retCode": -1, "error": str(e)}

def load():
    try:
        with open(STATE_FILE) as f: return json.load(f)
    except: return {"buy_price": 0}

def save(s):
    with open(STATE_FILE, "w") as f: json.dump(s, f)

def get_balance():
    d = bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": "USDT,PEPE"})
    usdt = pepe = 0.0
    try:
        for item in d.get("result", {}).get("list", []):
            for c in item.get("coin", []):
                if c.get("coin") == "USDT": usdt = float(c.get("walletBalance") or 0)
                if c.get("coin") == "PEPE": pepe = float(c.get("walletBalance") or 0)
    except: pass
    return usdt, pepe, d.get("retCode")

def get_price():
    try:
        r = session.get(f"{BASE_URL}/v5/market/tickers", params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        return float(r.json()["result"]["list"][0]["lastPrice"])
    except: return 0

print("BOT GESTART - wacht op eerste check...", flush=True)
while True:
    try:
        usdt, pepe, ret = get_balance()
        price = get_price()
        state = load()
        buy_price = state.get("buy_price", 0)
        in_pos = (pepe * price) > 1

        print(f"CHECK USDT={usdt:.2f} PEPE={pepe} price={price} buy={buy_price} in_pos={in_pos} ret={ret}", flush=True)

        if ret != 0 or price <= 0:
            print("PAUZE: Bybit fout / geen prijs, probeer over 60s opnieuw", flush=True)
        elif not in_pos and usdt >= TRADE_USDT:
            qty = int(TRADE_USDT / price)
            if qty > 0:
                res = bybit_post("/v5/order/create", {"category": "spot", "symbol": SYMBOL, "side": "Buy", "orderType": "Market", "qty": str(qty)})
                print(f"BUY {qty} -> {res}", flush=True)
                if res.get("retCode") == 0: save({"buy_price": price})
        elif in_pos and buy_price == 0:
            print("PEPE gevonden zonder buy_price, zet buy_price = huidige prijs", flush=True)
            save({"buy_price": price})
        elif in_pos and pepe > 0:
            pnl = (price - buy_price) / buy_price
            print(f"HOLD pnl={pnl*100:.2f}% TP +2% / SL -2%", flush=True)
            if price >= buy_price * 1.02 or price <= buy_price * 0.98:
                res = bybit_post("/v5/order/create", {"category": "spot", "symbol": SYMBOL, "side": "Sell", "orderType": "Market", "qty": str(int(pepe))})
                print(f"SELL -> {res}", flush=True)
                if res.get("retCode") == 0: save({"buy_price": 0})

    except Exception as e:
        print(f"ERROR (bot blijft draaien): {e}", flush=True)

    time.sleep(CHECK_EVERY_SEC)
