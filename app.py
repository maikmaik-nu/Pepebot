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

BUY_AMOUNT_USDC = 90.0
TAKE_PROFIT_PCT = 10.0
STOP_LOSS_PCT = -5.0
BUY_PRICE = 0.000003485

print(f"KEY LOADED: key_len={len(API_KEY)} secret_len={len(API_SECRET)}", flush=True)

_lock = threading.Lock()
_last_check = 0

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"TELEGRAM SKIP: {msg}", flush=True)
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print(f"TELEGRAM FAIL: {e}", flush=True)

def bybit_get(path, params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    to_sign = ts + API_KEY + recv + query
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sig, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "Content-Type": "application/json"}
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=15)
    return r.json()

def bybit_post(path, params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    body = json.dumps(params)
    to_sign = ts + API_KEY + recv + body
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sig, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "Content-Type": "application/json"}
    r = requests.post(BASE_URL + path, data=body, headers=headers, timeout=15)
    print(f" BYBIT POST {path} -> {r.status_code} {r.text[:500]}", flush=True)
    return r.json()

def get_balances():
    out = {}
    for acct in ["SPOT", "UNIFIED"]:
        try:
            d = bybit_get("/v5/account/wallet-balance", {"accountType": acct})
            if d.get("retCode") != 0:
                print(f"Saldo check {acct}: {d.get('retMsg')}", flush=True)
                continue
            lst = d.get("result", {}).get("list", [])
            if not lst:
                continue
            for coin in lst[0].get("coin", []):
                out[coin["coin"]] = float(coin.get("walletBalance", 0))
        except Exception as e:
            print(f"Saldo check {acct} FAIL: {e}", flush=True)
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
        usdc = bal.get("USDC", 0)
        pepe = bal.get("PEPE", 0)
        price = get_price()
        print(f"CHECK: USDC={usdc} PEPE={pepe} PRICE={price}", flush=True)
        if pepe > 100000:
            pnl_pct = ((price - BUY_PRICE) / BUY_PRICE) * 100
            print(f"HOLD: PEPE={pepe} PnL={pnl_pct:.2f}%", flush=True)
            if pnl_pct >= TAKE_PROFIT_PCT:
                qty_step = get_qty_step()
                qty = round(pepe / qty_step) * qty_step
                res = bybit_post("/v5/order/create", {"category": CATEGORY, "symbol": SYMBOL, "side": "Sell", "orderType": "Market", "qty": str(qty)})
                if res.get("retCode") == 0:
                    send_telegram(f"VERKOCHT met winst! PnL +{pnl_pct:.2f}% Prijs {price}")
                    return {"status": "sold_take_profit", "pnl_pct": pnl_pct}
                return {"status": "sell_failed", "res": res}
            if pnl_pct <= STOP_LOSS_PCT:
                qty_step = get_qty_step()
                qty = round(pepe / qty_step) * qty_step
                res = bybit_post("/v5/order/create", {"category": CATEGORY, "symbol": SYMBOL, "side": "Sell", "orderType": "Market", "qty": str(qty)})
                if res.get("retCode") == 0:
                    send_telegram(f"VERKOCHT met verlies! PnL {pnl_pct:.2f}% Prijs {price}")
                    return {"status": "sold_stop_loss", "pnl_pct": pnl_pct}
                return {"status": "sell_failed", "res": res}
            return {"status": "holding", "pepe": pepe, "pnl_pct": pnl_pct, "price": price}
        if usdc < 5:
            return {"status": "te weinig USDC", "usdc": usdc, "pepe": pepe}
        qty_step = get_qty_step()
        qty = round((BUY_AMOUNT_USDC / price) / qty_step) * qty_step
        print(f"BUY qty={qty} price={price}", flush=True)
        res = bybit_post("/v5/order/create", {"category": CATEGORY, "symbol": SYMBOL, "side": "Buy", "orderType": "Market", "qty": str(qty)})
        if res.get("retCode") == 0:
            send_telegram(f"GEKOCHT: {qty} PEPE voor ~{BUY_AMOUNT_USDC} USDC @ {price}")
            return {"status": "ok", "qty": qty, "price": price}
        return {"status": "buy_failed", "res": res}
    except Exception as e:
        print(f"CHECK FAIL: {e}", flush=True)
        return {"status": "fail", "error": str(e)}
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
           
