import os, time, hmac, hashlib, json, threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

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

def bybit_get(path, params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    to_sign = ts + API_KEY + recv + query
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json"
    }
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=15)
    print(f"BYBIT GET {path} -> {r.status_code} {r.text[:500]}", flush=True)
    return r.json()

def get_balances():
    out = {}
    try:
        d = bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        print(f"BYBIT BALANCE RAW: {d}", flush=True)

        if d.get("retCode") != 0:
            print(f"Saldo check UNIFIED: retCode={d.get('retCode')} retMsg={d.get('retMsg')}", flush=True)
            return out

        result = d.get("result", {})
        # Bybit geeft soms result = {"list": [...]}, soms result = [...]
        if isinstance(result, list):
            lst = result
        else:
            lst = result.get("list", [])

        if not lst:
            print("Saldo check UNIFIED: lege list", flush=True)
            return out

        for coin in lst[0].get("coin", []):
            out[coin["coin"]] = float(coin.get("walletBalance", 0))
            print(f"Saldo UNIFIED {coin['coin']} = {coin.get('walletBalance')}", flush=True)

    except Exception as e:
        print(f"Saldo check UNIFIED FAIL: {e}", flush=True)
    return out

def get_price():
    r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    return float(r["result"]["list"][0]["lastPrice"])

def get_qty_step():
    r = requests.get(BASE_URL + "/v5/market/instruments-info", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    return float(r["result"]["list"][0]["lotSizeFilter"]["qtyStep"])

def do_check():
    global _last_check
    now = time.time()
    if not _lock.acquire(blocking=False):
        return {"status": "already running"}
    try:
        if now - _last_check < 30:
            return {"status": "too soon, wait 30s"}
        _last_check = now
        bal = get_balances()
        usdt = bal.get("USDT", 0)
        pepe = bal.get("PEPE", 0)
        print(f"CHECK: USDT={usdt} PEPE={pepe}", flush=True)
        if usdt < 5:
            send_telegram(f"Bot stopt: te weinig USDT ({usdt})")
            return {"status": "te weinig USDT", "usdt": usdt, "pepe": pepe}
        price = get_price()
        qty_step = get_qty_step()
        qty = round((10 / price) / qty_step) * qty_step
        print(f"CHECK OK: price={price} qtyStep={qty_step} would_buy_qty={qty}", flush=True)
        return {"status": "ok", "usdt": usdt, "pepe": pepe, "price": price, "would_buy_qty": qty}
    finally:
        _lock.release()

def bot_loop():
    time.sleep(10)
    while True:
        try:
            print("AUTO CHECK gestart", flush=True)
            res = do_check()
            print(f"AUTO CHECK klaar: {res}", flush=True)
        except Exception as e:
            print(f"AUTO CHECK FAIL: {e}", flush=True)
        time.sleep(300)

threading.Thread(target=bot_loop, daemon=True).start()

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
    return jsonify(do_check())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
