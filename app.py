import os, math, time, threading, requests
from flask import Flask, jsonify, request
from pybit.unified_trading import HTTP

app = Flask(__name__)

# --- ENV VARS ---
BYBIT_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "verander-dit")
TRADE_USDT = float(os.environ.get("TRADE_USDT", "50"))
SYMBOL = os.environ.get("SYMBOL", "PEPEUSDT")
INTERVAL = os.environ.get("INTERVAL", "15")
# UT BOT 4 - 14
KEY_VALUE = float(os.environ.get("KEY_VALUE", "4.0"))
ATR_PERIOD = int(os.environ.get("ATR_PERIOD", "14"))
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))

session = HTTP(testnet=False, api_key=BYBIT_KEY, api_secret=BYBIT_SECRET, domain="bybit.eu")

last_signal = None
last_candle_time = None
last_trade_info = "Nog geen trade"
bot_state = "Wachten op eerste candle..."
last_error = ""

# UT BOT geheugen - dit is essentieel, anders werkt UT Bot niet
ut_trailing_stop = None
ut_position = None  # "long" of "short"

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

# --- BALANCES ---
def get_balances():
    try:
        r = session.get_wallet_balance(accountType="SPOT", coin="USDT")
        usdt = 0
        for c in r["result"]["list"][0]["coin"]:
            if c["coin"] == "USDT":
                usdt = float(c.get("walletBalance") or 0)
        r2 = session.get_wallet_balance(accountType="SPOT", coin="PEPE")
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

def get_klines():
    try:
        k = session.get_kline(category="spot", symbol=SYMBOL, interval=INTERVAL, limit=200)
        lst = list(reversed(k["result"]["list"]))
        # FIX: laatste candle is nog open, weggooien
        lst = lst[:-1]
        closes = [float(x[4]) for x in lst]
        highs = [float(x[2]) for x in lst]
        lows = [float(x[3]) for x in lst]
        times = [int(x[0]) for x in lst]
        return closes, highs, lows, times
    except Exception as e:
        print(f"Kline error: {e}", flush=True)
        return [], [], [], []

def calc_atr(highs, lows, closes):
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    if len(trs) < ATR_PERIOD:
        return 0
    return sum(trs[-ATR_PERIOD:]) / ATR_PERIOD

def calc_signal():
    global ut_trailing_stop, ut_position
    closes, highs, lows, times = get_klines()
    if len(closes) < ATR_PERIOD + 2:
        return None, closes, highs, lows, None

    atr = calc_atr(highs, lows, closes)
    if atr == 0:
        return None, closes, highs, lows, times[-1]

    close = closes[-1]

    # --- UT BOT 4 - 14, exact zoals TradingView ---
    # xATR = ATR(14)
    # nLoss = KeyValue(4) * xATR
    # Trailing stop
    nLoss = KEY_VALUE * atr

    if ut_trailing_stop is None:
        # Eerste keer initialiseren
        ut_trailing_stop = close - nLoss
        ut_position = "long" if close > ut_trailing_stop else "short"
        return None, closes, highs, lows, times[-1]

    # Trailing stop updaten - dit is de kern van UT Bot
    if close > ut_trailing_stop:
        # Prijs boven stop = long, stop mag alleen omhoog
        ut_trailing_stop = max(ut
