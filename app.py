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
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
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
    headers = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sig, "X-BAPI-SIGN-TYPE": "2", "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "Content-Type": "application/json"}
    r = requests.get(BASE_URL + path + "?" + query, headers=headers, timeout=15)
    return r.json()
def bybit_post(path, body):
    ts = str(int(time.time() * 1000))
    body_str = json.dumps(body, separators=(",", ":"))
    sig, recv = _sign(body_str, ts)
    headers = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sig, "X-BAPI-SIGN-TYPE": "2", "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "Content-Type": "application/json"}
    r = requests.post(BASE_URL + path, headers=headers, data=body_str, timeout=15)
    print(f"BYBIT POST {path} -> {r.text[:300]}", flush=True)
    return r.json()
def get_balances():
    try:
        d = bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": "USDC,PEPE"})
        if d.get("retCode") != 0:
            print(f"Saldo UNIFIED: {d.get('retCode')} {d.get('retMsg')}", flush=True)
            return {}
        out = {}
        lst = d.get("result", {}).get("list", [])
        if lst:
            for c in lst[0].get("coin", []):
                out[c["coin"]] = float(c.get("walletBalance", 0))
        return out
    except Exception as e:
        print(f"Saldo FAIL: {e}", flush=True)
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
    print(f"FILTERS: step={qty_step} min={min_qty} max={max_qty}", flush=True)
    return qty_step, min_qty, max_qty
def do_check():
    if not _lock.acquire(blocking=False):
        return {"status": "already running"}
    try:
        bal = get_balances()
        usdc = bal.get("USDC", 0)
        pepe = bal.get("PEPE", 0)
        print(f"CHECK: USDC={
