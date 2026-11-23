import os, time, hmac, hashlib, json, threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)
BASE_URL = "https://api.bybit.eu"
CATEGORY = "spot"
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()

def sign_get(params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sig = hmac.new(API_SECRET.encode(), (ts + API_KEY + recv + query).encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json"
    }

def bybit_get(path, params):
    r = requests.get(BASE_URL + path, params=params, headers=sign_get(params), timeout=15)
    return r.json()

def bybit_post(path, params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    payload = json.dumps(params, separators=(",", ":"))
    sig = hmac.new(API_SECRET.encode(), (ts + API_KEY + recv + payload).encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json"
    }
    r = requests.post(BASE_URL + path, data=payload, headers=headers, timeout=15)
    return r.json()

def get_balances():
    for acct in ["UNIFIED", "SPOT"]:
        d = bybit_get("/v5/account/wallet-balance", {"accountType": acct})
        if d.get("retCode") == 0:
            lst = d.get("result", {}).get("list", [])
            if lst:
                out = {c["coin"]: float(c.get("walletBalance", 0)) for c in lst[0].get("coin", [])}
                return out, acct, d
        print(f"Saldo {acct}: {d.get('retCode')} {d.get('retMsg')}", flush=True)
    return {}, "GEEN", {}

def find_symbol():
    # Check welk PEPE-paar echt bestaat op Bybit EU
    for sym in ["PEPEUSDC", "PEPEUSDT"]:
        try:
            d = bybit_get("/v5/market/instruments-info", {"category": CATEGORY, "symbol": sym})
            if d.get("retCode") == 0 and d.get("result", {}).get("list"):
                info = d["result"]["list"][0]
                qty_step = float(info["lotSizeFilter"]["qtyStep"])
                return sym, qty_step
        except Exception as e:
            print(f"Symbol check {sym} fail: {e}", flush=True)
    return None, 1

def get_price(sym):
    d = bybit_get("/v5/market/tickers", {"category": CATEGORY, "symbol": sym})
    return float(d["result"]["list"][0]["lastPrice"])

def bot_loop():
    time.sleep(10)
    while True:
        try:
            bal, worked, _ = get_balances()
            usdc = bal.get("USDC", 0.0)
            usdt = bal.get("USDT", 0.0)
            pepe = bal.get("PEPE", 0.0)
            sym, qty_step = find_symbol()
            if not sym:
                print("GEEN PEPE PAAR GEVONDEN OP BYBIT EU", flush=True)
                time.sleep(60)
                continue
            prijs = get_price(sym)
            print(f"ACC:{worked} SYM:{sym} Prijs:{prijs} USDC:{usdc} USDT:{usdt} PEPE:{pepe}", flush=True)
            # Bepaal waarmee we kunnen kopen
            if sym == "PEPEUSDC" and usdc >= 10 and pepe == 0:
                qty = int((10 / prijs) / qty_step) * qty_step
                print(f"LIVE KOOP {qty} PEPE voor 10 USDC op {sym}", flush=True)
                res = bybit_post("/v5/order/create", {"category": CATEGORY, "symbol": sym, "side": "Buy", "orderType": "Market", "qty": str(qty)})
                print(f"ORDER RESULT: {res}", flush=True)
            elif sym == "PEPEUSDT" and usdt >= 10 and pepe == 0:
                qty = int((10 / prijs) / qty_step) * qty_step
                print(f"LIVE KOOP {qty} PEPE voor 10 USDT op {sym}", flush=True)
                res = bybit_post("/v5/order/create", {"category": CATEGORY, "symbol": sym, "side": "Buy", "orderType": "Market", "qty": str(qty)})
                print(f"ORDER RESULT: {res}", flush=True)
            elif sym == "PEPEUSDT" and usdc >= 10 and usdt < 10:
                print("ACTIE NODIG: Convert USDC naar USDT in Bybit app, want alleen PEPEUSDT bestaat. Daarna koopt hij vanzelf.", flush=True)
            else:
                print("Wachten: geen saldo om te kopen of al in positie", flush=True)
        except Exception as e:
            print(f"BOT ERROR: {e}", flush=True)
        time.sleep(60)

threading.Thread(target=bot_loop, daemon=True).start()

@app.route("/")
def home():
    return "Pepebot-3 live"

@app.route("/test-key")
def test_key():
    bal, worked, _ = get_balances()
    sym, _ = find_symbol()
    prijs = get_price(sym) if sym else 0
    return jsonify({"werkt_met": worked, "symbool": sym, "USDC": bal.get("USDC", 0), "USDT": bal.get("USDT", 0), "PEPE": bal.get("PEPE", 0), "prijs": prijs})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
