import os, json, time, threading, hmac, hashlib, urllib.parse
from decimal import Decimal, ROUND_DOWN
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
        except:
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
        except:
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
    # FIX: geen coin param meer, dat gaf ERROR voor SPOT
    for account_type in ["SPOT", "UNIFIED"]:
        try:
            data = bybit_get("/v5/account/wallet-balance", {"accountType": account_type})
            log(f"Saldo check {account_type}: retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
            if data.get("retCode") == 10003:
                log(f"FOUT 10003 in {account_type}: Key geen toestemming. bybit.eu API Read + Spot Trade + Wallet AAN, IP UIT")
                last_error = "10003"
                continue
            if data.get("retCode") != 0:
                log(f"Saldo error {account_type}:
