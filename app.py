from decimal import Decimal, ROUND_DOWN
import os
import time
import json
import hmac
import hashlib
import urllib.parse
import threading
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

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

# UTBot settings
UTBOT_KEY_VALUE = 1.0
UTBOT_ATR_PERIOD = 10

STATE = {"in_position": False, "entry_price": 0.0, "qty": 0.0, "last_check": 0, "last_start_msg": 0, "last_error_msg": 0}
STATE_LOCK = threading.Lock()
_LOOP_STARTED = False
_CHECK_RUNNING = False

def log(msg):
    print(msg, flush=True)

log(f"KEY LOADED: {len(BYBIT_API_KEY)} chars, start={BYBIT_API_KEY[:4] if BYBIT_API_KEY else 'LEEG'}****")

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post("https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN + "/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        log("Telegram error: " + str(e))

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
        log("save_state error: " + str(e))

def _sign(params_str, timestamp):
    recv_window = "5000"
    payload = timestamp + BYBIT_API_KEY + recv_window + params_str
    sign = hmac.new(BYBIT_API_SECRET.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return sign, recv_window

def _headers(sign, recv_window, timestamp):
    return {"X-BAPI-API-KEY": BYBIT_API_KEY, "X-BAPI-SIGN": sign, "X-BAPI-SIGN-TYPE": "2", "X-BAPI-TIMESTAMP": timestamp, "X-BAPI-RECV-WINDOW": recv_window, "Content-Type": "application/json"}

def bybit_get(path, params):
    timestamp = str(int(time.time() * 1000))
    query = urllib.parse.urlencode(sorted(params.items()))
    sign, recv_window = _sign(query, timestamp)
    headers = _headers(sign, recv_window, timestamp)
    url = BASE_URL + path + "?" + query
    try:
        r = requests.get(url, headers=headers, timeout=15)
        try:
            return r.json()
        except Exception:
            log(f"bybit_get geen JSON, status={r.status_code} body={r.text[:200]}")
            return {"retCode": -1, "retMsg": "empty", "http_status": r.status_code}
    except Exception as e:
        log("bybit_get exception: " + str(e))
        return {"retCode": -1, "retMsg": str(e)}

def bybit_post(path, body):
    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(body, separators=(",", ":"))
    sign, recv_window = _sign(body_str, timestamp)
    headers = _headers(sign, recv_window, timestamp)
    url = BASE_URL + path
    try:
        r = requests.post(url, headers=headers, data=body_str, timeout=15)
        try:
            return r.json()
        except Exception:
            log(f"bybit_post geen JSON, status={r.status_code} body={r.text[:200]}")
            return {"retCode": -1, "retMsg": "empty"}
    except Exception as e:
        log("bybit_post exception: " + str(e))
        return {"retCode": -1, "retMsg": str(e)}

def get_balances():
    total_usdt = 0.0
    total_pepe = 0.0
    seen_ok = False
    last_error = "ERROR"
    # FIX: SPOT eerst, daarna UNIFIED. Geld staat in SPOT.
    for account_type in ["SPOT", "UNIFIED"]:
        try:
            data = bybit_get("/v5/account/wallet-balance", {"accountType": account_type})
            log(f"Saldo check {account_type}: retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
            if data.get("retCode") == 10003:
                log(f"FOUT 10003 in {account_type}: Key geen toestemming. 【entity-bybit¦canonical_name=Bybit】.eu API Read + Spot Trade + Wallet AAN, IP UIT")
                last_error = "10003"
                continue
            if data.get("retCode") != 0:
                log(f"Saldo error {account_type}: {data}")
                last_error = "ERROR"
                continue
            lst = data.get("result", {}).get("list", [])
            if not lst:
                continue
            seen_ok = True
            for c in lst[0].get("coin", []):
                if c.get("coin") == "USDT":
                    total_usdt += float(c.get("walletBalance", 0) or 0)
                if c.get("coin") == "PEPE":
                    total_pepe += float(c.get("walletBalance", 0) or 0)
        except Exception as e:
            log(f"Saldo exception {account_type}: {e}")
            last_error = "ERROR"
            continue
    if not seen_ok:
        return None, None, last_error
    return total_usdt, total_pepe, "OK"

def get_klines():
    try:
        r = requests.get(BASE_URL + "/v5/market/kline", params={"category": CATEGORY, "symbol": SYMBOL, "interval": INTERVAL, "limit": 200}, timeout=15)
        lst = r.json().get("result", {}).get("list", [])
        return list(reversed(lst))
    except Exception as e:
        log("Klines error: " + str(e))
        return []

def get_price():
    klines = get_klines()
    if not klines:
        return 0.0
    try:
        return float(klines[-1][4])
    except Exception:
        return 0.0

def calc_atr(klines, period):
    if len(klines) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(klines)):
        try:
            high = float(klines[i][2])
            low = float(klines[i][3])
            prev_close = float(klines[i-1][4])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
        except Exception:
            continue
    if len(trs) < period:
        return 0.0
    return sum(trs[-period:]) / period

def utbot_signal(klines):
    # UTBot: ATR trailing stop, buy als close > trailing, sell als close < trailing
    if len(klines) < UTBOT_ATR_PERIOD + 2:
        return "NONE", 0.0, 0.0
    atr = calc_atr(klines, UTBOT_ATR_PERIOD)
    if atr == 0:
        return "NONE", 0.0, 0.0
    try:
        prev_close = float(klines[-2][4])
        close = float(klines[-1][4])
        prev_high = float(klines[-2][2])
        prev_low = float(klines[-2][3])
    except Exception:
        return "NONE", 0.0, 0.0
    x_atr_trailing_stop = UTBOT_KEY_VALUE * atr
    # Simpele UTBot trailing: stop = close +/- key * ATR, richting op basis van vorige candle
    # Buy als close boven vorige trailing, sell als close eronder
    upper = prev_close + x_atr_trailing_stop
    lower = prev_close - x_atr_trailing_stop
    # Gebruik close vs trailing band als signaal
    if close > upper:
        return "BUY", close, atr
    if close < lower:
        return "SELL", close, atr
    return "NONE", close, atr

def place_order(side, qty):
    # qty afronden op hele PEPE, geen decimalen
    q = int(Decimal(str(qty)).quantize(Decimal("1"), rounding=ROUND_DOWN))
    if q <= 0:
        log(f"Order afgewezen, qty te klein: {qty}")
        return None
    body = {"category": CATEGORY, "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(q)}
    log(f"Order: {side} {q} {SYMBOL}")
    data = bybit_post("/v5/order/create", body)
    log(f"Order result: {data}")
    return data

def bot_check():
    global _CHECK_RUNNING
    if _CHECK_RUNNING:
        return
    _CHECK_RUNNING = True
    try:
        with STATE_LOCK:
            STATE["last_check"] = int(time.time())
        save_state()

        usdt, pepe, status = get_balances()
        if usdt is None:
            log(f"Bot stopt: geen saldo, status={status}")
            return

        price = get_price()
        if price <= 0:
            log("Bot stopt: geen prijs")
            return

        klines = get_klines()
        signal, close, atr = utbot_signal(klines)
        log(f"Check: price={price} usdt={usdt} pepe={pepe} signal={signal} atr={atr}")

        with STATE_LOCK:
            in_position = STATE["in_position"]

        # BUY: geen positie + BUY signaal + genoeg USDT
        if not in_position and signal == "BUY":
            if usdt < 10:
                log(f"Bot stopt: te weinig USDT ({usdt})")
                return
            # Gebruik 95% van USDT, min 10 USDT
            spend = usdt * 0.95
            qty = spend / price
            res = place_order("Buy", qty)
            if res and res.get("retCode") == 0:
                with STATE_LOCK:
                    STATE["in_position"] = True
                    STATE["entry_price"] = price
                    STATE["qty"] = qty
                save_state()
                send_telegram(f"BUY PEPEUSDT @ {price} qty={qty:.0f} UTBot BUY")
            return

        # SELL: in positie + SELL signaal
        if in_position and signal == "SELL":
            with STATE_LOCK:
                qty = STATE["qty"]
                # Als qty onbekend, verkoop al je PEPE saldo
                if qty <= 0:
                    qty = pepe
            if qty <= 0:
                log("Bot stopt: geen PEPE om te verkopen")
                return
            res = place_order("Sell", qty)
            if res and res.get("retCode") == 0:
                with STATE_LOCK:
                    STATE["in_position"] = False
                    STATE["entry_price"] = 0.0
                    STATE["qty"] = 0.0
                save_state()
                send_telegram(f"SELL PEPEUSDT @ {price} qty={qty:.0f} UTBot SELL")
            return

        log("Geen actie, signaal=" + signal)
    except Exception as e:
        log(f"bot_check error: {e}")
    finally:
        _CHECK_RUNNING = False

def bot_loop():
    while True:
        try:
            bot_check()
        except Exception as e:
            log(f"bot_loop error: {e}")
        time.sleep(60)

@app.route("/")
def home():
    return jsonify({"status": "OK", "symbol": SYMBOL, "in_position": STATE["in_position"]})

@app.route("/test-key")
def test_key():
    usdt, pepe, status = get_balances()
    return jsonify({"key_start": BYBIT_API_KEY[:4] + "****" if BYBIT_API_KEY else "LEEG", "usdt": usdt, "pepe": pepe, "status": status})

@app.route("/status")
def status():
    price = get_price()
    return jsonify({"in_position": STATE["in_position"], "entry_price": STATE["entry_price"], "qty": STATE["qty"], "price": price, "last_check": STATE["last_check"]})

@app.route("/check-now")
def check_now():
    threading.Thread(target=bot_check, daemon=True).start()
    return jsonify({"status": "check gestart"})

load_state()

if __name__ == "__main__":
    if not _LOOP_STARTED:
        _LOOP_STARTED = True
        threading.Thread(target=bot_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=10000)
