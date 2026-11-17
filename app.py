import os, time, requests, hmac, hashlib, json, threading, math
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

def log(msg):
    print(msg, flush=True)

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        log(f"Telegram error: {e}")

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                return int(json.load(f).get("last_handled_signal", 0))
    except Exception:
        pass
    return 0

def save_state(sig):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"last_handled_signal": int(sig)}, f)
    except Exception as e:
        log(f"State save error: {e}")

last_handled_signal = load_state()

def _sign(ts, recv, payload=""):
    msg = ts + BYBIT_API_KEY + recv + payload
    return hmac.new(BYBIT_API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

def _headers(ts, recv, sign):
    return {"X-BAPI-API-KEY": BYBIT_API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "X-BAPI-SIGN": sign, "X-BAPI-SIGN-TYPE": "2", "Content-Type": "application/json"}

def _extract_list(result):
    if isinstance(result, dict):
        return result.get("list", []) or []
    if isinstance(result, list):
        return result
    return []

def _safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {}

def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
        data = _safe_json(r)
        if data.get("retCode") != 0:
            log(f"Prijs error retCode: {data}")
            return 0
        lst = _extract_list(data.get("result", {}))
        if not lst:
            return 0
        return float(lst[0].get("lastPrice") or 0)
    except Exception as e:
        log(f"Prijs error: {e}")
        return 0

def get_balance(coin):
    # SPOT EERST, daar staat je geld. Daarna UNIFIED. Met query-signing.
    for acct in ["SPOT", "UNIFIED"]:
        try:
            if not BYBIT_API_KEY or not BYBIT_API_SECRET:
                return 0.0
            ts = str(int(time.time()*1000)); recv = "5000"
            query = f"accountType={acct}&coin={coin}"
            sign = _sign(ts, recv, query)
            url = f"{BASE_URL}/v5/account/wallet-balance?{query}"
            r = requests.get(url, headers=_headers(ts, recv, sign), timeout=10)
            data = _safe_json(r)
            if data.get("retCode") != 0:
                log(f"Saldo error {coin} {acct}: {data}")
                continue
            lst = _extract_list(data.get("result", {}))
            if not lst:
                continue
            for c in lst[0].get("coin", []) or []:
                if c.get("coin") == coin:
                    bal = float(c.get("walletBalance") or 0)
                    if bal > 0:
                        return bal
        except Exception as e:
            log(f"Saldo error {coin} {acct}: {e}")
            continue
    return 0.0

def get_free_usdt(): return get_balance("USDT")
def get_pepe_balance(): return get_balance("PEPE")

def is_in_positie():
    prijs = get_price()
    if prijs <= 0:
        return get_pepe_balance() > 0
    return get_pepe_balance() * prijs > 1

def place_order(side, qty):
    try:
        ts = str(int(time.time()*1000)); recv = "5000"
        body = {"category": CATEGORY, "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty)}
        payload = json.dumps(body)
        sign = _sign(ts, recv, payload)
        r = requests.post(f"{BASE_URL}/v5/order/create", headers=_headers(ts, recv, sign), data=payload, timeout=10)
        data = _safe_json(r)
        log(f"Order {side} {qty}: {data}")
        return data
    except Exception as e:
        log(f"Order error: {e}")
        return {}

def do_buy():
    usdt = get_free_usdt(); prijs = get_price()
    if usdt < 5 or prijs <= 0:
        log(f"BUY: te weinig USDT ({usdt}) of geen prijs ({prijs})")
        return
    qty = int(usdt / prijs)  # rente-op-rente: hele vrije USDT
    if qty <= 0: return
    log(f"BUY: {qty} PEPE voor {usdt} USDT @ {prijs}")
    place_order("Buy", qty)
    send_telegram(f"BUY: {qty} PEPE @ {prijs} - USDT: {usdt}")

def do_sell():
    pepe = get_pepe_balance(); prijs = get_price()
    if pepe <= 0 or prijs <= 0:
        log(f"SELL: geen PEPE ({pepe}) of geen prijs ({prijs})")
        return
    qty = int(pepe)
    log(f"SELL: {qty} PEPE @ {prijs}")
    place_order("Sell", qty)
    send_telegram(f"SELL: {qty} PEPE @ {prijs}")

def get_klines():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/kline", params={"category": CATEGORY, "symbol": SYMBOL, "interval": INTERVAL, "limit": 200}, timeout=10)
        data = _safe_json(r)
        return _extract_list(data.get("result", {}))
    except Exception as e:
        log(f"Klines error: {e}")
        return []

def utbot_signal():
    klines = get_klines()
    if len(klines) < ATR_PERIOD + 2: return 0
    klines = list(reversed(klines))
    closes = [float(k[4]) for k in klines]; highs = [float(k[2]) for k in klines]; lows = [float(k[3]) for k in klines]
    trs = []
    for i in range(1, len(klines)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr = sum(trs[-ATR_PERIOD:]) / ATR_PERIOD if trs else 0
    if closes[-1] > closes[-2] + atr * 0.1: return 1
    if closes[-1] < closes[-2] - atr * 0.1: return -1
    return 0

def bot_loop():
    global last_handled_signal
    log("Bot loop gestart - GEEN stop-loss - Rente-op-rente AAN - SPOT via 【entity-bybit.eu¦canonical_name=BYBIT】")
    send_telegram("Bot gestart: GEEN stop-loss - Rente-op-rente AAN - SPOT via 【entity-bybit.eu¦canonical_name=BYBIT】")
    while True:
        try:
            prijs = get_price(); usdt = get_free_usdt()
            log(f"Check - Prijs: {prijs} - USDT: {usdt} - In positie: {is_in_positie()}")
            if prijs <= 0:
                time.sleep(60); continue
            sig = utbot_signal()
            if sig == 1 and last_handled_signal != 1:
                if not is_in_positie():
                    do_buy(); last_handled_signal = 1; save_state(1)
            elif sig == -1 and last_handled_signal != -1:
                if is_in_positie():
                    do_sell(); last_handled_signal = -1; save_state(-1)
            time.sleep(60)
        except Exception as e:
            log(f"Loop error: {e}"); time.sleep(60)

@app.route("/")
def home(): return f"Bot live - Prijs: {get_price()} - USDT: {get_free_usdt()}"
@app.route("/ping")
def ping(): return "PONG"
@app.route("/status")
def status(): return jsonify({"prijs": get_price(), "usdt": get_free_usdt(), "pepe": get_pepe_balance(), "symbool": SYMBOL})
@app.route("/check")
def check():
    try:
        prijs = get_price(); klines = get_klines()
        send_telegram("TEST: bot check OK")
        return jsonify({"api_keys": bool(BYBIT_API_KEY and BYBIT_API_SECRET), "telegram": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID), "prijs": prijs, "usdt": get_free_usdt(), "pepe": get_pepe_balance(), "in_positie": is_in_positie(), "klines": len(klines)})
    except Exception as e:
        return jsonify({"error": str(e)})
@app.route("/webhook", methods=["POST"])
def webhook(): return jsonify({"ok": True})
@app.route("/telegram", methods=["POST"])
def telegram(): return jsonify({"ok": True})

_loop_started = False
_lock = threading.Lock()
def start_loop_once():
    global _loop_started
    with _lock:
        if not _loop_started:
            _loop_started = True
            threading.Thread(target=bot_loop, daemon=True).start()
start_loop_once()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
