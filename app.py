import os, time, hmac, hashlib, threading, requests
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = os.getenv("BASE_URL", "https://api.bybit.eu")
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
SYMBOL = "PEPEUSDT"
running = False

def log(msg):
    print(msg, flush=True)

def send_tg(msg):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
    except Exception as e:
        log(f"Telegram fout: {e}")

def get_json(url, headers=None, params=None):
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if not r.text or not r.text.strip().startswith("{"):
            log(f"LEEG ANTWOORD {url} status {r.status_code}: {r.text[:200]}")
            return None
        return r.json()
    except Exception as e:
        log(f"get_json fout: {e}")
        return None

def sign(params):
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    return hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()

def get_balance():
    try:
        ts = str(int(time.time() * 1000))
        p = {"api_key": API_KEY, "timestamp": ts, "recv_window": "5000", "accountType": "SPOT"}
        p["sign"] = sign(p)
        h = {"X-BAPI-API-KEY": API_KEY}
        d = get_json(f"{BASE_URL}/v5/account/wallet-balance", headers=h, params=p)
        if not d or d.get("retCode") != 0:
            log(f"Balance fail: {d}")
           
