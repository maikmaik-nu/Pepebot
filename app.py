import os, time, requests, hmac, hashlib, json, threading
from flask import Flask, request, jsonify

app = Flask(__name__)

# ================= CONFIG =================
BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
INTERVAL = "15"
ATR_PERIOD = 14
BASE_URL = "https://api.bybit.eu"
STATE_FILE = "state.json"

# Jouw instellingen uit Telegram
START_CAPITAL = 100.0
USE_COMPOUND = True  # Rente-op-rente AAN
USE_STOP_LOSS = False  # GEEN stop-loss

# ================= STATE IN MEMORY (Render wist state.json) =================
# Render Free wist files bij elke restart, dus memory is leading.
# state.json is alleen backup.
STATE = {
    "in_position": False,
    "entry_price": 0.0,
    "qty": 0.0,
    "last_signal_time": 0,
    "last_start_msg": 0,
    "last_error_msg": 0,
    "last_check": 0,
}
STATE_LOCK = threading.Lock()

def log(msg):
    print(msg, flush=True)

def send_telegram(msg, force=False):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        log(f"Telegram error: {e}")

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                with STATE_LOCK:
                    STATE.update(data)
    except Exception:
        pass

def save_state():
    try:
        with STATE_LOCK:
            data = dict(STATE)
        with open(STATE_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        log(f"save_state error: {e}")

# ================= BYBIT V5 SIGNING - CORRECT =================
def _sign(params_str, timestamp):
    recv_window = "5000"
    payload = timestamp + BYBIT_API_KEY + recv_window + params_str
    sign = hmac.new(
        BYBIT_API_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return sign, recv_window

def _headers(sign, recv_window, timestamp):
    return {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-SIGN": sign,
        "X-BAPI-SIGN-TYPE": "2",
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json"
    }

def bybit_get(path, params):
    timestamp = str(int(time.time() * 1000))
    query = "&".join([f"{k}={params[k]}" for k in sorted(params.keys())])
    sign, recv_window = _sign(query, timestamp)
    headers = _headers(sign, recv_window, timestamp)
    url = BASE_URL + path + "?" + query
    r = requests.get(url, headers=headers, timeout=15)
    return r.json()

def bybit_post(path, body):
    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(body, separators=(',', ':'))
    sign, recv_window = _sign(body_str, timestamp)
    headers = _headers(sign, recv_window, timestamp)
    url = BASE_URL + path
    r = requests.post(url, headers=headers, data=body_str, timeout=15)
    return r.json()

# ================= SALDO - UNIFIED EERST, DAN SPOT =================
def get_balances():
    """Geeft (usdt, pepe_qty, account_type) terug. None als 10003."""
    for account_type in ["UNIFIED", "SPOT"]:
        try:
            data = bybit_get("/v5/account/wallet-balance", {"accountType": account_type, "coin": "USDT,PEPE"})
            log(f"Saldo check {account_type}: retCode={data.get('retCode')}")

            if data.get("retCode") == 10003:
                log("FOUT 10003: Key heeft geen toestemming. bybit.eu > API > Read + Spot Trade + Wallet AAN, IP restrictie UIT")
                return None, None, "10003"

            if data.get("retCode") == 10006:
                log("FOUT 10006: Te veel requests, even wachten")
                return None, None, "RATE_LIMIT"

            if data.get("retCode") != 0:
                log(f"Saldo error {account_type}: {data}")
                continue

            lst = data.get("result", {}).get("list", [])
            if not lst:
                continue
            coins = {c.get("coin"): float(c.get("walletBalance", 0) or 0) for c in lst[0].get("coin", [])}
            usdt = coins.get("USDT", 0.0)
            pepe = coins.get("PEPE", 0.0)
            return usdt, pepe, account_type
        except Exception as e:
            log(f"Saldo exception {account_type}: {e}")
            continue
    return None, None, "ERROR"

# ================= MARKT DATA =================
def get_klines():
    try:
        url = f"{BASE_URL}/v5/market/kline"
        r = requests.get(url, params={"category": CATEGORY, "symbol": SYMBOL, "interval": INTERVAL, "limit": 100}, timeout=15)
        data = r.json()
        lst = data.get("result", {}).get("list", [])
        # Bybit: nieuwste eerst -> omdraaien naar oudste eerst
        return list(reversed(lst))
    except Exception as e:
        log(f"Klines error: {e}")
        return []

def get_price():
    try:
        url = f"{BASE_URL}/v5/market/tickers"
        r = requests.get(url, params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
        lst = r.json().get("result", {}).get("list", [])
        if lst:
            return float(lst[0].get("lastPrice", 0))
    except Exception as e:
        log(f"Price error: {e}")
    return 0

def get_qty_step():
    """Haalt qtyStep en minOrderQty op, anders fallback."""
    try:
        url = f"{BASE_URL}/v5/market/instruments-info"
        r = requests.get(url, params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
        lst = r.json().get("result", {}).get("list", [])
        if lst:
            f = lst[0].get("lotSizeFilter", {})
            return float(f.get("qtyStep", 1)), float(f.get("minOrderQty", 1))
    except Exception as e:
        log(f"Instrument error: {e}")
    return 1.0, 1.0

def calc_atr(klines):
    if len(klines) < ATR_PERIOD + 1:
        return 0
    trs = []
    for i in range(1, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        prev_close = float(klines[i-1][4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return sum(trs[-ATR_PERIOD:]) / ATR_PERIOD

# ================= ORDERS =================
def place_market_order(side, qty):
    """side = Buy of Sell, qty = hoeveelheid PEPE"""
    try:
        qty_step, min_qty = get_qty_step()
        # Afronden naar qtyStep, anders weigert Bybit
        qty = int(qty / qty_step) * qty_step
        if qty < min_qty:
            log(f"Order te klein: qty={qty} < min={min_qty}, skip")
            return None

        body = {
            "category": CATEGORY,
            "symbol": SYMBOL,
            "side": side,
            "orderType": "Market",
            "qty": str(qty)
        }
        log(f"Plaats order: {body}")
        data = bybit_post("/v5/order/create", body)
        log(f"Order result: {data}")

        if data.get("retCode") == 10003:
            send_telegram("⚠️ Pepebot: Order geweigerd (10003). Zet in bybit.eu API ook 'Spot Trade' AAN.")
            return None
        if data.get("retCode") != 0:
            log(f"Order error: {data}")
            return None
        return data
    except Exception as e:
        log(f"Order exception: {e}")
        return None

# ================= STRATEGIE =================
# Simpel en robuust, passend bij jouw instelling:
# - GEEN stop-loss
# - Rente-op-rente: we gebruiken elke keer je volledige USDT saldo
# - Koop als laatste candle groen is en sluit boven vorige close
# - Verkoop als laatste candle rood is en sluit onder vorige close
def check_bot():
    with STATE_LOCK:
        if time.time() - STATE["last_check"] < 60:
            return  # voorkom dubbele checks binnen 60 sec
        STATE["last_check"] = time.time()

    log("=== Bot check gestart ===")
    usdt, pepe_qty, acc_type = get_balances()

    if usdt is None:
        if acc_type == "10003":
            with STATE_LOCK:
                if time.time() - STATE["last_error_msg"] > 600:
                    STATE["last_error_msg"] = time.time()
                    send_telegram("⚠️ Pepebot: Kan saldo niet lezen (10003).\nFix: bybit.eu > API > Read + Spot Trade + Wallet AAN, IP restrictie UIT.\nNieuwe key in Render zetten + redeploy.")
        log("Bot stopt deze ronde: geen saldo")
        return

    log(f"Saldo USDT {acc_type}: {usdt} | PEPE: {pepe_qty}")

    klines = get_klines()
    if len(klines) < 5
