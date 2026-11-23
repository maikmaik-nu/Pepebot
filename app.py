"""
PEPEBOT-3 - DEFINITIEVE VERSIE
- Web Service op Render (gratis), NIET Background Worker
- Start: gunicorn app:app --workers 1
- Saldo: SPOT, USDT + USDC + PEPE
- Paar wordt gekozen door je geld: USDC -> PEPEUSDC, USDT -> PEPEUSDT
- Prijs: eerst api.bybit.eu, fallback publiek api.bybit.com (alleen prijs)
- Harde stop: geen prijs = geen trade, geen PRICE=0
- Signaal: UT Bot 4.0/14 + Supertrend 10/3.0, allebei omhoog = kopen
- Elke 60 sec, Telegram max 1x/uur behalve UUR-berichten
"""
import os
import time
import hmac
import hashlib
import requests
import threading
from flask import Flask, request, jsonify

app = Flask(__name__)

def _env(*names):
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""

API_KEY = _env("BYBIT_API_KEY", "BYBIT_KEY", "BYBIT_API")
API_SECRET = _env("BYBIT_API_SECRET", "BYBIT_SECRET", "BYBIT_API_SECRET_KEY")
TG_TOKEN = _env("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN", "TG_TOKEN", "BOT_TOKEN")
TG_CHAT = _env("TELEGRAM_CHAT_ID", "TELEGRAM_CHAT", "TG_CHAT_ID", "CHAT_ID")

BASE = _env("BASE_URL", "BASE", "BYBIT_BASE_URL") or "https://api.bybit.eu"
# Alleen voor PUBLIEKE prijs, nooit voor saldo/orders (EU-keys werken niet op .com)
BASE_PUBLIC_FALLBACK = "https://api.bybit.com"
INTERVAL = "15"
CHECK_RUNNING = False
LAST_MSG = 0

# ---------- Telegram ----------
def send_msg(text):
    global LAST_MSG
    if not TG_TOKEN or not TG_CHAT:
        return
    now = time.time()
    # Alleen "UUR"-meldingen mogen vaker dan 1x per uur door
    if now - LAST_MSG < 3600 and "UUR" not in text:
        return
    try:
        requests.post(
            "https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage",
            json={"chat_id": TG_CHAT, "text": text},
            timeout=10
        )
        LAST_MSG = now
    except Exception as e:
        print("TG fout:", e)

# ---------- Bybit sign ----------
def sign(params, timestamp):
    qs = "&".join([k + "=" + str(params[k]) for k in sorted(params)])
    to_sign = str(timestamp) + API_KEY + "5000" + qs
    return hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()

def headers_for(params, ts):
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": str(ts),
        "X-BAPI-RECV-WINDOW": "5000",
        "X-BAPI-SIGN": sign(params, ts)
    }

# ---------- Saldo: USDT + USDC + PEPE ----------
def get_balance():
    """Geeft (usdt, usdc, pepe) terug. None,None,None bij fout."""
    try:
        ts = int(time.time() * 1000)
        params = {"accountType": "SPOT"}
        r = requests.get(BASE + "/v5/account/wallet-balance",
                         params=params, headers=headers_for(params, ts), timeout=15)
        j = r.json()
        if j.get("retCode") != 0:
            print("Saldo check SPOT:", j)
            return None, None, None
        coins = j["result"]["list"][0]["coin"]
        usdt = usdc = pepe = 0.0
        for c in coins:
            v = float(c.get("walletBalance", 0) or 0)
            if c["coin"] == "USDT":
                usdt = v
            elif c["coin"] == "USDC":
                usdc = v
            elif c["coin"] == "PEPE":
                pepe =
