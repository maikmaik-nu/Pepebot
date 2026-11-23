import os, math, time, threading, requests
from flask import Flask, jsonify, request
from pybit.unified_trading import HTTP

app = Flask(__name__)

# --- ENV VARS (in Render > Environment) ---
BYBIT_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
TRADE_USDT = float(os.environ.get("TRADE_USDT", "50"))
SYMBOL = os.environ.get("SYMBOL", "PEPEUSDT")
INTERVAL = os.environ.get("INTERVAL", "15")
KEY_VALUE = float(os.environ.get("KEY_VALUE", "1.0"))
ATR_PERIOD = int(os.environ.get("ATR_PERIOD", "14"))
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))

session = HTTP(testnet=False, api_key=BYBIT_KEY, api_secret=BYBIT_SECRET)
last_signal = None
last_candle_time = None
last_trade_info = "Nog geen trade"
bot_state = "Wachten op eerste candle..."
last_error = ""
telegram_offset = None

# --- TELEGRAM ---
def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT:
        print(f"Telegram niet ingesteld: {msg}", flush=True)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg}, timeout=10
        )
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)

def get_balances():
    try:
        r = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        usdt = 0
        for c in r["result"]["list"][0]["coin"]:
            if c["coin"] == "USDT":
                usdt = float(c.get("walletBalance") or 0)
        r2 = session.get_wallet_balance(accountType="UNIFIED", coin="PEPE")
        pepe = 0
        for c in r2["result"]["list"][0]["coin"]:
            if c["coin"] == "PEPE":
                pepe = float(c.get("walletBalance") or c.get("availableToWithdraw") or 0)
        return usdt, pepe
    except Exception as e:
        print(f"Balance error: {e}", flush=True)
        return 0, 0

def get_price():
    try:
        tick = session.get_tickers(category="spot", symbol=SYMBOL)
        return float(tick["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print(f"Price error: {e}", flush=True)
        return 0

def do_buy(reason="UT Bot Buy"):
    global last_trade_info
    usdt, pepe = get_balances()
    if usdt < 10:
        send_telegram(f"❌ BUY genegeerd: te weinig USDT ({usdt:.2f})")
        return
    use = min(TRADE_USDT, usdt * 0.98)
    price = get_price()
    if price <= 0:
        send_telegram("❌ BUY mislukt: geen prijs")
        return
    qty = int(math.floor((use / price) / 1000) * 1000)
    if qty <= 0:
        send_telegram("❌ BUY genegeerd: qty te klein")
        return
    try:
        session.place_order(category="spot", symbol=SYMBOL, side="Buy", orderType="Market", qty=str(qty))
        last
