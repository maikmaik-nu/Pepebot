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
UT_FACTOR = 4.0
BASE_URL = "https://api.bybit.eu"
STATE_FILE = "state.json"

STATE = {"in_position": False, "entry_price": 0.0, "qty": 0.0, "last_check": 0, "last_start_msg": 0, "last_error_msg": 0, "trailing": 0.0, "prev_close": 0.0}
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

# --- UIT VORIGE: Bybit verbinding die het deed ---
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
    # OPLOSSING UIT VORIGE: alleen accountType, GEEN coin param
    for account_type in ["SPOT", "UNIFIED"]:
        try:
            data = bybit_get("/v5/account/wallet-balance", {"accountType": account_type})
            log(f"Saldo check {account_type}: retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
            if data.get("retCode") == 10003:
                log(f"FOUT 10003 in {account_type}: Key geen toestemming. bybit.eu API Read + Spot Trade + Wallet AAN, IP UIT")
                last_error = "10003"
                continue
            if data.get("retCode") != 0:
                log(f"Saldo error {account_type}: {data}")
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
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs[-ATR_PERIOD:]) / ATR_PERIOD

def round_qty(qty, qty_step):
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
                return
            STATE["last_check"] = time.time()
        log("=== Bot check gestart ===")
        usdt, pepe_qty, acc_type = get_balances()
        if usdt is None:
            with STATE_LOCK:
                STATE["last_check"] = 0
            if acc_type == "10003":
                with STATE_LOCK:
                    if time.time() - STATE["last_error_msg"] > 600:
                        STATE["last_error_msg"] = time.time()
                        send_telegram("Pepebot: Kan saldo niet lezen (10003). Fix: bybit.eu API Read + Spot Trade + Wallet AAN, IP UIT.")
            log("Bot stopt: geen saldo")
            return
        log(f"Saldo USDT {acc_type}: {usdt} | PEPE: {pepe_qty}")
        klines = get_klines()
        if len(klines) < ATR_PERIOD + 5:
            log("Te weinig klines, skip")
            return
        price = get_price()
        if price == 0:
            price = float(klines[-1][4])
        atr = calc_atr(klines)
        last = klines[-1]; prev = klines[-2]
        last_close = float(last[4]); prev_close = float(prev[4])
        log(f"Prijs: {price} | ATR: {atr} | Last close: {last_close} | Prev close: {prev_close}")
        with STATE_LOCK:
            in_pos = STATE["in_position"]
            prev_trailing = STATE.get("trailing", 0.0)
            stored_prev_close = STATE.get("prev_close", 0.0)
            if pepe_qty > 0 and not in_pos:
                STATE["in_position"] = True; STATE["qty"] = pepe_qty; in_pos = True
            if pepe_qty == 0 and in_pos:
                STATE["in_position"] = False; STATE["qty"] = 0; in_pos = False
        # --- UT BOT 4 / 14 ---
        if prev_trailing == 0.0:
            prev_trailing = last_close - UT_FACTOR * atr if last_close > prev_close else last_close + UT_FACTOR * atr
            stored_prev_close = prev_close
        if last_close > prev_trailing:
            trailing = max(prev_trailing, last_close - UT_FACTOR * atr)
        else:
            trailing = min(prev_trailing, last_close + UT_FACTOR * atr)
        buy_signal = (not in_pos) and (stored_prev_close <= prev_trailing) and (last_close > trailing)
        sell_signal = (in_pos) and (stored_prev_close >= prev_trailing) and (last_close < trailing)
        log(f"UT Bot: prev_trailing={prev_trailing} trailing={trailing} buy={buy_signal} sell={sell_signal} in_pos={in_pos}")
        with STATE_LOCK:
            STATE["trailing"] = trailing
            STATE["prev_close"] = last_close
        save_state()
        if buy_signal:
            if usdt < 5:
                log("Te weinig USDT"); return
            qty = (usdt * 0.999) / price
            res = place_market_order("Buy", qty)
            if res:
                with STATE_LOCK:
                    STATE["in_position"] = True; STATE["entry_price"] = price; STATE["qty"] = qty
                save_state()
                send_telegram(f"GEKOCHT PEPEUSDT - UT Bot BUY - Prijs: {price} Inleg: {usdt} USDT")
                log("KOOP uitgevoerd - UT Bot")
            return
        if sell_signal:
            qty_to_sell = pepe_qty if pepe_qty > 0 else 0
            if qty_to_sell <= 0:
                log("Niks te verkopen, saldo PEPE is 0")
                return
            res = place_market_order("Sell", qty_to_sell)
            if res:
                with STATE_LOCK:
                    STATE["in_position"] = False; STATE["qty"] = 0; STATE["entry_price"] = 0
                save_state()
                send_telegram(f"VERKOCHT PEPEUSDT - UT Bot SELL - Prijs: {price} Saldo: {usdt} USDT")
                log("VERKOOP uitgevoerd - UT Bot")
            return
        log(f"Geen UT Bot signaal. In positie: {in_pos}")
        with STATE_LOCK:
            if time.time() - STATE["last_start_msg"] > 3600:
                STATE["last_start_msg"] = time.time(); need_start = True
            else:
                need_start = False
        if need_start:
            send_telegram(f"Bot gestart: UT Bot 4 / 14 - SPOT via bybit.eu Saldo: {usdt} USDT Trailing: {trailing}")
        save_state()
        log("=== Bot check klaar ===")
    finally:
        _CHECK_RUNNING = False

def loop():
    check_bot()
    while True:
        time.sleep(900)
        try:
            check_bot()
        except Exception as e:
            log("Loop error: " + str(e))

def start_loop_once():
    global _LOOP_STARTED
    if _LOOP_STARTED:
        return
    _LOOP_STARTED = True
    load_state()
    threading.Thread(target=loop, daemon=True).start()

@app.route("/")
def home():
    with STATE_LOCK:
        st = dict(STATE)
    return jsonify({"status": "running", "symbol": SYMBOL, "in_position": st["in_position"], "trailing": st.get("trailing", 0), "key_start": BYBIT_API_KEY[:4] + "****" if BYBIT_API_KEY else "LEEG"})

@app.route("/ping")
def ping():
    return jsonify({"ok": True})

@app.route("/check")
def manual_check():
    threading.Thread(target=check_bot, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/test-key")
def test_key():
    usdt, pepe, acc = get_balances()
    return jsonify({"key_start": BYBIT_API_KEY[:4] + "****" if BYBIT_API_KEY else "LEEG", "usdt": usdt, "pepe": pepe, "status": acc})

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    log("Webhook: " + str(data))
    threading.Thread(target=check_bot, daemon=True).start()
    return jsonify({"ok": True})

start_loop_once()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
