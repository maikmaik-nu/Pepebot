import os, time, hmac, hashlib, requests, threading, traceback
from flask import Flask, jsonify, request

app = Flask(__name__)

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDC"
CATEGORY = "spot"

UT_FACTOR = 4.0
UT_PERIOD = 14
ST_PERIOD = 10
ST_MULT = 3.0
KLINE_INTERVAL = "15"
KLINE_LIMIT = 200
CHECK_INTERVAL = 60
USE_PERCENT = 0.95

last_telegram = 0
last_uur_msg = 0
CHECK_RUNNING = False
bot_started_at = time.time()
total_checks = 0
total_buys = 0
total_sells = 0
last_signal = {}
last_error = ""

def send_msg(msg, force=False):
    global last_telegram
    now = time.time()
    if not force and "UUR" not in msg and now - last_telegram < 3600:
        return False
    last_telegram = now
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"TELEGRAM (geen token): {msg}", flush=True)
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
        return True
    except Exception as e:
        print(f"Telegram fout: {e}", flush=True)
        return False

def send_uur_update(usdc, pepe, price, ut_up, st_up, ut_stop, st_val):
    global last_uur_msg
    now = time.time()
    if now - last_uur_msg < 3600:
        return
    last_uur_msg = now
    send_msg(f"UUR UPDATE Pepebot-3\nUSDC: {usdc}\nPEPE: {pepe}\nPrijs: {price}\nSymbol: {SYMBOL}\nUT: {'UP' if ut_up else 'DOWN'} (stop {ut_stop})\nST: {'UP' if st_up else 'DOWN'} (val {st_val})", force=True)

def sign(params):
    timestamp = str(int(time.time() * 1000))
    recv = "5000"
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    to_sign = timestamp + API_KEY + recv + query
    signature = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": signature,
        "Content-Type": "application/json"
    }

def get_balance():
    try:
        params = {"accountType": "SPOT"}
        headers = sign(params)
        r = requests.get(f"{BASE_URL}/v5/account/wallet-balance", params=params, headers=headers, timeout=10)
        data = r.json()
        if data.get("retCode") != 0:
            print(f"Balance fail: {data}", flush=True)
            return None, None
        coins = data["result"]["list"][0]["coin"]
        usdc = 0.0
        pepe = 0.0
        for c in coins:
            if c["coin"] == "USDC":
                usdc = float(c.get("walletBalance", 0))
            if c["coin"] == "PEPE":
                pepe = float(c.get("wallet
