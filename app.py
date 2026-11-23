# ============================================================
# PEPEBOT-3 - VOLLEDIGE VERSIE
# 【entity-Bybit¦canonical_name=Bybit】 EU - SPOT - PEPEUSDT
# Flask + Gunicorn + Render Web Service (gratis)
# ============================================================
import os
import time
import hmac
import hashlib
import json
import math
import threading
import traceback
from datetime import datetime, timezone
from collections import deque
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# ============================================================
# 1. CONFIG - ALLES HIER AANPASSEN
# ============================================================
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"

# --- Trading instellingen ---
TRADE_USDT = float(os.environ.get("TRADE_USDT", "10"))       # per trade
MAX_USDT_PER_DAY = float(os.environ.get("MAX_USDT_PER_DAY", "50"))
MAX_OPEN_TRADES = 1
CHECK_EVERY = int(os.environ.get("CHECK_EVERY", "30"))       # seconden tussen checks
MIN_USDT_TO_TRADE = 10.0

# --- Strategie instellingen ---
RSI_PERIOD = 14
RSI_BUY = 35.0        # koop als RSI onder dit zit (oversold)
RSI_SELL = 65.0       # verkoop als RSI boven dit zit (overbought)
RSI_OVERBOUGHT_EXIT = 70.0
USE_RSI = True
USE_TREND_FILTER = True
TREND_FAST = 9        # EMA fast
TREND_SLOW = 21       # EMA slow
USE_DIP_BUY = True
DIP_PCT = 0.012       # 1.2% dip t.o.v. laatste high
PUMP_PCT = 0.015      # 1.5% pump t.o.v. buy price = take profit 1

# --- Risk management ---
STOP_LOSS_PCT = 0.04      # -4% = verkoop met verlies, niet blijven vasthouden
TAKE_PROFIT_PCT = 0.03    # +3% = verkoop met winst
TRAILING_PCT = 0.015      # trailing stop 1.5% onder hoogste prijs na buy
MAX_HOLD_MINUTES = 240    # max 4 uur in positie, daarna eruit
COOLDOWN_AFTER_SELL = 60  # seconden wachten na verkoop voor nieuwe koop
COOLDOWN_AFTER_STOPLOSS = 300  # 5 min wachten na stoploss
MIN_PROFIT_TO_SELL = 0.002  # minimaal 0.2% winst na fees, anders niet verkopen

# --- State file ---
STATE_FILE = "bot_state.json"
PRICE_HISTORY_FILE = "price_history.json"
LOG_FILE = "bot_log.json"

# ============================================================
# 2. STATE - WORDT OPGESLAGEN ZODAT HIJ NIET VERGEET WAAR HIJ WAS
# ============================================================
DEFAULT_STATE = {
    "in_position": False,
    "buy_price": 0.0,
    "buy_qty": 0.0,
    "buy_time": 0,
    "buy_usdt_spent": 0.0,
    "highest_since_buy": 0.0,
    "lowest_since_buy": 0.0,
    "last_price": 0.0,
    "last_high": 0.0,
    "last_low": 0.0,
    "total_trades": 0,
    "total_wins": 0,
    "total_losses": 0,
    "total_profit_usdt": 0.0,
    "today_date": "",
    "today_spent_usdt": 0.0,
    "today_trades": 0,
    "last_sell_time": 0,
    "last_stoploss_time": 0,
    "last_buy_time": 0,
    "last_signal": "NONE",
    "last_signal_reason": "",
    "last_error": "",
    "last_balance_code": 0,
    "consecutive_errors": 0,
    "bot_started_at": 0,
    "last_check_at": 0,
}

state = dict(DEFAULT_STATE)
state_lock = threading.Lock()
price_history = deque(maxlen=200)  # laatste 200 prijzen voor RSI / EMA
trade_log = deque(maxlen=100)

def load_state():
    global state
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                loaded = json.load(f)
                for k, v in DEFAULT_STATE.items():
                    if k in loaded:
                        state[k] = loaded[k]
            print(f"STATE GELEDEN: in_position={state['in_position']} buy_price={state['buy_price']}", flush=True)
    except Exception as e:
        print(f"STATE LOAD FAIL: {e}", flush=True)

def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"STATE SAVE FAIL: {e}", flush=True)

def load_price_history():
    try:
        if os.path.exists(PRICE_HISTORY_FILE):
            with open(PRICE_HISTORY_FILE, "r") as f:
                data = json.load(f)
                for p in data[-200:]:
                    price_history.append(float(p))
    except:
        pass

def save_price_history():
    try:
        with open(PRICE_HISTORY_FILE, "w") as f:
            json.dump(list(price_history), f)
    except:
        pass

def log_trade(action, price, qty, usdt, reason, profit=None):
    entry = {
        "time": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "price": price,
        "qty": qty,
        "usdt": usdt,
        "reason": reason,
        "profit": profit,
        "balance_code": state.get("last_balance_code", 0)
    }
    trade_log.append(entry)
    print(f"TRADE LOG: {entry}", flush=True)
    try:
        existing = []
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r") as f:
                existing = json.load(f)
        existing.append(entry)
        with open(LOG_FILE, "w") as f:
            json.dump(existing[-200:], f)
    except:
        pass

load_state()
load_price_history()
if state["bot_started_at"] == 0:
    state["bot_started_at"] = int(time.time())

# ============================================================
# 3. 【entity-BYBIT¦canonical_name=Bybit】 API - SIGNING, BALANCE, ORDERS, KLINES
# ============================================================
def sign_params(params):
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sig = hmac.new(API_SECRET.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return qs, sig

def 【entity-bybit_get¦canonical_name=Bybit】(path, params, need_auth=True):
    params = dict(params)
    if need_auth:
        params["api_key"] = API_KEY
        params["timestamp"] = str(int(time.time() * 1000))
        params["recv_window"] = "5000"
        qs, sig = sign_params(params)
        headers = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sig}
        url = BASE_URL + path + "?" + qs + f"&sign={sig}"
    else:
        qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
        headers = {}
        url = BASE_URL + path + "?" + qs
    try:
        r = requests.get(url, headers=headers, timeout=12)
        return r.json()
    except Exception as e:
        return {"retCode": -1, "retMsg": str(e), "result": {}}

def bybit_post(path, params):
    params = dict(params)
    params["api_key"] = API_KEY
    params["timestamp"] = str(int(time.time() * 1000))
    params["recv_window"] = "5000"
    qs, sig = sign_params(params)
    headers = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sig, "Content-Type": "application/json"}
    try:
        r = requests.post(BASE_URL + path, json=params, headers=headers, timeout=12)
        return r.json()
    except Exception as e:
        return {"retCode": -1, "retMsg": str(e), "result": {}}

def get_price():
    # 1e poging: tickers (snel)
    try:
        r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
        d = r.json()
        price = float(d["result"]["list"][0]["lastPrice"])
        if price > 0:
            return price
    except Exception as e:
        print(f"PRICE TICKER FAIL: {e}", flush=True)
    # 2e poging: klines (fallback)
    try:
        d = 【entity-bybit_get¦canonical_name=Bybit】("/v5/market/kline", {"category": CATEGORY, "symbol": SYMBOL, "interval": "1", "limit": "1"}, need_auth=False)
        price = float(d["result"]["list"][0][4])
        if price > 0:
            return price
    except Exception as e:
        print(f"PRICE KLINE FAIL: {e}", flush=True)
    return 0.0

def get_klines(limit=100):
    try:
        d = 【entity-bybit_get¦canonical_name=Bybit】("/v5/market/kline", {"category": CATEGORY, "symbol": SYMBOL, "interval": "5", "limit": str(limit)}, need_auth=False)
        closes = []
        for row in reversed(d["result"]["list"]):
            closes.append(float(row[4]))
        return closes
    except Exception as e:
        print(f"KLINE FAIL: {e}", flush=True)
        return []

def get_balance_all():
    """Geeft (usdt, pepe, retCode) terug"""
    if not API_KEY or not API_SECRET:
        return 0.0, 0.0, "NO_KEY"
    # USDT
    usdt = 0.0
    pepe = 0.0
    code = 0
    d1 = 【entity-bybit_get¦canonical_name=Bybit】("/v5/account/wallet-balance", {"accountType": "SPOT", "coin": "USDT"})
    if d1.get("retCode") != 0:
        print(f"BALANCE FAIL USDT: {d1}", flush=True)
        return 0.0, 0.0, d1.get("retCode")
    try:
        for c in d1["result"]["list"][0]["coin"]:
            if c["coin"] == "USDT":
                usdt = float(c["walletBalance"])
    except Exception as e:
        print(f"BALANCE PARSE USDT FAIL: {e}", flush=True)
    # PEPE apart, want Bybit geeft alleen gevraagde coin terug
    d2 = bybit_get("/v5/account/wallet-balance", {"accountType": "SPOT", "coin": "PEPE"})
    if d2.get("retCode") != 0:
        print(f"BALANCE FAIL PEPE: {d2}", flush=True)
        # Als USDT wel lukte, geef die toch terug
        return usdt, 0.0, d2.get("retCode")
    try:
        for c in d2["result"]["list"][0]["coin"]:
            if c["coin"] == "PEPE":
                pepe = float(c["walletBalance"])
    except Exception as e:
        print(f"BALANCE PARSE PEPE FAIL: {e}", flush=True)
    return usdt, pepe, 0

def get_symbol_info():
    """Haal min qty / step op zodat orders niet afgekeurd worden"""
    try:
        d = bybit_get("/v5/market/instruments-info", {"category": CATEGORY, "symbol": SYMBOL}, need_auth=False)
        info = d["result"]["list"][0]
        lot = info["lotSizeFilter"]
        return {
            "min_qty": float(lot["minOrderQty"]),
            "max_qty": float(lot["maxOrderQty"]),
            "qty_step": float(lot["qtyStep"]),
            "min_order_amt": float(lot.get("minOrderAmt", 0)),
        }
    except Exception as e:
        print(f"SYMBOL INFO FAIL: {e}", flush=True)
        return {"min_qty": 1000.0, "max_qty": 1000000000.0, "qty_step": 1.0, "min_order_amt": 1.0}

SYMBOL_INFO = get_symbol_info()

def round_qty(qty):
    step = SYMBOL_INFO["qty_step"]
    if step <= 0:
        return int(qty)
    # Rond af naar beneden op step
    decimals = 0
    if "." in str(step):
        decimals = len(str(step).split(".")[1].rstrip("0"))
    rounded = math.floor(qty / step) * step
    return round(rounded, decimals)

def place_buy(usdt_amount, price):
    qty = round_qty(usdt_amount / price)
    if qty < SYMBOL_INFO["min_qty"]:
        return {"retCode": -1, "retMsg": f"qty {qty} < min {SYMBOL_INFO['min_qty']}"}
    if usdt_amount < SYMBOL_INFO["min_order_amt"]:
        return {"retCode": -1, "retMsg": f"amount {usdt_amount} < min {SYMBOL_INFO['min_order_amt']}"}
    print(f"BUY ORDER: {qty} PEPE voor ~{usdt_amount} USDT @ {price}", flush=True)
    res = bybit_post("/v5/order/create", {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": "Buy",
        "orderType": "Market",
        "qty": str(qty)
    })
    print(f"BUY RESULT: {res}", flush=True)
    return res

def place_sell(qty):
    qty = round_qty(qty)
    if qty < SYMBOL_INFO["min_qty"]:
        return {"retCode": -1, "retMsg": f"qty {qty} < min {SYMBOL_INFO['min_qty']}"}
    print(f"SELL ORDER: {qty} PEPE", flush=True)
    res = bybit_post("/v5/order/create", {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": "Sell",
        "orderType": "Market",
        "qty": str(qty)
    })
    print(f"SELL RESULT: {res}", flush=True)
    return res

# ============================================================
# 4. INDICATOREN - RSI + EMA
# ============================================================
def calc_rsi(prices, period=14):
    if len(prices) < period + 1:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        if diff > 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))
    # Eerste gemiddelde
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        return 100.0
    # Wilder smoothing
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)

def calc_ema(prices, period):
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period
    for p in prices[period:]:
        ema = p * k + ema * (1 - k)
    return ema

def get_indicators():
    # Gebruik price_history + verse klines
    closes = get_klines(100)
    if len(closes) < 30:
        closes = list(price_history)
    if len(closes) < 5:
        return {"rsi": 50.0, "ema_fast": 0.0, "ema_slow": 0.0, "trend_up": True}
    rsi = calc_rsi(closes, RSI_PERIOD)
    ema_fast = calc_ema(closes, TREND_FAST)
    ema_slow = calc_ema(closes, TREND_SLOW)
    return {
        "rsi": rsi,
        "ema_fast": ema_fast,
        "ema_slow": ema_slow,
        "trend_up": ema_fast > ema_slow,
        "trend_down": ema_fast < ema_slow,
        "closes_len": len(closes)
    }

# ============================================================
# 5. STRATEGIE - BESLISSEN OF WE KOPEN / VERKOPEN
