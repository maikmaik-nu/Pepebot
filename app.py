import os, time, requests, hmac, hashlib, json, threading
from flask import Flask, request, jsonify
app = Flask(__name__)

BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
INTERVAL = "15"
KEY_VALUE = 4
ATR_PERIOD = 14
BASE_URL = "https://api.bybit.com"

def log(msg):
    print(msg, flush=True)

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        log(f"Telegram error: {e}")

def _sign(ts, recv, payload=""):
    msg = ts + BYBIT_API_KEY + recv + payload
    return hmac.new(BYBIT_API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

def _headers(ts, recv, sign):
    return {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json"
    }

def _extract_list(result):
    if isinstance(result, dict):
        return result.get("list", []) or []
    if isinstance(result, list):
        return result
    return []

def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
        if r.get("retCode") != 0:
            log(f"Prijs error retCode: {r}")
            return 0
        lst = _extract_list(r.get("result", {}))
        if not lst:
            log(f"Prijs error geen list: {r}")
            return 0
        return float(lst[0].get("lastPrice") or 0)
    except Exception as e:
        log(f"Prijs error: {e}")
        return 0

def get_balance(coin):
    for acct in ["UNIFIED", "SPOT", "FUND"]:
        try:
            if not BYBIT_API_KEY or not BYBIT_API_SECRET:
                return 0.0
            ts = str(int(time.time() * 1000))
            recv = "5000"
            query = f"accountType={acct}&coin={coin}"
            sign = _sign(ts, recv, query)
            url = f"{BASE_URL}/v5/account/wallet-balance?{query}"
            r = requests.get(url, headers=_headers(ts, recv, sign), timeout=10).json()
            if r.get("retCode") != 0:
                log(f"Saldo error {coin} {acct}: {r}")
                continue
            lst = _extract_list(r.get("result", {}))
            if not lst:
                continue
            coins = lst[0].get("coin", []) or []
            for c in coins:
                if c.get("coin") == coin:
                    return float(c.get("walletBalance") or 0)
        except Exception as e:
            log(f"Saldo error {coin} {acct}: {e}")
            continue
    return 0.0

def get_free_usdt():
    return get_balance("USDT")

def get_pepe_balance():
    return get_balance("PEPE")

def is_in_positie():
    prijs = get_price()
    if prijs <= 0:
        return get_pepe_balance() > 0
    return get_pepe_balance() * prijs > 1

def place_order(side, qty):
    try:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        body = {"category": CATEGORY, "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty)}
        payload = json.dumps(body)
        sign = _sign(ts, recv, payload)
        r = requests.post(f"{BASE_URL}/v5/order/create", headers=_headers(ts, recv, sign), data=payload, timeout=10).json()
        log(f"Order {side} {qty}: {r}")
        return r
    except Exception as e:
        log(f"Order error: {e}")
        return {}

def do_buy():
    usdt = get_free_usdt()
    prijs = get_price()
    if usdt < 5 or prijs <= 0:
        log(f"BUY: te weinig USDT ({usdt}) of geen prijs ({prijs})")
        return
    qty = int(usdt / prijs)
    if qty <= 0:
        return
    log(f"BUY: {qty} PEPE voor {usdt} USDT @ {prijs}")
    place_order("Buy", qty)
    send_telegram(f"BUY: {qty} PEPE @ {prijs} - USDT: {usdt}")

def do_sell():
    pepe = get_pepe_balance()
    prijs = get_price()
    if pepe <= 0 or prijs <= 0:
        log(f"SELL: geen PEPE ({pepe}) of geen prijs ({prijs})")
        return
    qty = int(pepe)
    log(f"SELL: {qty} PEPE @ {prijs}")
    place_order("Sell", qty)
    send_telegram(f"SELL: {qty} PEPE @ {prijs}")

def get_klines():
    try:
        r = requests.get(f"{
