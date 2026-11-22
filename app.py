import os
import time
import hmac
import hashlib
import json
import requests
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = "https://api.bybit.eu"
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()

SYMBOL = "PEPEUSDC"
QUOTE = "USDC"
BASE = "PEPE"
MIN_BUY = 10.0

# ---------- Bybit helpers ----------

def sign(timestamp, recv_window, payload):
    msg = timestamp + API_KEY + recv_window + payload
    return hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

def bybit_get(path, params):
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sign(timestamp, recv_window, query),
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
    }
    try:
        r = requests.get(BASE_URL + path + "?" + query, headers=headers, timeout=10)
    except Exception as e:
        return 0, {"retCode": -1, "retMsg": f"Request fout: {e}"}
    if not r.text.strip():
        return r.status_code, {"retCode": -1, "retMsg": f"Lege body, HTTP {r.status_code}"}
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"retCode": -1, "retMsg": f"Geen JSON: {r.text[:200]}"}

def bybit_post(path, body):
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    payload = json.dumps(body, separators=(",", ":"))
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sign(timestamp, recv_window, payload),
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(BASE_URL + path, headers=headers, data=payload, timeout=10)
    except Exception as e:
        return 0, {"retCode": -1, "retMsg": f"Request fout: {e}"}
    if not r.text.strip():
        return r.status_code, {"retCode": -1, "retMsg": f"Lege body, HTTP {r.status_code}"}
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {"retCode": -1, "retMsg": f"Geen JSON: {r.text[:200]}"}

def get_price():
    # Public endpoint, geen signature nodig
    try:
        r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        data = r.json()
        return float(data["result"]["list"][0]["lastPrice"])
    except Exception:
        return 0.0

def get_balance():
    # ALLEEN UNIFIED - SPOT mag niet bij wallet-balance op Bybit EU
    status, data = bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
    print(f"Saldo check UNIFIED: status={status} data={data}", flush=True)
    if status == 200 and data.get("retCode") == 0:
        try:
            coins = data["result"]["list"][0]["coin"]
            balances = {c["coin"]: float(c.get("walletBalance", 0) or 0) for c in coins}
            quote = balances.get(QUOTE, 0.0)
            base = balances.get(BASE, 0.0)
            return quote, base, 0, "UNIFIED"
        except Exception as e:
            print(f"Balance parse fout: {e}", flush=True)
    else:
        print(f"BALANCE FAIL UNIFIED: status={status} retCode={data.get('retCode')} msg={data.get('retMsg')}", flush=True)
    return 0.0, 0.0, -1, "NONE"

def place_order(side, qty):
    body = {
        "category": "spot",
        "symbol": SYMBOL,
        "side": side,
        "orderType": "Market",
        "qty": str(qty),
    }
    status, data = bybit_post("/v5/order/create", body)
    print(f"ORDER {side} {qty} -> status={status} data={data}", flush=True)
    return status, data

# ---------- Routes ----------

@app.route("/")
def home():
    quote, base, retcode, account = get_balance()
    price = get_price()
    in_position = base * price >= 5.0
    print(f"CHECK: {QUOTE}={quote} {BASE}={base} price={price} in_position={in_position} retCode={retcode} account={account}", flush=True)

    if retcode != 0:
        return jsonify({"status": "geen saldo / geen rechten", "retCode": retcode, "account": account, "price": price})

    action = "niks"
    if not in_position and quote >= MIN_BUY:
        qty = int((quote / price) * 0.99)
        if qty > 0:
            status, data = place_order("Buy", qty)
            action = f"Buy {qty} -> {data.get('retMsg')}"
    elif in_position:
        action = "in positie, niks doen"

    return jsonify({
        "status": "ok",
        "account": account,
        f"{QUOTE}": quote,
        f"{BASE}": base,
        "price": price,
        "in_position": in_position,
        "action": action,
    })

@app.route("/test-key")
def test_key():
    quote, base, retcode, account = get_balance()
    return jsonify({
        "api_key_set": bool(API_KEY),
        "key_prefix": API_KEY[:4] + "..." if API_KEY else None,
        "account_used": account,
        "retCode": retcode,
        "balances": {QUOTE: quote, BASE: base},
        "price": get_price(),
    })

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
