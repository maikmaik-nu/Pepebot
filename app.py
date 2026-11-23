import os
import math
import threading
import traceback
import requests
from datetime import datetime, timezone
from flask import Flask, jsonify
from pybit.unified_trading import HTTP

# ============================================================
# 1. CONFIG - VIA RENDER > ENVIRONMENT
# ============================================================
app = Flask(__name__)

BYBIT_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")

# Hoeveel USDT per trade? Zet in Render op bijv. 50
TRADE_USDT = float(os.environ.get("TRADE_USDT", "50"))
# DRY_RUN = True = GEEN echte orders, alleen Telegram "ik zou nu kopen"
# Zet pas op False als je hem 1 dag getest hebt
DRY_RUN = os.environ.get("DRY_RUN", "True").lower() == "true"

SYMBOL = "PEPEUSDT"
INTERVAL = "15"  # 15m

# --- UT BOT INSTELLINGEN - 4, 14 ---
# In TradingView: UT Bot Alerts, Key Value = 1.0, ATR Period = 14
UT_KEY_VALUE = 1.0
UT_ATR_PERIOD = 14

# Hoe vaak checken? 60 sec is genoeg voor 15m candles
CHECK_INTERVAL_SEC = 60

# Minimale USDT om te mogen kopen
MIN_USDT_TO_BUY = 10
# PEPE qty moet veelvoud van 1000 zijn op 【entity-Bybit¦canonical_name=Bybit】 Spot
QTY_STEP = 1000

session = HTTP(testnet=False, api_key=BYBIT_KEY, api_secret=BYBIT_SECRET)

# --- STATE ---
last_signal = None          # "buy" of "sell" van laatste gesloten candle
last_candle_time = None     # timestamp van laatste verwerkte candle
last_trade_time = None
last_trade_type = None
in_position = False         # hebben we PEPE?
bot_state = "Opstarten..."
bot_start_time = datetime.now(timezone.utc)
total_trades = 0
total_buys = 0
total_sells = 0
last_error = None
last_ut = None
last_ut_value = None
last_price = 0
last_candle_str = "-"

# ============================================================
# 2. HELPERS - TIJD / LOGGING / TELEGRAM
# ============================================================
def now_str():
    return datetime.now(timezone.utc).strftime("%d-%m %H:%M:%S UTC")

def log(msg):
    print(f"[{now_str()}] {msg}", flush=True)

def send_telegram(msg):
    """Stuur bericht naar Telegram, faal stil als niet ingesteld."""
    log(f"TG: {msg}")
    if not TG_TOKEN or not TG_CHAT:
        log("Telegram niet ingesteld, sla over")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg},
            timeout=10
        )
    except Exception as e:
        log(f"Telegram error: {e}")

# ============================================================
# 3. BYBIT - BALANS / PRIJS / CANDLES
# ============================================================
def get_balances():
    """
    Haalt USDT + PEPE op uit UNIFIED wallet.
    walletBalance is leidend, niet availableToWithdraw.
    """
    usdt = 0.0
    pepe = 0.0
    try:
        r = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        coins = r["result"]["list"][0]["coin"]
        for c in coins:
            if c["coin"] == "USDT":
                usdt = float(c.get("walletBalance") or 0)
        r2 = session.get_wallet_balance(accountType="UNIFIED", coin="PEPE")
        coins2 = r2["result"]["list"][0]["coin"]
        for c in coins2:
            if c["coin"] == "PEPE":
                pepe = float(c.get("walletBalance") or c.get("availableToWithdraw") or 0)
    except Exception as e:
        log(f"Balance error: {e}")
        global last_error
        last_error = str(e)
    return usdt, pepe

def get_price():
    """Haalt laatste PEPE prijs op."""
    try:
        tick = session.get_tickers(category="spot", symbol=SYMBOL)
        price = float(tick["result"]["list"][0]["lastPrice"])
        return price
    except Exception as e:
        log(f"Prijs error: {e}")
        return 0

def get_candles(limit=200):
    """
    Haalt candles op van Bybit.
    Bybit geeft ze nieuw -> oud terug, wij draaien ze om naar oud -> nieuw.
    Formaat Bybit: [start, open, high, low, close, volume, turnover]
    """
    r = session.get_kline(category="spot", symbol=SYMBOL, interval=INTERVAL, limit=limit)
    out = []
    for k in reversed(r["result"]["list"]):
        out.append({
            "time": int(k[0]),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
            "volume": float(k[5]),
        })
    return out

# ============================================================
# 4. INDICATOR - ALLEEN UT BOT 4, 14
# ============================================================
def calc_atr(candles, period):
    """
    ATR = Average True Range.
    True Range = max(high-low, |high-prev_close|, |low-prev_close|)
    ATR = SMA van True Range over `period`.
    Retourneert lijst met ATR per candle-index.
    """
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i-1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)

    atrs = []
    for i in range(len(candles)):
        if i == 0:
            atrs.append(trs[0] if trs else 0)
        elif i < period:
            # Nog niet genoeg data, neem gemiddelde van wat er is
            atrs.append(sum(trs[:i]) / max(1, i))
        else:
            # trs[i-1] hoort bij candle i
            atrs.append(sum(trs[i-period:i]) / period)
    return atrs

def calc_ut_bot(candles, key_value=UT_KEY_VALUE, atr_period=UT_ATR_PERIOD):
    """
    UT Bot Alerts 4, 14 - zelfde als TradingView UT Bot.
    Trailing stop die omhoog kruipt in uptrend en omlaag in downtrend.
    Als prijs door de trailing heen breekt, flipt signaal.

    Retourneert: (signal, trailing_lijn)
    signal = "buy" of "sell" voor laatste candle
    """
    atrs = calc_atr(candles, atr_period)

    trailing = None
    prev_close = None
    signal = "buy"

    for i in range(1, len(candles)):
        c = candles
