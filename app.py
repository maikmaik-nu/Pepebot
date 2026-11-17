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

START_CAPITAL = 100.0
USE_COMPOUND = True
USE_STOP_LOSS = False

STATE = {"in_position": False, "entry_price": 0.0, "qty": 0.0, "last_check": 0}
STATE_LOCK = threading.Lock()
_CHECK_RUNNING = False

def log(msg):
    print(msg, flush=True)

def send_telegram(msg):
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

def get_balance_raw(acct):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    query = f"accountType={acct}"
    sign = _sign(ts, recv, query)
    url = f"{BASE_URL}/v5/account/wallet-balance?{query}"
    r = requests.get(url, headers=_headers(ts, recv, sign), timeout=10).json()
    return r

def get_balance(coin):
    total = 0.0
    for acct in ["SPOT", "UNIFIED", "FUND", "CONTRACT"]:
        try:
            r = get_balance_raw(acct)
            if r.get("retCode") != 0:
                log(f"Saldo error {acct}: {r}")
                continue
            lst = _extract_list(r.get("result", {}))
            if not lst:
                continue
            for entry in lst:
                for c in entry.get("coin", []):
                    if c.get("coin") == coin:
                        total += float(c.get("walletBalance", 0) or 0)
        except Exception as e:
            log(f"Saldo error {acct}: {e}")
            continue
    return total

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

def get_qty_step():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/instruments-info", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
        lst = r.get("result", {}).get("list", [])
        if lst:
            f = lst[0].get("lotSizeFilter", {})
            return float(f.get("qtyStep", 1)), float(f.get("minOrderQty", 1))
    except Exception as e:
        log(f"Instrument error: {e}")
    return 1.0, 1.0

def place_order(side, qty):
    try:
        qty_step, min_qty = get_qty_step()
        from decimal import Decimal, ROUND_DOWN
        step = Decimal(str(qty_step))
        q = Decimal(str(qty))
        qty = float((q / step).to_integral_value(rounding=ROUND_DOWN) * step)
        if qty < min_qty:
            log(f"Order te klein: {qty}")
            return None
        ts = str(int(time.time() * 1000))
        recv = "5000"
        body = {"category": CATEGORY, "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty)}
        body_str = json.dumps(body, separators=(",", ":"))
        sign = _sign(ts, recv, body_str)
        r = requests.post(f"{BASE_URL}/v5/order/create", headers=_headers(ts, recv, sign), data=body_str, timeout=15).json()
        log(f"Order result: {r}")
        return r if r.get("retCode") == 0 else None
    except Exception as e:
        log(f"Order exception: {e}")
        return None

def check_bot():
    global _CHECK_RUNNING
    if _CHECK_RUNNING:
        return
    _CHECK_RUNNING = True
    try:
        usdt = get_balance("USDT")
        pepe = get_balance("PEPE")
        log(f"Saldo USDT: {usdt} | PEPE: {pepe}")
        klines = get_klines()
        if len(klines) < 5:
            return
        price = get_price()
        if price == 0:
            price = float(klines[-1][4])
        last = klines[-1]
        prev = klines[-2]
        last_open = float(last[1]); last_close = float(last[4]); prev_close = float(prev[4])
        with STATE_LOCK:
            in_pos = STATE["in_position"]
            if pepe > 0 and not in_pos:
                STATE["in_position"] = True; STATE["qty"] = pepe; in_pos = True
            if pepe == 0 and in_pos:
                STATE["in_position"] = False; STATE["qty"] = 0; in_pos = False
        if not in_pos and last_close > last_open and last_close > prev_close:
            if usdt < 5:
                log("Te weinig USDT")
                return
            qty = usdt / price if USE_COMPOUND else START_CAPITAL / price
            res = place_order("Buy", qty)
            if res:
                with STATE_LOCK:
                    STATE["in_position"] = True; STATE["entry_price"] = price; STATE["qty"] = qty
                send_telegram(f"GEKOCHT PEPEUSDT Prijs: {price} Inleg: {usdt} USDT GEEN stop-loss Rente-op-rente AAN")
            return
        if in_pos and last_close < last_open and last_close < prev_close:
            if pepe <= 0:
                return
            res = place_order("Sell", pepe)
            if res:
                with STATE_LOCK:
                    STATE["in_position"] = False; STATE["qty"] = 0; STATE["entry_price"] = 0
                send_telegram(f"VERKOCHT PEPEUSDT Prijs: {price} Saldo: {usdt} USDT")
            return
        log(f"Geen signaal. In positie: {in_pos} Prijs: {price}")
    finally:
        _CHECK_RUNNING = False

def loop():
    while True:
        try:
            check_bot()
        except Exception as e:
            log(f"Loop error: {e}")
        time.sleep(900)

threading.Thread(target=loop, daemon=True).start()

@app.route("/")
def home():
    return jsonify({"status": "running", "symbol": SYMBOL})

@app.route("/test-key")
def test_key():
    usdt = get_balance("USDT")
    pepe = get_balance("PEPE")
    return jsonify({"key_start": BYBIT_API_KEY[:4] + "****" if BYBIT_API_KEY else "LEEG", "usdt": usdt, "pepe": pepe, "status": "OK"})

@app.route("/debug")
def debug():
    out = {}
    for acct in ["SPOT", "UNIFIED", "FUND", "CONTRACT"]:
        try:
            out[acct] = get_balance_raw(acct)
        except Exception as e:
            out[acct] = str(e)
    return jsonify(out)

@app.route("/check")
def check():
    threading.Thread(target=check_bot, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/webhook", methods=["POST"])
def webhook():
    threading.Thread(target=check_bot, daemon=True).start()
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
