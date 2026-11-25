import os, time, hmac, hashlib, json, threading, urllib.parse
import requests
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
TG_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDC"
CATEGORY = "spot"

print(f"KEY LOADED: key_len={len(API_KEY)} base={BASE_URL} symbol={SYMBOL}", flush=True)

_lock = threading.Lock()

def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
    except Exception as e:
        print(f"TELEGRAM FAIL: {e}", flush=True)

def _sign(payload, ts):
    recv = "5000"
    to_sign = ts + API_KEY + recv + payload
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    return sig, recv

def bybit_get(path, params):
    ts = str(int(time.time() * 1000))
    query = urllib.parse.urlencode(sorted(params.items()))
    sig, recv = _sign(query, ts)
    headers = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sig,
               "X-BAPI-SIGN-TYPE": "2", "X-BAPI-TIMESTAMP": ts,
               "X-BAPI-RECV-WINDOW": recv, "Content-Type": "application/json"}
    r = requests.get(BASE_URL + path + "?" + query, headers=headers, timeout=15)
    return r.json()

def bybit_post(path, body):
    ts = str(int(time.time() * 1000))
    body_str = json.dumps(body, separators=(",", ":"))
    sig, recv = _sign(body_str, ts)
    headers = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sig,
               "X-BAPI-SIGN-TYPE": "2", "X-BAPI-TIMESTAMP": ts,
               "X-BAPI-RECV-WINDOW": recv, "Content-Type": "application/json"}
    r = requests.post(BASE_URL + path, headers=headers, data=body_str, timeout=15)
    print(f"BYBIT POST {path} -> {r.text[:300]}", flush=True)
    return r.json()

def get_balances():
    out = {}
    for acct in ["SPOT", "UNIFIED"]:
        try:
            d = bybit_get("/v5/account/wallet-balance", {"accountType": acct, "coin": "USDC,PEPE"})
            if d.get("retCode") != 0:
                print(f"Saldo {acct}: {d.get('retCode')} {d.get('retMsg')}", flush=True)
                continue
            lst = d.get("result", {}).get("list", [])
            if not lst:
                continue
            for c in lst[0].get("coin", []):
                out[c["coin"]] = out.get(c["coin"], 0) + float(c.get("walletBalance", 0))
        except Exception as e:
            print(f"Saldo {acct} FAIL: {e}", flush=True)
    return out

def get_price():
    r = requests.get(BASE_URL + "/v5/market/tickers",
        params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    return float(r["result"]["list"][0]["lastPrice"])

def get_filters():
    r = requests.get(BASE_URL + "/v5/market/instruments-info",
        params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    f = r["result"]["list"][0]["lotSizeFilter"]
    return float(f["qtyStep"]), float(f.get("minOrderQty", 0)), float(f.get("maxMktOrderQty", 0) or f.get("maxOrderQty", 0))

def do_check():
    if not _lock.acquire(blocking=False):
        return {"status": "already running"}
    try:
        bal = get_balances()
        usdc = bal.get("USDC", 0)
        pepe = bal.get("PEPE", 0)
        print(f"CHECK: USDC={usdc} PEPE={pepe}", flush=True)
        if usdc < 5:
            return {"status": "te weinig USDC", "usdc": usdc, "pepe": pepe}
        price = get_price()
        qty_step, min_qty, max_qty = get_filters()
        # Koop voor 90% van je USDC, nooit boven de Bybit-max
        qty = int((usdc * 0.9 / price) / qty_step) * qty_step
        if max_qty > 0:
            qty = min(qty, int(max_qty * 0.99 / qty_step) * qty_step)
        if qty < min_qty:
            return {"status": "qty te klein", "qty": qty}
        print(f"BUY PEPEUSDC qty={qty} voor ~{usdc*0.9} USDC @ {price}", flush=True)
        body = {"category": CATEGORY, "symbol": SYMBOL, "side": "Buy",
                "orderType": "Market", "qty": str(qty), "marketUnit": "baseCoin"}
        res = bybit_post("/v5/order/create", body)
        if res.get("retCode") == 0:
            send_telegram(f"GEKOCHT: {qty} PEPE voor ~{round(usdc*0.9,2)} USDC @ {price}")
            return {"status": "ok", "qty": qty, "price": price}
        send_telegram(f"Buy mislukt: {res.get('retMsg')}")
        return {"status": "buy failed", "res": res}
    except Exception as e:
        print(f"CHECK FAIL: {e}", flush=True)
        return {"status": "error", "error": str(e)}
    finally:
        _lock.release()

def bot_loop():
    time.sleep(10)
    while True:
        try:
            print("AUTO CHECK", flush=True)
            print(do_check(), flush=True)
        except Exception as e:
            print(f"LOOP FAIL: {e}", flush=True)
        time.sleep(300)

threading.Thread(target=bot_loop, daemon=True).start()

@app.route("/")
def home():
    return "Pepebot live"

@app.route("/test-key")
def test_key():
    bal = get_balances()
    try:
        price = get_price()
    except Exception as e:
        price = f"FAIL {e}"
    return jsonify({"key_len": len(API_KEY), "balances": bal, "price": price})

@app.route("/check")
def check():
    return jsonify(do_check())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
