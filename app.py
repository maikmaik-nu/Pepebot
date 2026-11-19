import os
import time
import hmac
import hashlib
import json
import threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()

print("KEY LOADED key_len=" + str(len(API_KEY)) + " secret_len=" + str(len(API_SECRET)), flush=True)

def sign_get(params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    parts = []
    for k in sorted(params.keys()):
        parts.append(k + "=" + str(params[k]))
    query = "&".join(parts)
    msg = ts + API_KEY + recv + query
    sig = hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    headers = {}
    headers["X-BAPI-API-KEY"] = API_KEY
    headers["X-BAPI-SIGN"] = sig
    headers["X-BAPI-TIMESTAMP"] = ts
    headers["X-BAPI-RECV-WINDOW"] = recv
    headers["Content-Type"] = "application/json"
    return headers

def sign_post(body):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    body_str = json.dumps(body)
    msg = ts + API_KEY + recv + body_str
    sig = hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    headers = {}
    headers["X-BAPI-API-KEY"] = API_KEY
    headers["X-BAPI-SIGN"] = sig
    headers["X-BAPI-TIMESTAMP"] = ts
    headers["X-BAPI-RECV-WINDOW"] = recv
    headers["Content-Type"] = "application/json"
    return headers, body_str

def bybit_get(path, params):
    headers = sign_get(params)
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=15)
    print("【entity-BYBIT¦canonical_name=Bybit】 GET " + path + " -> " + str(r.status_code) + " " + r.text[:400], flush=True)
    return r.json()

def bybit_post(path, body):
    headers, body_str = sign_post(body)
    r = requests.post(BASE_URL + path, data=body_str, headers=headers, timeout=15)
    print("BYBIT POST " + path + " -> " + str(r.status_code) + " " + r.text[:400], flush=True)
    return r.json()

def get_balances():
    out = {}
    for acct in ["SPOT", "UNIFIED"]:
        d = bybit_get("/v5/account/wallet-balance", {"accountType": acct})
        if d.get("retCode") != 0:
            print("Saldo " + acct + " retCode=" + str(d.get("retCode")) + " " + str(d.get("retMsg")), flush=True)
            continue
        lst = d.get("result", {}).get("list", [])
        if len(lst) == 0:
            continue
        for coin in lst[0].get("coin", []):
            out[coin["coin"]] = float(coin.get("walletBalance", 0))
            print("Saldo " + acct + " " + coin["coin"] + " = " + str(coin.get("walletBalance")), flush=True)
    return out

def get_price():
    r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    return float(r["result"]["list"][0]["lastPrice"])

def do_check():
    bal = get_balances()
    usdt = bal.get("USDT", 0)
    pepe = bal.get("PEPE", 0)
    print("CHECK USDT=" + str(usdt) + " PEPE=" + str(pepe), flush=True)
    if usdt < 5:
        return {"status": "te weinig USDT", "usdt": usdt, "pepe": pepe}
    price = get_price()
    print("CHECK price=" + str(price), flush=True)
    r = requests.get(BASE_URL + "/v5/market/instruments-info", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    filt = r["result"]["list"][0]["lotSizeFilter"]
    qty_step = float(filt["qtyStep"])
    min_qty = float(filt.get("minOrderQty", 0))
    raw = 10.0 / price
    qty = int(raw / qty_step) * qty_step
    qty = round(qty, 8)
    print("BUY qty=" + str(qty) + " min=" + str(min_qty), flush=True)
    if qty < min_qty:
        return {"status": "qty te klein", "qty": qty}
    body = {"category": CATEGORY, "symbol": SYMBOL, "side": "Buy", "orderType": "Market", "qty": str(qty)}
    res = bybit_post("/v5/order/create", body)
    return {"status": "buy gedaan", "usdt": usdt, "pepe": pepe, "price": price, "order": res}

def bot_loop():
    time.sleep(10)
    print("BOT LOOP GESTART", flush=True)
    while True:
        print("AUTO CHECK gestart", flush=True)
        try:
            res = do_check()
            print("AUTO CHECK klaar " + str(res), flush=True)
        except Exception as e:
            print("AUTO CHECK FAIL " + str(e), flush=True)
        time.sleep(300)

threading.Thread(target=bot_loop, daemon=True).start()

@app.route("/")
def home():
    return "Pepebot-3 live"

@app.route("/test-key")
def test_key():
    bal = get_balances()
    price = 0
    try:
        price = get_price()
    except Exception as e:
        price = str(e)
    return jsonify({"key_len": len(API_KEY), "secret_len": len(API_SECRET), "balances": bal, "price": price})

@app.route("/check")
def check():
    return jsonify(do_check())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
