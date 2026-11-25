import os, time, hmac, hashlib, json, threading, requests
from flask import Flask, jsonify
app = Flask(__name__)
BASE_URL = os.getenv("BASE_URL", "https://api.bybit.eu").strip()
API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()
BUY_AMOUNT = 100.0
CHECK_SECONDS = 60
running = False
def log(msg):
    print(msg, flush=True)
def send_tg(msg):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
    except Exception as e:
        log(f"Telegram fout: {e}")
def make_sign(timestamp, recv_window, payload_str):
    msg = timestamp + API_KEY + recv_window + payload_str
    return hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        log(f"Geen JSON terug, status {resp.status_code}, body: {resp.text[:300]}")
        return {}
def headers_for(ts, recv, sign):
    return {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "X-BAPI-SIGN": sign, "X-BAPI-SIGN-TYPE": "2"}
def get_balance_unified(coin):
    ts = str(int(time.time() * 1000)); recv = "20000"; query = "accountType=UNIFIED&coin=" + coin; sign = make_sign(ts, recv, query)
    r = safe_json(requests.get(BASE_URL + "/v5/account/wallet-balance?" + query, headers=headers_for(ts, recv, sign), timeout=10))
    if r.get("retCode") != 0:
        log(f"Balance UNIFIED fail {coin}: {r}"); return 0.0
    lst = r.get("result", {}).get("list", []) or []
    if not lst: return 0.0
    for c in lst[0].get("coin", []) or []:
        if c.get("coin") == coin: return float(c.get("walletBalance") or 0)
    return 0.0
def get_balance_funding(coin):
    ts = str(int(time.time() * 1000)); recv = "20000"; query = "accountType=FUND&coin=" + coin; sign = make_sign(ts, recv, query)
    r = safe_json(requests.get(BASE_URL + "/v5/asset/transfer/query-account-coins-balance?" + query, headers=headers_for(ts, recv, sign), timeout=10))
    if r.get("retCode") != 0:
        log(f"Balance FUND fail {coin}: {r}"); return 0.0
    res = r.get("result", {})
    lst = res.get("list", []) if isinstance(res, dict) else res if isinstance(res, list) else []
    if isinstance(res, dict) and not lst and res.get("coin"): lst = [res]
    for c in lst:
        if isinstance(c, dict) and c.get("coin") == coin: return float(c.get("balance") or c.get("walletBalance") or 0)
    return 0.0
def get_balance():
    try:
        if not API_KEY or not API_SECRET:
            log("Balance fout: geen API key in Render"); return 0.0, 0.0, 0.0
        usdc = get_balance_unified("USDC"); usdt = get_balance_unified("USDT"); pepe = get_balance_unified("PEPE")
        if usdc == 0: usdc = get_balance_funding("USDC")
        if usdt == 0: usdt = get_balance_funding("USDT")
        if pepe == 0: pepe = get_balance_funding("PEPE")
        return usdt, usdc, pepe
    except Exception as e:
        log(f"Balance fout: {e}"); return 0.0, 0.0, 0.0
def get_klines(symbol):
    try:
        r = safe_json(requests.get(BASE_URL + "/v5/market/kline", params={"category": "spot", "symbol": symbol, "interval": "15", "limit": 200}, timeout=10))
        if r.get("ret
