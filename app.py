import os, time, requests, hmac, hashlib, json, threading
from flask import Flask, request, jsonify

app = Flask(__name__)

BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
INTERVAL = "15"
KEY_VALUE = 4
ATR_PERIOD = 14
BASE_URL = "https://api.bybit.eu"

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

def _sign(ts, recv, payload=""):
    msg = ts + BYBIT_API_KEY + recv + payload
    return hmac.new(BYBIT_API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

def _headers(ts, recv, sign):
    return {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json"
    }

def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers",
                         params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
        return float(r["result"]["list"][0]["lastPrice"])
    except Exception as e:
        log(f"Prijs error: {e}")
        return 0

def get_balance(coin):
    # ALLES VIA SPOT - dit is de fix voor jouw 100.24 USDT
    try:
        if not BYBIT_API_KEY or not BYBIT_API_SECRET:
            return 0.0
        ts = str(int(time.time() * 1000))
        recv = "5000"
        query = f"accountType=SPOT&coin={coin}"
        sign = _sign(ts, recv, query)
        url = f"{BASE_URL}/v5/account/wallet-balance?{query}"
        r = requests.get(url, headers=_headers(ts, recv, sign), timeout=10).json()
        if r.get("retCode") != 0:
            log(f"Saldo error {coin}: {r}")
            return 0.0
        coins = r["result"]["list"][0]["coin"]
        for c in coins:
            if c["coin"] == coin:
                return float(c.get("walletBalance") or 0)
        return 0.0
    except Exception as e:
        log(f"Saldo error {coin}: {e}")
        return 0.0

def get_free_usdt():
    return get_balance("USDT")

def get_pepe_balance():
    return get_balance("PEPE")

def is_in_positie():
    prijs = get_price()
    if prijs <= 0:
        return False
    waarde = get_pepe_balance() * prijs
    return waarde >= 10

def place_order(side, qty):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    body = {"category": "spot", "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty)}
    body_str = json.dumps(body)
    sign = _sign(ts, recv, body_str)
    r = requests.post(f"{BASE_URL}/v5/order/create",
                      headers=_headers(ts, recv, sign), data=body_str, timeout=10)
    log(f"ORDER {side} qty {qty}: {r.text}")
    try:
        return r.json()
    except Exception:
        return {"retCode": -1, "retMsg": r.text}

def do_buy():
    usdt = get_free_usdt()
    log(f"UT BOT BUY - USDT SPOT: {usdt}")
    if usdt < 10:
        send_telegram(f"BUY: te weinig USDT SPOT ({usdt:.2f})")
        return
    bedrag = usdt * 0.998
    prijs = get_price()
    if prijs <= 0:
        return
    qty = int((bedrag / prijs) // 1000 * 1000)
    if qty < 1000:
        send_telegram("BUY: qty te klein")
        return
    res = place_order("Buy", qty)
    if res.get("retCode") == 0:
        send_telegram(f"ECHT GEKOCHT {SYMBOL} SPOT Bedrag: {bedrag:.2f} USDT Qty: {qty} Prijs: {prijs}")
    else:
        send_telegram(f"BUY mislukt: {res}")

def do_sell():
    pepe = get_pepe_balance()
    log(f"UT BOT SELL - PEPE SPOT: {pepe}")
    if pepe < 1000:
        send_telegram(f"SELL: te weinig PEPE SPOT ({pepe})")
        return
    qty = int(pepe // 1000 * 1000)
    res = place_order("Sell", qty)
    if res.get("retCode") == 0:
        send_telegram(f"ECHT VERKOCHT {SYMBOL} SPOT Qty: {qty}")
    else:
        send_telegram(f"SELL mislukt: {res}")

def get_klines():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/kline",
                         params={"category": CATEGORY, "symbol": SYMBOL, "interval": INTERVAL, "limit": 200},
                         timeout=10).json()
        if r.get("retCode") != 0:
            log(f"Kline error: {r}")
            return [], [], []
        klines = r["result"]["list"][::-1]
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        return closes, highs, lows
    except Exception as e:
        log(f"Kline error: {e}")
        return [], [], []

def utbot_signal():
    closes, highs, lows = get_klines()
    if len(closes) < 30:
        return "NONE"

    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)

    atr_at = [None] * len(closes)
    atr = sum(trs[:ATR_PERIOD]) / ATR_PERIOD
    atr_at[ATR_PERIOD] = atr
    for i in range(ATR_PERIOD + 1, len(closes)):
        atr = (atr * (ATR_PERIOD - 1) + trs[i-1]) / ATR_PERIOD
        atr_at[i] = atr

    trailing = None
    last_signal = "NONE"

    for i in range(ATR_PERIOD, len(closes)):
        nloss = KEY_VALUE * atr_at[i]
        close = closes[i]
        prev_close = closes[i-1]

        if trailing is None:
            trailing = close - nloss if close > prev_close else close + nloss
        else:
            prev_trailing = trailing
            if close > prev_trailing and prev_close > prev_trailing:
                trailing = max(prev_trailing, close - nloss)
            elif close < prev_trailing and prev_close < prev_trailing:
                trailing = min(prev_trailing, close + nloss)
            elif close > prev_trailing:
                trailing = close - nloss
            else:
                trailing = close + nloss

        if close > trailing:
            last_signal = "BUY"
        elif close < trailing:
            last_signal = "SELL"

    return last_signal

last_handled_signal = None

def bot_loop():
    global last_handled_signal
    log("Bot loop gestart - UT Bot zelf berekenen - SPOT via bybit.eu")
    while True:
        try:
            signaal = utbot_signal()
            in_pos = is_in_positie()
            prijs = get_price()
            log(f"Check - UT Bot: {signaal} - In positie: {in_pos} - Prijs: {prijs}")
            if signaal == "BUY" and not in_pos and last_handled_signal != "BUY":
                do_buy()
                last_handled_signal = "BUY"
            elif signaal == "SELL" and in_pos and last_handled_signal != "SELL":
                do_sell()
                last_handled_signal = "SELL"
            elif signaal == "BUY" and in_pos:
                last_handled_signal = "BUY"
            elif signaal == "SELL" and not in_pos:
                last_handled_signal = "SELL"
        except Exception as e:
            log(f"Loop error: {e}")
        time.sleep(60)

@app.route("/")
def home():
    return "Pepebot FINAL - UT Bot zelf - SPOT - bybit.eu - Rente-op-rente AAN"

@app.route("/ping")
def ping():
    return jsonify({"status": "Pong", "in_positie": is_in_positie(), "utbot": "4,14"})

@app.route("/status")
def status():
    return jsonify({"usdt_spot": get_free_usdt(), "pepe_spot": get_pepe_balance(), "prijs": get_price(), "in_positie": is_in_positie(), "utbot_signaal": utbot_signal()})

@app.route("/webhook", methods=["POST"])
def webhook():
    return jsonify({"ok": True, "msg": "Bot berekent UT Bot nu zelf, geen webhook meer nodig"})

@app.route("/telegram", methods=["POST"])
def telegram():
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("message", {}).get("text", "")
    if text == "/ping":
        send_telegram("PONG - Ik ben LIVE - UT Bot zelf - SPOT - bybit.eu")
    elif text == "/status":
        send_telegram(f"Status SPOT: USDT {get_free_usdt():.2f} - PEPE {get_pepe_balance():.0f} - In positie: {is_in_positie()} - UT Bot: {utbot_signal()}")
    return jsonify({"ok": True})

_loop_started = False
_lock = threading.Lock()

def start_loop_once():
    global _loop_started
    with _lock:
        if not _loop_started:
            _loop_started = True
            send_telegram("Bot gestart - UT Bot zelf - SPOT via bybit.eu - Rente-op-rente AAN - Start met 100")
            threading.Thread(target=bot_loop, daemon=True).start()

start_loop_once()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
