import os, time, hmac, hashlib, json, threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# --- CONFIG ---
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

print(f"KEY LOADED: key_len={len(API_KEY)} secret_len={len(API_SECRET)} base={BASE_URL} symbol={SYMBOL} category={CATEGORY}", flush=True)

_lock = threading.Lock()
_last_check = 0

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"TELEGRAM SKIP: {msg}", flush=True)
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print(f"TELEGRAM FAIL: {e}", flush=True)

def sign(params):
    ts = str(int(time.time()*1000))
    params = dict(params)
    params["api_key"] = API_KEY
    params["timestamp"] = ts
    params["recv_window"] = "5000"
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sig = hmac.new(API_SECRET.encode(), (ts + API_KEY + "5000" + query).encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000",
        "Content-Type": "application/json"
    }
    return params, headers

def bybit_get(path, params):
    p, h = sign(params)
    r = requests.get(BASE_URL + path, params=p, headers=h, timeout=15)
    print(f"【entity-BYBIT¦canonical_name=Bybit】 GET {path} -> {r.status_code} {r.text[:300]}", flush=True)
    return r.json()

def get_balances():
    # GEEN coin param! Alleen accountType. Dit was de bug.
    out = {}
    for acct in ["SPOT", "UNIFIED"]:
        try:
            d = bybit_get("/v5/account/wallet-balance", {"accountType": acct})
            if d.get("retCode") != 0:
                print(f"Saldo check {acct}: retCode={d.get('retCode')} retMsg={d.get('retMsg')}", flush=True)
                continue
            for coin in d["result"]["list"][0]["coin"]:
                out[coin["coin"]] = float(coin.get("walletBalance", 0))
                print(f"Saldo {acct} {coin['coin']} = {coin.get('walletBalance')}", flush=True)
        except Exception as e:
            print(f"Saldo check {acct} FAIL: {e}", flush=True)
    return out

def get_price():
    r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    return float(r["result"]["list"][0]["lastPrice"])

def get_qty_step():
    r = requests.get(BASE_URL + "/v5/market/instruments-info", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    return float(r["result"]["list"][0]["lotSizeFilter"]["qtyStep"])

@app.route("/")
def home():
    return "Pepebot-3 live"

@app.route("/test-key")
def test_key():
    bal = get_balances()
    try:
        price = get_price()
    except Exception as e:
        price = f"FAIL {e}"
    return jsonify({"key_len": len(API_KEY), "secret_len": len(API_SECRET), "base": BASE_URL, "balances": bal, "PEPEUSDT_price": price})

@app.route("/check")
def check():
    global _last_check
    now = time.time()
    # Kan niet dubbel draaien
    if not _lock.acquire(blocking=False):
        return jsonify({"status": "already running"})
    try:
        if now - _last_check < 30:
            return jsonify({"status": "too soon, wait 30s"})
        _last_check = now
        bal = get_balances()
        usdt = bal.get("USDT", 0)
        pepe = bal.get("PEPE", 0)
        print(f"CHECK: USDT={usdt} PEPE={pepe}", flush=True)
        if usdt < 5:
            send_telegram(f"Bot stopt: te weinig USDT ({usdt})")
            return jsonify({"status": "te weinig USDT", "usdt": usdt, "pepe": pepe})
        price = get_price()
        qty_step = get_qty_step()
        # Voorbeeld: koop voor 10 USDT, afronden op qtyStep
        qty = round((10 / price) / qty_step) * qty_step
        print(f"CHECK OK: price={price} qtyStep={qty_step} would_buy_qty={qty}", flush=True)
        return jsonify({"status": "ok", "usdt": usdt, "pepe": pepe, "price": price, "would_buy_qty": qty})
    finally:
        _lock.release()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
