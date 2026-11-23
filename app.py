import os, time, requests, hmac, hashlib, json, threading
from flask import Flask, request, jsonify
app = Flask(__name__)

BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

SYMBOL = "PEPEUSDC"
CATEGORY = "spot"
INTERVAL = "15"
BASE_URL = "https://api.bybit.eu"
BASE_COIN = "USDC"

def log(m): print(m, flush=True)

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e: log(f"Telegram error: {e}")

def _sign(ts, recv, payload=""):
    msg = ts + BYBIT_API_KEY + recv + payload
    return hmac.new(BYBIT_API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

def _headers(ts, recv, sign):
    return {"X-BAPI-API-KEY": BYBIT_API_KEY, "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv, "X-BAPI-SIGN": sign,
            "Content-Type": "application/json"}

def _extract_list(result):
    if isinstance(result, dict): return result.get("list", []) or []
    if isinstance(result, list): return result
    return []

def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers",
            params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
        if r.get("retCode") != 0: return 0
        lst = _extract_list(r.get("result", {}))
        return float(lst[0].get("lastPrice") or 0) if lst else 0
    except: return 0

def get_balance(coin):
    # Alleen SPOT, daar staat je USDC
    try:
        ts = str(int(time.time()*1000)); recv = "5000"
        query = f"accountType=SPOT&coin={coin}"
        sign = _sign(ts, recv, query)
        r = requests.get(f"{BASE_URL}/v5/account/wallet-balance?{query}",
            headers=_headers(ts, recv, sign), timeout=10).json()
        if r.get("retCode") != 0:
            log(f"Saldo error {coin}: {r}")
            return 0.0
        lst = _extract_list(r.get("result", {}))
        if not lst: return 0.0
        for c in lst[0].get("coin", []) or []:
            if c.get("coin") == coin:
                return float(c.get("walletBalance") or 0)
    except Exception as e: log(f"Saldo error {coin}: {e}")
    return 0.0

def get_free_base(): return get_balance(BASE_COIN)
def get_pepe(): return get_balance("PEPE")

def is_in_positie():
    p = get_price()
    if p <= 0: return get_pepe() > 0
    return get_pepe() * p > 1

def place_order(side, qty):
    try:
        ts = str(int(time.time()*1000)); recv = "5000"
        body = {"category": CATEGORY, "symbol": SYMBOL, "side": side,
                "orderType": "Market", "qty": str(qty)}
        payload = json.dumps(body)
        sign = _sign(ts, recv, payload)
        r = requests.post(f"{BASE_URL}/v5/order/create",
            headers=_headers(ts, recv, sign), data=payload, timeout=10).json()
        log(f"Order {side} {qty}: {r}")
        return r
    except Exception as e:
        log(f"Order error: {e}"); return {}

def do_buy():
    base = get_free_base(); prijs = get_price()
    if base < 5 or prijs <= 0:
        log(f"BUY: te weinig {BASE_COIN} ({base}) of geen prijs ({prijs})"); return
    qty = int((base * 0.999) / prijs)
    if qty <= 0: return
    log(f"BUY: {qty} PEPE voor {base} {BASE_COIN} @ {prijs}")
    place_order("Buy", qty)
    send_telegram(f"BUY: {qty} PEPE @ {prijs} - {BASE_COIN}: {base}")

def do_sell():
    pepe = get_pepe(); prijs = get_price()
    if pepe <= 0 or prijs <= 0:
        log(f"SELL: geen PEPE ({pepe})"); return
    qty = int(pepe)
    log(f"SELL: {qty} PEPE @ {prijs}")
    place_order("Sell", qty)
    send_telegram(f"SELL: {qty} PEPE @ {prijs}")

def get_klines():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/kline",
            params={"category": CATEGORY, "symbol": SYMBOL, "interval": INTERVAL, "limit": 200}, timeout=10).json()
        return _extract_list(r.get("result", {}))
    except: return []

def utbot_signal():
    klines = get_klines()
    if len(klines) < 16: return 0
    klines = list(reversed(klines))
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(1, len(klines))]
    atr = sum(trs[-14:]) / 14 if trs else 0
    if closes[-1] > closes[-2] + atr*0.1: return 1
    if closes[-1] < closes[-2] - atr*0.1: return -1
    return 0

last_signal = 0
def bot_loop():
    global last_signal
    log(f"Bot loop gestart - PEPEUSDC - SPOT via bybit.eu - base {BASE_COIN}")
    send_telegram(f"Bot gestart: PEPEUSDC - SPOT - base {BASE_COIN}")
    while True:
        try:
            prijs = get_price(); base = get_free_base()
            log(f"Check - Prijs: {prijs} - {BASE_COIN}: {base}")
            sig = utbot_signal()
            if sig == 1 and last_signal != 1:
                if not is_in_positie(): do_buy(); last_signal = 1
            elif sig == -1 and last_signal != -1:
                if is_in_positie(): do_sell(); last_signal = -1
            time.sleep(60)
        except Exception as e:
            log(f"Loop error: {e}"); time.sleep(60)

@app.route("/")
def home(): return f"Bot live - Prijs: {get_price()} - {BASE_COIN}: {get_free_base()}"
@app.route("/ping")
def ping(): return "PONG"
@app.route("/status")
def status(): return jsonify({"prijs": get_price(), "usdc": get_free_base(), "pepe": get_pepe(), "symbool": SYMBOL})
@app.route("/test-key")
def test_key():
    return jsonify({"api_key_set": bool(BYBIT_API_KEY), "base": BASE_COIN, "usdc": get_free_base(), "pepe": get_pepe(), "prijs": get_price()})
@app.route("/webhook", methods=["POST"])
def webhook(): return jsonify({"ok": True})

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
