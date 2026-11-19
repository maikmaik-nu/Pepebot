import os, json, time, threading, hmac, hashlib
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

STATE = {"in_position": False, "entry_price": 0.0, "qty": 0.0, "balance": 100.0, "last_check": 0, "last_start_msg": 0, "last_error_msg": 0}
STATE_LOCK = threading.Lock()
_LOOP_STARTED = False
_CHECK_RUNNING = False

def log(msg):
    print(msg, flush=True)

log("KEY LOADED: " + str(len(BYBIT_API_KEY)) + " chars, start=" + (BYBIT_API_KEY[:4] if BYBIT_API_KEY else "LEEG") + "****")

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
                    if "balance" not in STATE or not STATE["balance"]:
                        STATE["balance"] = 100.0
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
        except:
            log("bybit_post geen JSON, status=" + str(r.status_code) + " body=" + str(r.text)[:200])
            return {"retCode": -1, "retMsg": "empty", "http_status": r.status_code}
    except Exception as e:
        log("bybit_post exception: " + str(e))
        return {"retCode": -1, "retMsg": str(e)}

def get_klines():
    for base in ["https://api.bybit.eu", "https://api.bybit.com"]:
        try:
            r = requests.get(base + "/v5/market/kline", params={"category": CATEGORY, "symbol": SYMBOL, "interval": INTERVAL, "limit": 100}, timeout=15)
            lst = r.json().get("result", {}).get("list", [])
            if lst:
                log("Klines OK via " + base + " aantal: " + str(len(lst)))
                return list(reversed(lst))
            log("Klines leeg via " + base + " resp: " + str(r.text)[:200])
        except Exception as e:
            log("Klines error via " + base + ": " + str(e))
    return []

def get_price():
    for base in ["https://api.bybit.eu", "https://api.bybit.com"]:
        try:
            r = requests.get(base + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
            lst = r.json().get("result", {}).get("list", [])
            if lst:
                return float(lst[0].get("lastPrice", 0))
        except Exception:
            pass
    return 0

def get_qty_step():
    for base in ["https://api.bybit.eu", "https://api.bybit.com"]:
        try:
            r = requests.get(base + "/v5/market/instruments-info", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
            lst = r.json().get("result", {}).get("list", [])
            if lst:
                f = lst[0].get("lotSizeFilter", {})
                return float(f.get("qtyStep", 1)), float(f.get("minOrderQty", 1))
        except Exception:
            pass
    return 1.0, 1.0

def calc_atr(klines):
    if len(klines) < ATR_PERIOD + 1:
        return 0
    trs = []
    for i in range(1, len(klines)):
        high = float(klines[i][2]); low = float(klines[i][3]); prev_close = float(klines[i-1][4])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs[-ATR_PERIOD:]) / ATR_PERIOD

def round_qty(qty, qty_step):
    from decimal import Decimal, ROUND_DOWN
    step = Decimal(str(qty_step))
    q = Decimal(str(qty))
    rounded = (q / step).to_integral_value(rounding=ROUND_DOWN) * step
    return float(rounded)

def place_market_order(side, qty):
    try:
        qty_step, min_qty = get_qty_step()
        qty = round_qty(qty, qty_step)
        if qty < min_qty:
            log("Order te klein: " + str(qty))
            return None
        body = {"category": CATEGORY, "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty)}
        log("Plaats order: " + str(body))
        data = bybit_post("/v5/order/create", body)
        log("Order result: " + str(data))
        if data.get("retCode") != 0:
            return None
        return data
    except Exception as e:
        log("Order exception: " + str(e))
        return None

def check_bot():
    global _CHECK_RUNNING
    if _CHECK_RUNNING:
        log("Check al bezig, skip")
        return
    _CHECK_RUNNING = True
    try:
        with STATE_LOCK:
            if time.time() - STATE["last_check"] < 60:
                log("Te snel opnieuw, wacht 60s")
                return
            STATE["last_check"] = time.time()
        log("=== Bot check gestart ===")
        with STATE_LOCK:
            in_pos = STATE["in_position"]
            saved_qty = STATE["qty"]
            entry = STATE["entry_price"]
            balance = STATE.get("balance", 100.0) or 100.0
        usdt = balance
        pepe_qty = saved_qty if in_pos else 0.0
        log("Inleg: " + str(usdt) + " | PEPE in state: " + str(pepe_qty) + " | In positie: " + str(in_pos))
        klines = get_klines()
        if len(klines) < 5:
            log("Te weinig klines, skip")
            return
        price = get_price()
        if price == 0:
            price = float(klines[-1][4])
        atr = calc_atr(klines)
        last = klines[-1]; prev = klines[-2]
        last_open = float(last[1]); last_close = float(last[4]); prev_close = float(prev[4])
        log("Prijs: " + str(price) + " | ATR: " + str(atr))
        if not in_pos and last_close > last_open and last_close > prev_close:
            if usdt < 5:
                log("Te weinig USDT"); return
            qty = usdt / price
            res = place_market_order("Buy", qty)
            if res:
                with STATE_LOCK:
                    STATE["in_position"] = True; STATE["entry_price"] = price; STATE["qty"] = qty
                save_state()
                send_telegram("GEKOCHT PEPEUSDT Prijs: " + str(price) + " Inleg: " + str(usdt) + " USDT GEEN stop-loss Rente-op-rente AAN")
                log("KOOP uitgevoerd")
            return
        if in_pos and last_close < last_open and last_close < prev_close:
            qty_to_sell = pepe_qty if pepe_qty > 0 else 0
            if qty_to_sell <= 0:
                log("Niks te verkopen, qty is 0")
                return
            res = place_market_order("Sell", qty_to_sell)
            if res:
                new_balance = 0.0
                if entry and entry > 0:
                    new_balance =
