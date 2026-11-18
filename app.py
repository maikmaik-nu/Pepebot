import os, json, time, threading, hmac, hashlib, urllib.parse
from decimal import Decimal, ROUND_DOWN
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

【entity-BYBIT_API_KEY¦canonical_name=BYBIT】 = os.environ.get("【entity-BYBIT_API_KEY¦canonical_name=BYBIT】", "").strip()
【entity-BYBIT_API_SECRET¦canonical_name=BYBIT】 = os.environ.get("【entity-BYBIT_API_SECRET¦canonical_name=BYBIT】", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
INTERVAL = "15"
ATR_PERIOD = 14
UT_FACTOR = 4.0
BASE_URL = "https://api." + "by" + "bit.eu"
STATE_FILE = "state.json"

STATE = {"in_position": False, "entry_price": 0.0, "qty": 0.0, "last_check": 0, "last_start_msg": 0, "last_error_msg": 0, "trailing": 0.0, "prev_close": 0.0}
STATE_LOCK = threading.Lock()
_LOOP_STARTED = False
_CHECK_RUNNING = False

def log(msg):
    print(msg, flush=True)

log(f"KEY LOADED: {len(【entity-BYBIT_API_KEY¦canonical_name=BYBIT】)} chars, start={【entity-BYBIT_API_KEY¦canonical_name=BYBIT】[:4] if 【entity-BYBIT_API_KEY¦canonical_name=BYBIT】 else 'LEEG'}****")

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

def get_server_time():
    try:
        r = requests.get(BASE_URL + "/v5/market/time", timeout=5)
        return str(int(r.json()["result"]["timeSecond"]) * 1000)
    except:
        return str(int(time.time() * 1000))

def _sign(params_str, timestamp):
    recv_window = "20000"
    payload = timestamp + 【entity-BYBIT_API_KEY¦canonical_name=BYBIT】 + recv_window + params_str
    sign = hmac.new(【entity-BYBIT_API_SECRET¦canonical_name=BYBIT】.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return sign, recv_window

def _headers(sign, recv_window, timestamp):
    return {"X-BAPI-API-KEY": 【entity-BYBIT_API_KEY¦canonical_name=BYBIT】, "X-BAPI-SIGN": sign, "X-BAPI-SIGN-TYPE": "2", "X-BAPI-TIMESTAMP": timestamp, "X-BAPI-RECV-WINDOW": recv_window, "Content-Type": "application/json"}

def bybit_get(path, params):
    timestamp = get_server_time()
    query = urllib.parse.urlencode(sorted(params.items()))
    sign, recv_window = _sign(query, timestamp)
    headers = _headers(sign, recv_window, timestamp)
    url = BASE_URL + path + "?" + query
    try:
        r = requests.get(url, headers=headers, timeout=15)
        try:
            return r.json()
        except:
            return {"retCode": -1, "retMsg": "empty", "http_status": r.status_code}
    except Exception as e:
        log("bybit_get exception: " + str(e))
        return {"retCode": -1, "retMsg": str(e)}

def bybit_post(path, body):
    timestamp = get_server_time()
    body_str = json.dumps(body, separators=(",", ":"))
    sign, recv_window = _sign(body_str, timestamp)
    headers = _headers(sign, recv_window, timestamp)
    url = BASE_URL + path
    try:
        r = requests.post(url, headers=headers, data=body_str, timeout=15)
        try:
            return r.json()
        except:
            return {"retCode": -1, "retMsg": "empty"}
    except Exception as e:
        log("bybit_post exception: " + str(e))
        return {"retCode": -1, "retMsg": str(e)}

def get_balances():
    total_usdt = 0.0
    total_pepe = 0.0
    seen_ok = False
    last_error = "ERROR"
    for account_type in ["SPOT", "UNIFIED"]:
        try:
            data = bybit_get("/v5/account/wallet-balance", {"accountType": account_type})
            log(f"Saldo check {account_type}: retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
            if data.get("retCode") == 10003:
                last_error = "10003"
                continue
            if data.get("retCode") != 0:
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
    return total_usdt, total_pepe, "SPOT+UNIFIED"

def get_klines():
    try:
        r = requests.get(BASE_URL + "/v5/market/kline", params={"category": CATEGORY, "symbol": SYMBOL, "interval": INTERVAL, "limit": 100}, timeout=15)
        lst = r.json().get("result", {}).get("list", [])
        return list(reversed(lst))
    except Exception as e:
        log("Klines error: " + str(e))
        return []

def get_price():
    try:
        r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
        lst = r.json().get("result", {}).get("list", [])
        if lst:
            return float(lst[0].get("lastPrice", 0))
    except Exception as e:
        log("Price error: " + str(e))
    return 0

def get_qty_step():
    try:
        r = requests.get(BASE_URL + "/v5/market/instruments-info", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
        lst = r.json().get("result", {}).get("list", [])
        if lst:
            f = lst[0].get("lotSizeFilter", {})
            return float(f.get("qtyStep", 1)), float(f.get("minOrderQty", 1))
    except Exception as e:
        log("Instrument error: " + str(e))
    return 1.0, 1.0

def calc_atr(klines):
    if len(klines) < ATR_PERIOD + 1:
        return 0
    trs = []
    for i in range(1, len(klines)):
        high = float(klines[i][2]); low = float(klines[i][3]); prev_close = float(klines[i-1][4])
        trs
