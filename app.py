import os, time, requests, hmac, hashlib, json, threading, math

from flask import Flask, request, jsonify
app = Flask(__name__)

# ===== CONFIG =====
BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# BTC werkt op 【entity-Bybit¦canonical_name=Bybit】 EU, PEPE niet. Daarom BTC.
SYMBOL = "BTCUSDT"
CATEGORY = "spot"
INTERVAL = "15"
ATR_PERIOD = 14
ATR_MULT = 1.0  # UT Bot multiplier, 1.0 = standaard gevoelig
BASE_URL = "https://api.bybit.eu"
MIN_ORDER_USDT = 5.0
STATE_FILE = "state.json"

def log(msg):
    print(msg, flush=True)

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        log(f"Telegram error: {e}")

# ===== STATE - FIX: geheugen blijft bewaard na Render-restart =====
def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                d = json.load(f)
                return int(d.get("last_handled_signal", 0))
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

# ===== BYBIT SIGNING - FIX: SIGN-TYPE toegevoegd =====
def _sign(ts, recv, payload=""):
    msg = ts + BYBIT_API_KEY + recv + payload
    return hmac.new(BYBIT_API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

def _headers(ts, recv, sign):
    return {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "X-BAPI-SIGN-TYPE": "2",
        "Content-Type": "application/json"
    }

def _extract_list(result):
    if isinstance(result, dict):
        return result.get("list", []) or []
    if isinstance(result, list):
        return result
    return []

def _safe_json(resp):
    if resp.status_code != 200:
        log(f"HTTP {resp.status_code}: {resp.text[:300]}")
        return None
    try:
        return resp.json()
    except Exception as e:
        log(f"Geen JSON: {resp.text[:300]} err={e}")
        return None

def get_price():
    try:
        resp = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
        r = _safe_json(resp)
        if not r or r.get("retCode") != 0:
            log(f"Prijs error: {r}")
            return 0
        lst = _extract_list(r.get("result", {}))
        if not lst:
            return 0
        return float(lst[0].get("lastPrice") or 0)
    except Exception as e:
        log(f"Prijs error: {e}")
        return 0

def get_balance(coin):
    # FIX: Alleen UNIFIED. SPOT bestaat niet in Bybit V5 wallet-balance
    # en gaf altijd 10003 "not authorized".
    if not BYBIT_API_KEY or not BYBIT_API_SECRET:
        log("Geen API key/secret")
        return 0.0
    try:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        query = f"accountType=UNIFIED&coin={coin}"
        sign = _sign(ts, recv, query)
        url = f"{BASE_URL}/v5/account/wallet-balance?{query}"
        resp = requests.get(url, headers=_headers(ts, recv, sign), timeout=10)
        r = _safe_json(resp)
        if not r:
            return 0.0
        if r.get("retCode") != 0:
            log(f"Saldo error {coin} UNIFIED: {r}")
            return 0.0
        lst = _extract_list(r.get("result", {}))
        if not lst:
            return 0.0
        coins = lst[0].get("coin", []) or []
        for c in coins:
            if c.get("coin") == coin:
                try:
                    return float(c.get("walletBalance") or 0)
                except Exception:
                    return 0.0
    except Exception as e:
        log(f"Saldo error {coin} UNIFIED: {e}")
    return 0.0

def get_free_usdt():
    return get_balance("USDT")

def get_coin_balance():
    base = SYMBOL.replace("USDT", "").replace("USDC", "")
    return get_balance(base)

def get_symbol_info():
    try:
        resp = requests.get(f"{BASE_URL}/v5/market/instruments-info", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
        r = _safe_json(resp)
        if not r or r.get("retCode") != 0:
            return None
        lst = _extract_list(r.get("result", {}))
        if not lst:
            return None
        return lst[0]
    except Exception:
        return None

def round_qty(qty):
    info = get_symbol_info()
    if not info:
        return round(qty, 6)
    try:
        step = float(info.get("lotSizeFilter", {}).get("qtyStep", "0.000001"))
        min_qty = float(info.get("lotSizeFilter", {}).get("minOrderQty", "0"))
        qty = math.floor(qty / step) * step
        if qty < min_qty:
            return 0
        return round(qty, 8)
    except Exception:
        return round(qty, 6)

def is_in_positie():
    # FIX: nooit traden op prijs 0
    prijs = get_price()
    if prijs <= 0:
        return get_coin_balance() > 0
    return get_coin_balance() * prijs > 1

def place_order(side, qty):
    try:
        qty = round_qty(qty)
        if qty <= 0:
            log(f"Order {side}: qty te klein na afronden")
            return {}
        ts = str(int(time.time() * 1000))
        recv = "5000"
        body = {"category": CATEGORY, "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty)}
        payload = json.dumps(body)
        sign = _sign(ts, recv, payload)
        resp = requests.post(f"{BASE_URL}/v5/order/create", headers=_headers(ts, recv, sign), data=payload, timeout=10)
        r = _safe_json(resp)
        log(f"Order {side} {qty}: {r}")
        return r or {}
    except Exception as e:
        log(f"Order error: {e}")
        return {}

def do_buy():
    usdt = get_free_usdt()
    prijs = get_price()
    if usdt < MIN_ORDER_USDT or prijs <= 0:
        log(f"BUY: te weinig USDT ({usdt}) of geen prijs ({prijs})")
        return
    qty = usdt / prijs
    log(f"BUY: {qty} {SYMBOL} voor {usdt} USDT @ {prijs}")
    res = place_order("Buy", qty)
    if res and res.get("retCode") == 0:
        send_telegram(f"BUY: {SYMBOL} @ {prijs} - USDT: {usdt}")
    else:
        log(f"BUY mislukt: {res}")

def do_sell():
    bal = get_coin_balance()
    prijs = get_price()
    if bal <= 0 or prijs <= 0:
        log(f"SELL: geen {SYMBOL} ({bal}) of geen prijs ({prijs})")
        return
    log(f"SELL: {bal} {SYMBOL} @ {prijs}")
    res = place_order("Sell", bal)
    if res and res.get("retCode") == 0:
        send_telegram(f"SELL: {SYMBOL} @ {prijs}")
    else:
        log(f"SELL mislukt: {res}")

def get_klines():
    try:
        resp = requests.get(f"{BASE_URL}/v5/market/kline", params={"category": CATEGORY, "symbol": SYMBOL, "interval": INTERVAL, "limit": 200}, timeout=10)
        r = _safe_json(resp)
        if not r:
            return []
        return _extract_list(r.get("result", {}))
    except Exception as e:
        log(f"Klines error: {e}")
        return []

def utbot_signal():
    # FIX: echte UT Bot trailing-stop, niet alleen close vs prev_close
    klines = get_klines()
    if len(klines) < ATR_PERIOD + 2:
        return 0
    klines = list(reversed(klines))  # oud -> nieuw
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]

    # ATR
    trs = []
    for i in range(1, len(klines)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atrs = []
    for i in range(len(trs)):
        window = trs[max(0, i-ATR_PERIOD+1):i+1]
        atrs.append(sum(window)/len(window))

    # Trailing stop
    trailing = None
    trend = 0
    for i in range(1, len(closes)):
        atr = atrs[i-1] if i-1 < len(atrs) else atrs[-1]
        if closes[i] > closes[i-1]:
            new_stop = max(lows[i] - atr * ATR_MULT, trailing) if trailing is not None else lows[i] - atr * ATR_MULT
            if trend == -1 and closes[i] > trailing:
                trend = 1
            trailing = new_stop
        else:
            new_stop = min(highs[i] + atr * ATR_MULT, trailing) if trailing is not None else highs[i] + atr * ATR_MULT
            if trend == 1 and closes[i] < trailing:
                trend = -1
            trailing = new_stop

    if trailing is None:
        return 0
    last_close = closes[-1]
    prev_close = closes[-2]
    if prev_close <= trailing and last_close > trailing:
        return 1
    if prev_close >= trailing and last_close < trailing:
        return -1
    if last_close > trailing:
        return 1 if trend != 1 else 0
    if last_close < trailing:
        return -1 if trend != -1 else 0
    return 0

def bot_loop():
    global last_handled_signal
    log(f"Bot loop gestart - {SYMBOL} - SPOT via bybit.eu - GEEN stop-loss - Rente-op-rente AAN - last_signal={last_handled_signal}")
    send_telegram(f"Bot gestart: {SYMBOL} - SPOT via bybit.eu - GEEN stop-loss - Rente-op-rente AAN")
    while True:
        try:
            prijs = get_price()
            if prijs <= 0:
                log("Check overgeslagen: geen prijs")
                time.sleep(60)
                continue
            usdt = get_free_usdt()
            log(f"Check - Prijs: {prijs} - USDT: {usdt} - last_signal: {last_handled_signal}")
            sig = utbot_signal()
            if sig == 1 and last_handled_signal != 1:
                if not is_in_positie():
                    do_buy()
                    last_handled_signal = 1
                    save_state(1)
            elif sig == -1 and last_handled_signal != -1:
                if is_in_positie():
                    do_sell()
                    last_handled_signal = -1
                    save_state(-1)
            time.sleep(60)
        except Exception as e:
            log(f"Loop error: {e}")
            time.sleep(60)

@app.route("/")
def home():
    return f"Bot live - {SYMBOL} Prijs: {get_price()} - USDT: {get_free_usdt()}"

@app.route("/ping")
def ping():
    return "PONG"

@app.route("/status")
def status():
    return jsonify({"prijs": get_price(), "usdt": get_free_usdt(), "coin": get_coin_balance(), "symbool": SYMBOL, "last_signal": last_handled_signal})

@app.route("/webhook", methods=["POST"])
def webhook():
    return jsonify({"ok": True})

@app.route("/telegram", methods=["POST"])
def telegram():
    return jsonify({"ok": True})

_loop_started = False
_lock = threading.Lock()
def start_loop_once():
    global _loop_started
    with _lock:
        if not _loop_started:
            _loop_started = True
            threading.Thread(target=bot_loop, daemon=True).start()

# FIX: default = 1, dus hij start ook als RUN_BOT ontbreekt in Render.
# Zet RUN_BOT=0 als je hem expres uit wil zetten.
if os.environ.get("RUN_BOT", "1") == "1":
    start_loop_once()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
