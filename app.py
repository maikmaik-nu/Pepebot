import os
import time
import hmac
import hashlib
import json
import threading
import urllib.parse
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
print("KEY LOADED key_len=", len(API_KEY))
print("BASE=", BASE_URL, "SYMBOL=", SYMBOL)
_lock = threading.Lock()
def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        url = "https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage"
        requests.post(url, json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
    except Exception as e:
        print("TELEGRAM FAIL:", e)
def _sign(payload, ts):
    recv = "5000"
    to_sign = ts + API_KEY + recv + payload
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    return sig, recv
def bybit_get(path, params):
    ts = str(int(time.time() * 1000))
    query = urllib.parse.urlencode(sorted(params.items()))
    sig, recv = _sign(query, ts)
    headers = {}
    headers["X-BAPI-API-KEY"] = API_KEY
    headers["X-BAPI-SIGN"] = sig
    headers["X-BAPI-SIGN-TYPE"] = "2"
    headers["X-BAPI-TIMESTAMP"] = ts
    headers["X-BAPI-RECV-WINDOW"] = recv
    headers["Content-Type"] = "application/json"
    r = requests.get(BASE_URL + path + "?" + query, headers=headers, timeout=15)
    return r.json()
def bybit_post(path, body):
    ts = str(int(time.time() * 1000))
    body_str = json.dumps(body, separators=(",", ":"))
    sig, recv = _sign(body_str, ts)
    headers = {}
    headers["X-BAPI-API-KEY"] = API_KEY
    headers["X-BAPI-SIGN"] = sig
    headers["X-BAPI-SIGN-TYPE"] = "2"
    headers["X-BAPI-TIMESTAMP"] = ts
    headers["X-BAPI-RECV-WINDOW"] = recv
    headers["Content-Type"] = "application/json"
    r = requests.post(BASE_URL + path, headers=headers, data=body_str, timeout=15)
    print("【entity-BYBIT¦canonical_name=BYBIT】 POST:", path, r.text[:300])
    return r.json()
def get_balances():
    try:
        d = bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": "USDC,PEPE"})
        if d.get("retCode") != 0:
            print("Saldo UNIFIED:", d.get("retCode"), d.get("retMsg"))
            return {}
        out = {}
        lst = d.get("result", {}).get("list", [])
        if lst:
            for c in lst[0].get("coin", []):
                out[c["coin"]] = float(c.get("walletBalance", 0))
        return out
    except Exception as e:
        print("Saldo FAIL:", e)
        return {}
def get_price():
    r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    return float(r["result"]["list"][0]["lastPrice"])
def get_filters():
    r = requests.get(BASE_URL + "/v5/market/instruments-info", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    f = r["result"]["list"][0]["lotSizeFilter"]
    qty_step = float(f.get("qtyStep") or f.get("basePrecision") or 1)
    min_qty = float(f.get("minOrderQty", 0) or 0)
    max_qty = float(f.get("maxMktOrderQty") or f.get("maxMarketOrderQty") or f.get("maxOrderQty") or 0)
    print("FILTERS step=", qty_step, "min=", min_qty, "max=", max_qty)
    return qty_step, min_qty, max_qty
def do_check():
    if not _lock.acquire(blocking=False):
        return {"status": "already running"}
    try:
        bal = get_balances()
        usdc = bal.get("USDC", 0)
        pepe = bal.get("PEPE", 0)
        print("CHECK USDC=", usdc, "PEPE=", pepe)
        if usdc < 5:
            return {"status": "te weinig USDC", "usdc": usdc, "pepe": pepe}
        price = get_price()
        qty_step, min_qty, max_qty = get_filters()
        qty = int((usdc * 0.9 / price) / qty_step) * qty_step
        if max_qty > 0:
            qty = min(qty, int(max_qty * 0.99 / qty_step) * qty_step)
        if qty < min_qty:
            return {"status": "qty te klein", "qty": qty}
        print("BUY qty=", qty, "price=", price)
        body = {}
        body["category"] = CATEGORY
        body["symbol"] = SYMBOL
        body["side"] = "Buy"
        body["orderType"] = "Market"
        body["qty"] = str(qty)
        body["marketUnit"] = "baseCoin"
        res = bybit_post("/v5/order/create", body)
        if res.get("retCode") == 0:
            msg = "GEKOCHT: " + str(qty) + " PEPE"
            send_telegram(msg)
            return {"status": "ok", "qty": qty, "price": price}
        send_telegram("Buy mislukt")
        return {"status": "buy failed", "res": res}
    except Exception as e:
        print("CHECK FAIL:", e)
        return {"status": "error", "error": str(e)}
    finally:
        _lock.release()
def bot_loop():
    time.sleep(10)
    while True:
        try:
            print("AUTO CHECK")
            print(do_check())
        except Exception as e:
            print("LOOP FAIL:", e)
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
        price = "FAIL"
    return jsonify({"key_len": len(API_KEY), "balances": bal, "price": price})
@app.route("/check")
def check():
    return jsonify(do_check())
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
