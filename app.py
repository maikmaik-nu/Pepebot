import os, time, requests, hmac, hashlib, json, threading
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

START_CAPITAL = 100.0
USE_COMPOUND = True
USE_STOP_LOSS = False

STATE = {"in_position": False, "entry_price": 0.0, "qty": 0.0, "last_check": 0, "last_start_msg": 0, "last_error_msg": 0}
STATE_LOCK = threading.Lock()

def log(msg):
    print(msg, flush=True)

def send_telegram(msg, force=False):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        log(f"Telegram error: {e}")

def _sign(ts, recv, payload):
    msg = ts + BYBIT_API_KEY + recv + payload
    return hmac.new(BYBIT_API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

def _headers(ts, recv, sign):
    return {"X-BAPI-API-KEY": BYBIT_API_KEY, "X-BAPI-SIGN": sign, "X-BAPI-SIGN-TYPE": "2", "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "Content-Type": "application/json"}

def _extract_list(result):
    if isinstance(result, list):
        return result
    return result.get("list", [])

def get_balance(coin):
    for acct in ["SPOT", "UNIFIED"]:
        try:
            ts = str(int(time.time() * 1000))
            recv = "5000"
            query = f"accountType={acct}"
            sign = _sign(ts, recv, query)
            url = f"{BASE_URL}/v5/account/wallet-balance?{query}"
            r = requests.get(url, headers=_headers(ts, recv, sign), timeout=10).json()
            if r.get("retCode") != 0:
                log(f"Saldo error {acct}: {r}")
                continue
            lst = _extract_list(r.get("result", {}))
            if not lst:
                continue
            for c in lst[0].get("coin", []):
                if c.get("coin") == coin:
                    return float(c.get("walletBalance", 0) or 0)
        except Exception as e:
            log(f"Saldo error {acct}: {e}")
            continue
    return 0.0

def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
        lst = r.get("result", {}).get("list", [])
        if lst:
            return float(lst[0].get("lastPrice", 0))
    except Exception as e:
        log(f"Price error: {e}")
    return 0.0

def get_klines():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/kline", params={"category": CATEGORY, "symbol": SYMBOL, "interval": INTERVAL, "limit": 100}, timeout=10).json()
        return list(reversed(r.get("result", {}).get("list", [])))
    except Exception as e:
        log(f"Klines error: {e}")
        return []

@app.route("/")
def home():
    return jsonify({"status": "running", "symbol": SYMBOL})

@app.route("/test-key")
def test_key():
    usdt = get_balance("USDT")
    pepe = get_balance("PEPE")
    return jsonify({"key_start": BYBIT_API_KEY[:4] + "****" if BYBIT_API_KEY else "LEEG", "usdt": usdt, "pepe": pepe, "status": "OK" if usdt is not None else "ERROR"})

@app.route("/check")
def check():
    return jsonify({"ok": True, "price": get_price(), "usdt": get_balance("USDT")})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
