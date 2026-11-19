import os
import time
import hmac
import hashlib
import json
import threading
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

# Hoeveel USDT per koop, en om de hoeveel sec checken
BUY_USDT = 10.0
CHECK_INTERVAL = 300  # 5 min

print(f"KEY LOADED: key_len={len(API_KEY)} secret_len={len(API_SECRET)} base={BASE_URL} symbol={SYMBOL} category={CATEGORY}", flush=True)

_lock = threading.Lock()

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"TELEGRAM SKIP: {msg}", flush=True)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception as e:
        print(f"TELEGRAM FAIL: {e}", flush=True)

def _sign_get(params):
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
        "Content-Type": "application/json",
    }
    return headers

def _sign_post(body_dict):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    body_str = json.dumps(body_dict)
    to_sign = ts + API_KEY + recv + body_str
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json",
    }
    return headers, body_str

def bybit_get(path, params):
    headers = _sign_get(params)
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=15)
    print(f"【entity-BYBIT¦canonical_name=BYBIT】 GET {path} -> {r.status_code} {r.text[:500]}", flush=True)
    try:
        return r.json()
    except Exception:
        return {"retCode": -1, "retMsg": f"no json: {r.text[:200]}"}

def bybit_post(path, body):
    headers, body_str = _sign_post(body)
    r = requests.post(BASE_URL + path, data=body_str, headers=headers, timeout=15)
    print(f"【entity-BYBIT¦canonical_name=BYBIT】 POST {path} -> {r.status_code} {r.text[:500]}", flush=True)
    try:
        return r.json()
    except Exception:
        return {"retCode": -1, "retMsg": f"no json: {r.text[:200]}"}

def get_balances():
    out = {}
    for acct in ["SPOT", "UNIFIED"]:
        try:
            d = bybit_get("/v5/account/wallet-balance", {"accountType": acct})
            if d.get("retCode") != 0:
                print(f"Saldo check {acct}: retCode={d.get('retCode')} retMsg={d.get('retMsg')}", flush=True)
                continue
            lst = d.get("result", {}).get("list", [])
            if not lst:
                continue
            for coin in lst[0].get("coin", []):
                # Laatste waarde wint, SPOT en UNIFIED staan apart maar je geld staat op 1 plek
                val = float(coin.get("walletBalance", 0))
                if coin["coin"] not in out or val > 0:
                    out[coin["coin"]] = val
                print(f"Saldo {acct} {coin['coin']} = {coin.get('walletBalance')}", flush=True)
        except Exception as e:
            print(f"Saldo check {acct} FAIL: {e}", flush=True)
    return out

def get_price():
    r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    return float(r["result"]["list"][0]["lastPrice"])

def get_filters():
    r = requests.get(BASE_URL + "/v5/market/instruments-info", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    f = r["result"]["list"][0]["lotSizeFilter"]
    return float(f["qtyStep"]), float(f.get("minOrderQty", 0))

def place_buy(usdt_amount):
    price = get_price()
    qty_step, min_qty = get_filters()
    raw_qty = usdt_amount / price
    qty = int(raw_qty / qty_step) * qty_step
    # Voorkom float-rommel: rond af op aantal decimalen van qtyStep
    decimals = len(str(qty_step).split(".")[-1]) if "." in str(qty_step) else 0
    qty = round(qty, decimals)
    print(f"BUY: price={price} qtyStep={qty_step} min={min_qty} qty={qty}", flush=True)
    if qty < min_qty:
        return {"status": "qty te klein", "qty": qty, "min": min_qty}
    body = {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": "Buy",
        "orderType": "Market",
        "qty": str(qty),
    }
    res = bybit_post("/v5/order/create", body)
    if res.get("retCode") == 0:
        send_telegram(f"PEPE gekocht: {qty} PEPE voor ~{usdt_amount} USDT @ {price}")
    return res

def do_check():
    if not _lock.acquire(blocking=False):
        return {"status": "already running"}
    try:
        bal = get_balances()
        usdt = bal.get("USDT", 0)
        pepe = bal.get("PEPE", 0)
        print(f"CHECK: USDT={usdt} PEPE={pepe}", flush=True)
        if usdt < 5:
            print("CHECK: te weinig USDT, skip", flush=True)
            return {"status": "te weinig USDT", "usdt": usdt, "pepe": pepe}
        price = get_price()
        print(f"CHECK: price={price}", flush=True)
        # Simpele logica: zolang je USDT hebt, koop voor BUY_USDT
        # Pas dit later aan naar je eigen strategie
        res = place_buy(min(BUY_USDT, usdt))
        return {"status": "buy gedaan", "usdt": usdt, "pepe": pepe, "price": price, "order": res}
    except Exception as e:
        print(f"CHECK FAIL: {e}", flush=True)
        return {"status": f"fail {e}"}
    finally
