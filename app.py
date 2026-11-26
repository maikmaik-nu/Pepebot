import os, time, requests, hmac, hashlib, json, threading
from flask import Flask, request, jsonify
app = Flask(__name__)

【entity-BYBIT_API_KEY¦canonical_name=Bybit】 = os.environ.get("【entity-BYBIT_API_KEY¦canonical_name=Bybit】", "")
【entity-BYBIT_API_SECRET¦canonical_name=Bybit】 = os.environ.get("【entity-BYBIT_API_SECRET¦canonical_name=Bybit】", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SYMBOL = "PEPEUSDC"
CATEGORY = "spot"
INTERVAL = "15"
BUY_AMOUNT = 100  # <-- vanmorgen: was 10, nu 100
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
    msg = ts + 【entity-BYBIT_API_KEY¦canonical_name=Bybit】 + recv + payload
    return hmac.new(【entity-BYBIT_API_SECRET¦canonical_name=Bybit】.encode(), msg.encode(), hashlib.sha256).hexdigest()

def _headers(ts, recv, sign):
    return {
        "X-BAPI-API-KEY": 【entity-BYBIT_API_KEY¦canonical_name=Bybit】,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json"
    }

def _extract_list(result):
    if isinstance(result, dict):
        return result.get("list", []) or []
    if isinstance(result, list):
        return result
    return []

def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
        if r.get("retCode") != 0:
            return 0
        lst = _extract_list(r.get("result", {}))
        if not lst:
            return 0
        return float(lst[0].get("lastPrice") or 0)
    except Exception as e:
        log(f"Prijs error: {e}")
        return 0

def get_balance(coin):
    for acct in ["UNIFIED", "SPOT", "FUND"]:
        try:
            ts = str(int(time.time() * 1000))
            recv = "5000"
            query = f"accountType={acct}&coin={coin}"
            sign = _sign(ts, recv, query)
            url = f"{BASE_URL}/v5/account/wallet-balance?{query}"
            r = requests.get(url, headers=_headers(ts, recv, sign), timeout=10).json()
            if r.get("retCode") != 0:
                continue
            lst = _extract_list(r.get("result", {}))
            if not lst:
                continue
            coins = lst[0].get("coin", []) or []
            for c in coins:
                if c.get("coin") == coin:
                    return float(c.get("walletBalance") or 0)
        except Exception:
            continue
    return 0.0

def get_free_usdc():
    return get_balance("USDC")

def get_pepe_balance():
    return get_balance("PEPE")

def is_in_positie():
    prijs = get_price()
    if prijs <= 0:
        return get_pepe_balance() > 0
    return get_pepe_balance() * prijs > 1

# FIX VAN VANMORGEN: qtyStep crasht niet meer
def get_qty_step():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/instruments-info", params={"category": "spot", "symbol": SYMBOL}, timeout=10).json()
        lst = r.get("result", {}).get("list", [])
        if lst:
            step = lst[0].get("lotSizeFilter", {}).get("qtyStep", 1)
            return float(step)
    except Exception as e:
        log(f"qtyStep fail, gebruik 1: {e}")
    return 1.0

def place_order(side, qty):
    try:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        body = {"category": CATEGORY, "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(int(qty))}
        payload = json.dumps(body)
        sign = _sign(ts, recv, payload)
        r = requests.post(f"{BASE_URL}/v5/order/create", headers=_headers(ts, recv, sign), data=payload, timeout=10).json()
        log(f"Order {side} {qty}: {r}")
        return r
    except Exception as e:
        log(f"Order error: {e}")
        return {"retCode": -1}

def do_buy():
    usdc = get_free_usdc()
    prijs = get_price()
    if usdc < BUY_AMOUNT or prijs <= 0:
        log(f"BUY blocked: USDC {usdc} prijs {prijs}")
        return
    step = get_qty_step()
    qty = int((BUY_AMOUNT / prijs) // step * step)
    if qty <= 0:
        return
    log(f"ORDER Buy {SYMBOL} qty {qty} voor {BUY_AMOUNT} USDC @ {prijs} step {step}")
    res = place_order("Buy", qty)
    if res.get("retCode") == 0:
        send_telegram(f"PEPE GEKOCHT: {qty} PEPE voor {BUY_AMOUNT} USDC @ {prijs}")

def do_sell():
    pepe = get_pepe_balance()
    prijs = get_price()
    if pepe <= 0 or prijs <= 0:
        log(f"SELL blocked: PEPE {pepe} prijs {prijs}")
        return
    qty = int(pepe)
    log(f"ORDER Sell {SYMBOL} qty {qty} @ {prijs}")
    res = place_order("Sell", qty)
    if res.get("retCode") == 0:
        send_telegram(f"PEPE VERKOCHT: {qty} PEPE @ {prijs}")

def get_klines():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/kline", params={"category": CATEGORY, "symbol": SYMBOL, "interval": INTERVAL, "limit": 200}, timeout=10).json()
        return _extract_list(r.get("result", {}))
    except Exception as e:
        log(f"Klines error: {e}")
        return []

# STRATEGIE: ALLEEN UT BOT - GEEN SUPERTREND
def utbot_signal():
    klines = get_klines()
    if len(klines) < ATR_PERIOD + 2:
        return 0
    klines = list(reversed(klines))
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    trs = []
    for i in range(1, len(klines)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr = sum(trs[-ATR_PERIOD:]) / ATR_PERIOD if trs else 0
    if atr == 0:
        return 0
    last_close = closes[-1]
    prev_close = closes[-2]
    if last_close > prev_close + atr * 0.1:
        return 1
    if last_close < prev_close - atr * 0.1:
        return -1
    return 0

last_handled = 0

def bot_loop():
    global last_handled
    log("Bot gestart - PEPEUSDC - UT BOT ONLY - Buy 100 - bybit.eu - 26 nov - qtyStep fix")
    send_telegram("Bot gestart: PEPEUSDC - UT BOT ONLY - Buy 100")
    while True:
        try:
            prijs = get_price()
            usdc = get_free_usdc()
            pepe = get_pepe_balance()
            sig = utbot_signal()
            log(f"CHECK: USDC={usdc} PEPE={pepe} PRICE={prijs} UTBOT={'buy' if sig==1 else 'sell' if sig==-1 else 'neutraal'} Candles=200")
            in_pos = is_in_positie()
            if sig == 1 and not in_pos and last_handled != 1:
                do_buy()
                last_handled = 1
            elif sig == -1 and in_pos and last_handled != -1:
                do_sell()
                last_handled = -1
            else:
                log(f"AUTO CHECK klaar: status hold, signal {'buy' if sig==1 else 'sell' if sig==-1 else 'neutraal'}")
            time.sleep(60)
        except Exception as e:
            log(f"Loop error: {e}")
            time.sleep(60)

@app.route("/")
def home():
    return f"Bot live - Prijs: {get_price()} - USDC: {get_free_usdc()} - PEPE: {get_pepe_balance()}"

@app.route("/ping")
def ping():
    return "PONG"

@app.route("/status")
def status():
    return jsonify({"prijs": get_price(), "usdc": get_free_usdc(), "pepe": get_pepe_balance(), "symbool": SYMBOL, "utbot": utbot_signal()})

@app.route("/debug-key")
def debug_key():
    return jsonify({"key_len": len(BYBIT_API_KEY), "key_start": BYBIT_API_KEY[:4] if BYBIT_API_KEY else "", "usdc": get_free_usdc(), "pepe": get_pepe_balance()})

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
start_loop_once()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
