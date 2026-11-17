import os, json, time, threading
import hmac, hashlib, urllib.parse
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

BYBIT_API_KEY = os.environ.get(
    "BYBIT_API_KEY", "").strip()
BYBIT_API_SECRET = os.environ.get(
    "BYBIT_API_SECRET", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID", "").strip()

SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
INTERVAL = "15"
ATR_PERIOD = 14
BASE_URL = "https://api.bybit.eu"
STATE_FILE = "state.json"

STATE = {
    "in_position": False,
    "entry_price": 0.0,
    "qty": 0.0,
    "last_check": 0,
    "last_start_msg": 0,
    "last_error_msg": 0
}
STATE_LOCK = threading.Lock()
_LOOP_STARTED = False

def log(msg):
    print(msg, flush=True)

log("KEY LOADED")

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN:
        return
    if not TELEGRAM_CHAT_ID:
        return
    try:
        url = "https://api.telegram.org/bot"
        url += TELEGRAM_BOT_TOKEN
        url += "/sendMessage"
        requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": msg
            },
            timeout=10
        )
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
    payload = timestamp
    payload += BYBIT_API_KEY
    payload += recv_window
    payload += params_str
    sign = hmac.new(
        BYBIT_API_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return sign, recv_window

def _headers(sign, recv_window, timestamp):
    return {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-SIGN": sign,
        "X-BAPI-SIGN-TYPE": "2",
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json"
    }

def bybit_get(path, params):
    timestamp = str(int(time.time() * 1000))
    query = urllib.parse.urlencode(
        sorted(params.items())
    )
    sign, recv_window = _sign(
        query, timestamp
    )
    headers = _headers(
        sign, recv_window, timestamp
    )
    url = BASE_URL + path + "?" + query
    try:
        r = requests.get(
            url, headers=headers, timeout=15
        )
        try:
            return r.json()
        except Exception:
            log("bybit_get geen JSON")
            return {
                "retCode": -1,
                "retMsg": "empty"
            }
    except Exception as e:
        log("bybit_get error: " + str(e))
        return {
            "retCode": -1,
            "retMsg": str(e)
        }

def bybit_post(path, body):
    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(
        body, separators=(",", ":")
    )
    sign, recv_window = _sign(
        body_str, timestamp
    )
    headers = _headers(
        sign, recv_window, timestamp
    )
    url = BASE_URL + path
    try:
        r = requests.post(
            url,
            headers=headers,
            data=body_str,
            timeout=15
        )
        try:
            return r.json()
        except Exception:
            log("bybit_post geen JSON")
            return {
                "retCode": -1,
                "retMsg": "empty"
            }
    except Exception as e:
        log("bybit_post error: " + str(e))
        return {
            "retCode": -1,
            "retMsg": str(e)
        }

def get_balances():
    total_usdt = 0.0
    total_pepe = 0.0
    seen_ok = False
    last_error = "ERROR"
    last_raw = {}
    for account_type in ["SPOT", "UNIFIED"]:
        try:
            data = bybit_get(
                "/v5/account/wallet-balance",
                {"accountType": account_type}
            )
            last_raw = data
            log("Saldo check " + account_type)
            log("retCode=" + str(data.get("retCode")))
            if data.get("retCode") == 10003:
                log("FOUT 10003: geen permissie")
                last_error = "10003"
                continue
            if data.get("retCode") != 0:
                log("Saldo error: " + str(data))
                last_error = "ERROR"
                continue
            lst = data.get("result", {}).get(
                "list", []
            )
            if not lst:
                continue
            seen_ok = True
            for c in lst[0].get("coin", []):
                if c.get("coin") == "USDT":
                    v = c.get("walletBalance", 0) or 0
                    total_usdt += float(v)
                if c.get("coin") == "PEPE":
                    v = c.get("walletBalance", 0) or 0
                    total_pepe += float(v)
        except Exception as e:
            log("Saldo exception: " + str(e))
            last_error = "ERROR"
            continue
    if not seen_ok:
        return None, None, last_error, last_raw
    return total_usdt, total_pepe, "OK", last_raw

def get_klines():
    try:
        r = requests.get(
            BASE_URL + "/v5/market/kline",
            params={
                "category": CATEGORY,
                "symbol": SYMBOL,
                "interval": INTERVAL,
                "limit": 100
            },
            timeout=15
        )
        lst = r.json().get(
            "result", {}
        ).get("list", [])
        return list(reversed(lst))
    except Exception as e:
        log("Klines error: " + str(e))
        return []

def get_price():
    try:
        r = requests.get(
            BASE_URL + "/v5/market/tickers",
            params={
                "category": CATEGORY,
                "symbol": SYMBOL
            },
            timeout=10
        )
        lst = r.json().get(
            "result", {}
        ).get("list", [])
        if lst:
            return float(lst[0].get(
                "lastPrice", 0
            ))
    except Exception as e:
        log("Price error: " + str(e))
    return 0

def get_qty_step():
    try:
        r = requests.get(
            BASE_URL + "/v5/market/instruments-info",
            params={
                "category": CATEGORY,
                "symbol": SYMBOL
            },
            timeout=10
        )
        lst = r.json().get(
            "result", {}
        ).get("list", [])
        if lst:
            f = lst[0].get(
                "lotSizeFilter", {}
            )
            return (
                float(f.get("qtyStep", 1)),
                float(f.get("minOrderQty", 1))
            )
    except Exception as e:
        log("Instrument error: " + str(e))
    return 1.0, 1.0

def calc_atr(klines):
    if len(klines) < ATR_PERIOD + 1:
        return 0
    trs = []
    for i in range(1, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        prev = float(klines[i-1][4])
        tr = max(
            high - low,
            abs(high - prev),
            abs(low - prev)
        )
        trs.append(tr)
    return sum(trs[-ATR_PERIOD:]) / ATR_PERIOD

def place_market_order(side, qty):
    try:
        qty_step, min_qty = get_qty_step()
        qty = int(qty / qty_step) * qty_step
        if qty < min_qty:
            log("Order te klein")
            return None
        body = {
            "category": CATEGORY,
            "symbol": SYMBOL,
            "side": side,
            "orderType": "Market",
            "qty": str(qty)
        }
        log("Plaats order: " + str(body))
        data = bybit_post(
            "/v5/order/create", body
        )
        log("Order result: " + str(data))
        if data.get("retCode") != 0:
            return None
        return data
    except Exception as e:
        log("Order error: " + str(e))
        return None

def check_bot():
    with STATE_LOCK:
        if time.time() - STATE["last_check"] < 60:
            return
        STATE["last_check"] = time.time()
    log("=== Bot check gestart ===")
    usdt, pepe_qty, acc_type, _ = get_balances()
    if usdt is None:
        if acc_type == "10003":
            with STATE_LOCK:
                if time.time() - STATE["last_error_msg"] > 600:
                    STATE["last_error_msg"] = time.time()
                    send_telegram(
                        "Pepebot: saldo 10003. "
                        "Fix: bybit.eu Read + Spot "
                        "Trade + Wallet AAN, IP UIT."
                    )
        log("Bot stopt: geen saldo")
        return
    log("Saldo USDT: " + str(usdt))
    log("Saldo PEPE: " + str(pepe_qty))
    klines = get_klines()
    if len(klines) < 5:
        log("Te weinig klines")
        return
    price = get_price()
    if price == 0:
        price = float(klines[-1][4])
    atr = calc_atr(klines)
    last = klines[-1]
    prev = klines[-2]
    last_open = float(last[1])
    last_close = float(last[4])
    prev_close = float(prev[4])
    log("Prijs: " + str(price))
    log("ATR: " + str(atr))
    with STATE_LOCK:
        in_pos = STATE["in_position"]
        if pepe_qty > 0 and not in_pos:
            STATE["in_position"] = True
            STATE["qty"] = pepe_qty
            in_pos = True
        if pepe_qty == 0 and in_pos:
            STATE["in_position"] = False
            STATE["qty"] = 0
            in_pos = False
    if not in_pos:
        if last_close > last_open:
            if last_close > prev_close:
                if usdt < 5:
                    log("Te weinig USDT")
                    return
                qty = usdt / price
                res = place_market_order(
                    "Buy", qty
                )
                if res:
                    with STATE_LOCK:
                        STATE["in_position"] = True
                        STATE["entry_price"] = price
                        STATE["qty"] = qty
                    save_state()
                    send_telegram(
                        "GEKOCHT PEPEUSDT "
                        "Prijs: " + str(price)
                    )
                    log("KOOP uitgevoerd")
                return
    if in_pos:
        if last_close < last_open:
            if last_close < prev_close:
                qty_to_sell = pepe_qty
                if qty_to_sell <= 0:
                    qty_to_sell = STATE["qty"]
                if qty_to_sell <= 0:
                    return
                res = place_market_order(
                    "Sell", qty_to_sell
                )
                if res:
                    with STATE_LOCK:
                        STATE["in_position"] = False
                        STATE["qty"] = 0
                        STATE["entry_price"] = 0
                    save_state()
                    send_telegram(
                        "VERKOCHT PEPEUSDT "
                        "Prijs: " + str(price)
                    )
                    log("VERKOOP uitgevoerd")
                return
    log("Geen signaal")
    with STATE_LOCK:
        if time.time() - STATE["last_start_msg"] > 3600:
            STATE["last_start_msg"] = time.time()
            need_start = True
        else:
            need_start = False
    if need_start:
        send_telegram(
            "Bot gestart: GEEN stop-loss "
            "Start 100 - Rente-op-rente AAN "
            "SPOT via bybit.eu "
            "Saldo: " + str(usdt) + " USDT"
        )
    save_state()
    log("=== Bot check klaar ===")

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
    threading.Thread(
        target=loop, daemon=True
    ).start()

@app.route("/")
def home():
    with STATE_LOCK:
        st = dict(STATE)
    key = BYBIT_API_KEY[:4] + "****"
    if not BYBIT_API_KEY:
        key = "LEEG"
    return jsonify({
        "status": "running",
        "symbol": SYMBOL,
        "in_position": st["in_position"],
        "key_start": key
    })

@app.route("/check")
def manual_check():
    threading.Thread(
        target=check_bot, daemon=True
    ).start()
    return jsonify({"ok": True})

@app.route("/test-key")
def test_key():
    usdt, pepe, acc, raw = get_balances()
    key = BYBIT_API_KEY[:4] + "****"
    if not BYBIT_API_KEY:
        key = "LEEG"
    return jsonify({
        "key_start": key,
        "usdt": usdt,
        "pepe": pepe,
        "status": acc,
        "bybit": raw
    })

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(
        force=True, silent=True
    ) or {}
    log("Webhook: " + str(data))
    threading.Thread(
        target=check_bot, daemon=True
    ).start()
    return jsonify({"ok": True})

start_loop_once()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
