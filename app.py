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
UTBOT_FACTOR = 4.0
UTBOT_ATR_PERIOD = 14
BASE_URL = "https://api.bybit.eu"
STATE_FILE = "state.json"

STATE = {"in_position": False, "entry_price": 0.0, "qty": 0.0, "last_check": 0, "last_start_msg": 0, "last_error_msg": 0, "trailing_stop": 0.0, "last_signal": ""}
STATE_LOCK = threading.Lock()
_LOOP_STARTED = False
_CHECK_RUNNING = False
LAST_BYBIT_ERROR = {}

def log(msg):
    print(msg, flush=True)

log(f"KEY LOADED: key_len={len(BYBIT_API_KEY)} secret_len={len(BYBIT_API_SECRET)} key_start={BYBIT_API_KEY[:4] if BYBIT_API_KEY else 'LEEG'}**** UTBOT {UTBOT_FACTOR}-{UTBOT_ATR_PERIOD}")

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
    global LAST_BYBIT_ERROR
    timestamp = str(int(time.time() * 1000))
    query = urllib.parse.urlencode(sorted(params.items()))
    sign, recv_window = _sign(query, timestamp)
    headers = _headers(sign, recv_window, timestamp)
    url = BASE_URL + path + "?" + query
    try:
        r = requests.get(url, headers=headers, timeout=15)
        try:
            data = r.json()
        except:
            data = {"retCode": -1, "retMsg": "no json", "http_status": r.status_code, "body": r.text[:300]}
        LAST_BYBIT_ERROR = data
        log(f"bybit_get {path} {params} -> retCode={data.get('retCode')} retMsg={data.get('retMsg')} http={r.status_code}")
        return data
    except Exception as e:
        log("bybit_get exception: " + str(e))
        data = {"retCode": -1, "retMsg": str(e)}
        LAST_BYBIT_ERROR = data
        return data

def bybit_post(path, body):
    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(body, separators=(",", ":"))
    sign, recv_window = _sign(body_str, timestamp)
    headers = _headers(sign, recv_window, timestamp)
    url = BASE_URL + path
    try:
        r = requests.post(url, headers=headers, data=body_str, timeout=15)
        try:
            data = r.json()
        except:
            data = {"retCode": -1, "retMsg": "no json", "http_status": r.status_code}
        log(f"bybit_post {path} -> retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
        return data
    except Exception as e:
        log("bybit_post exception: " + str(e))
        return {"retCode": -1, "retMsg": str(e)}

def get_balances():
    total_usdt = 0.0
    total_pepe = 0.0
    seen_ok = False
    last_error = "ERROR"
    last_raw = {}
    for account_type in ["SPOT", "UNIFIED", "FUND"]:
        data = bybit_get("/v5/account/wallet-balance", {"accountType": account_type})
        last_raw[account_type] = data
        try:
            rc = int(data.get("retCode", -1))
        except:
            rc = -1
        if rc == 10003:
            last_error = "10003"; continue
        if rc == 10004:
            last_error = "10004"; continue
        if rc == 10001:
            last_error = "10001"; continue
        if rc != 0:
            last_error = f"ERROR_{rc}"; continue
        lst = data.get("result", {}).get("list", [])
        if not lst:
            continue
        seen_ok = True
        for c in lst[0].get("coin", []):
            if c.get("coin") == "USDT":
                total_usdt += float(c.get("walletBalance", 0) or 0)
            if c.get("coin") == "PEPE":
                total_pepe += float(c.get("walletBalance", 0) or 0)
    if not seen_ok:
        return None, None, last_error, last_raw
    return total_usdt, total_pepe, "SPOT+UNIFIED+FUND", last_raw

def get_klines():
    try:
        r = requests.get(BASE_URL + "/v5/market/kline", params={"category": CATEGORY, "symbol": SYMBOL, "interval": INTERVAL, "limit": 200}, timeout=15)
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

def calc_utbot(klines, atr_period=UTBOT_ATR_PERIOD, factor=UTBOT_FACTOR):
    if len(klines) < atr_period + 5:
        return "NONE", 0, 0
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    trs = []
    for i in range(1, len(klines)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    atr = sum(trs[:atr_period]) / atr_period
    atr_list = [atr]
    for i in range(atr_period, len(trs)):
        atr = (atr * (atr_period - 1) + trs[i]) / atr_period
        atr_list.append(atr)
    trailings = []
    trailing = 0.0
    for idx in range(atr_period, len(closes)):
        a = atr_list[idx - atr_period]
        x_atr = a * factor
        close = closes[idx]
        if idx == atr_period:
            prev_c = closes[idx-1]
            trailing = close - x_atr if close > prev_c else close + x_atr
        else:
            if close > trailing:
                trailing = max(trailing, close - x_atr)
            else:
                trailing = min(trailing, close + x_atr)
        trailings.append(trailing)
    last_close = closes[-1]
    prev_close = closes[-2]
    last_trail = trailings[-1]
    prev_trail = trailings[-2]
    if last_close > last_trail and prev_close <= prev_trail:
        signal = "BUY"
    elif last_close < last_trail and prev_close >= prev_trail:
        signal = "SELL"
    else:
        signal = "NONE"
    return signal, last_trail, atr_list[-1]

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
        if int(data.get("retCode", -1)) != 0:
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
        log("=== Bot check gestart UTBOT 4-14 ===")
        usdt, pepe_qty, acc_type, raw = get_balances()
        if usdt is None:
            with STATE_LOCK:
                STATE["last_check"] = 0
            log(f"Bot stopt: geen saldo, fout={acc_type} raw={raw}")
            with STATE_LOCK:
                if time.time() - STATE["last_error_msg"] > 600:
                    STATE["last_error_msg"] = time.time()
                    send_telegram(f"Pepebot: Kan saldo niet lezen ({acc_type}). Check key op bybit.eu, secret, IP UIT.")
            return
        log(f"Saldo USDT {acc_type}: {usdt} | PEPE: {pepe_qty}")
        klines = get_klines()
        if len(klines) < 30:
            log("Te weinig klines, skip")
            return
        price = get_price()
        if price == 0:
            price = float(klines[-1][4])
        signal, trailing, atr = calc_utbot(klines)
        log(f"Prijs: {price} | UTBOT signaal: {signal} | Trailing: {trailing} | ATR: {atr}")
        with STATE_LOCK:
            in_pos = STATE["in_position"]
            STATE["trailing_stop"] = trailing
            if pepe_qty > 0 and not in_pos:
                STATE["in_position"] = True; STATE["qty"] = pepe_qty; in_pos = True
            if pepe_qty == 0 and in_pos:
                STATE["in_position"] = False; STATE["qty"] = 0; in_pos = False
        if signal == "BUY" and not in_pos:
            if usdt < 5:
                log("Te weinig USDT"); return
            qty = usdt / price
            res = place_market_order("Buy", qty)
            if res:
                with STATE_LOCK:
                    STATE["in_position"] = True; STATE["entry_price"] = price; STATE["qty"] = qty; STATE["last_signal"] = "BUY"
                save_state()
                send_telegram(f"UTBOT BUY PEPEUSDT Prijs: {price} Inleg: {usdt} USDT Trailing: {trailing} Rente-op-rente AAN")
                log("KOOP uitgevoerd UTBOT BUY")
            return
        if signal == "SELL" and in_pos:
            qty_to_sell = pepe_qty if pepe_qty > 0 else 0
            if qty_to_sell <= 0:
                log("Niks te verkopen, saldo PEPE is 0")
                return
            res = place_market_order("Sell", qty_to_sell)
            if res:
                with STATE_LOCK:
                    STATE["in_position"] = False; STATE["qty"] = 0; STATE["entry_price"] = 0; STATE["last_signal"] = "SELL"
                save_state()
                send_telegram(f"UTBOT SELL PEPEUSDT Prijs: {price} Saldo: {usdt} USDT Trailing: {trailing}")
                log("VERKOOP uitgevoerd UTBOT SELL")
            return
        with STATE_LOCK:
            if signal in ["BUY", "SELL"]:
                STATE["last_signal"] = signal
        log(f"Geen trade. Signaal: {signal} In positie: {in_pos}")
        with STATE_LOCK:
            if time.time() - STATE["last_start_msg"] > 3600:
                STATE["last_start_msg"] = time.time(); need_start = True
            else:
                need_start = False
        if need_start:
            send_telegram(f"Bot gestart: UTBOT 4-14 - GEEN stop-loss - Rente-op-rente AAN - SPOT via bybit.eu Saldo: {usdt} USDT Signaal: {signal}")
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
    return jsonify({"status": "running", "symbol": SYMBOL, "strategy": "UTBOT 4-14", "in_position": st["in_position"], "last_signal": st.get("last_signal", ""), "trailing_stop": st.get("trailing_stop", 0), "key_start": BYBIT_API_KEY[:4] + "****" if BYBIT_API_KEY else "LEEG"})

@app.route("/check")
def manual_check():
    threading.Thread(target=check_bot, daemon=True).start()
    return jsonify({"ok": True})

@app.route("/test-key")
def test_key():
    usdt, pepe, acc, raw = get_balances()
    klines = get_klines()
    signal, trailing, atr = calc_utbot(klines) if len(klines) > 30 else ("NONE", 0, 0)
    return jsonify({"key_start": BYBIT_API_KEY[:4] + "****" if BYBIT_API_KEY else "LEEG", "key_len": len(BYBIT_API_KEY), "secret_len": len(BYBIT_API_SECRET), "usdt": usdt, "pepe": pepe, "status": acc, "strategy": "UTBOT 4-14", "signal": signal, "trailing": trailing, "atr": atr, "bybit_raw": raw})

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
