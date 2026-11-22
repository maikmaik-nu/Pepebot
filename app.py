import os
import time
import hmac
import hashlib
import json
import threading
import logging
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request

# ================= LOGGING =================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("pepebot")

# ================= CONFIG =================
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()

BASE_URL = "https://api.bybit.eu"
CATEGORY = "spot"
SYMBOL = "PEPEUSDT"
COIN_USDT = "USDT"
COIN_PEPE = "PEPE"

# Trading config - pas aan in Render Environment als je wil
TRADE_USDT = float(os.getenv("TRADE_USDT", "20"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "3.0"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "2.0"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
MIN_USDT_TO_TRADE = float(os.getenv("MIN_USDT_TO_TRADE", "10"))
MAX_OPEN_ORDERS = int(os.getenv("MAX_OPEN_ORDERS", "1"))

# State in memory
STATE = {
    "started_at": datetime.now(timezone.utc).isoformat(),
    "last_check": None,
    "last_price": 0.0,
    "last_usdt": 0.0,
    "last_pepe": 0.0,
    "in_position": False,
    "entry_price": 0.0,
    "entry_qty": 0.0,
    "entry_time": None,
    "last_action": "idle",
    "last_error": None,
    "total_trades": 0,
    "total_profit_usdt": 0.0,
    "bot_running": True,
    "last_retcode": None,
}

app = Flask(__name__)

# ================= 【entity-BYBIT¦canonical_name=Bybit】 HELPERS =================
def _timestamp_ms():
    return str(int(time.time() * 1000))

def _sign(secret, payload):
    return hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

def bybit_get(path, params=None):
    if params is None:
        params = {}
    ts = _timestamp_ms()
    params["api_key"] = BYBIT_API_KEY
    params["timestamp"] = ts
    params["recv_window"] = "5000"
    # 【entity-Bybit¦canonical_name=Bybit】 V5 signature: timestamp + api_key + recv_window + query string
    query = "&".join([f"{k}={params[k]}" for k in sorted(params.keys())])
    sign_payload = ts + BYBIT_API_KEY + "5000" + query
    sign = _sign(BYBIT_API_SECRET, sign_payload)
    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-SIGN": sign,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000",
        "Content-Type": "application/json",
    }
    try:
        r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=15)
        data = r.json()
        return r.status_code, data
    except Exception as e:
        log.error(f"bybit_get error {path}: {e}")
        return 0, {"retCode": -1, "retMsg": str(e), "result": {}}

def bybit_post(path, body=None):
    if body is None:
        body = {}
    ts = _timestamp_ms()
    body_str = json.dumps(body, separators=(",", ":"))
    sign_payload = ts + BYBIT_API_KEY + "5000" + body_str
    sign = _sign(BYBIT_API_SECRET, sign_payload)
    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-SIGN": sign,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(BASE_URL + path, data=body_str, headers=headers, timeout=15)
        data = r.json()
        return r.status_code, data
    except Exception as e:
        log.error(f"bybit_post error {path}: {e}")
        return 0, {"retCode": -1, "retMsg": str(e), "result": {}}

# ================= BALANCE =================
def get_wallet_balance(account_type):
    status, data = bybit_get("/v5/account/wallet-balance", {
        "accountType": account_type,
    })
    return status, data

def parse_balances(data):
    usdt = 0.0
    pepe = 0.0
    try:
        lst = data.get("result", {}).get("list", [])
        if not lst:
            return usdt, pepe
        coins = lst[0].get("coin", [])
        for c in coins:
            coin = c.get("coin", "")
            bal = c.get("walletBalance", "0")
            try:
                bal_f = float(bal)
            except:
                bal_f = 0.0
            if coin == COIN_USDT:
                usdt = bal_f
            if coin == COIN_PEPE:
                pepe = bal_f
    except Exception as e:
        log.error(f"parse_balances error: {e}")
    return usdt, pepe

def get_balance():
    # Eerst SPOT, want daar staat je geld
    status, data = get_wallet_balance("SPOT")
    retcode = data.get("retCode", -1)
    STATE["last_retcode"] = retcode
    if retcode == 0:
        usdt, pepe = parse_balances(data)
        return usdt, pepe, retcode, "SPOT"
    log.warning(f"BALANCE FAIL SPOT: retCode={retcode} msg={data.get('retMsg')}")
    # Fallback UNIFIED
    status2, data2 = get_wallet_balance("UNIFIED")
    retcode2 = data2.get("retCode", -1)
    STATE["last_retcode"] = retcode2
    if retcode2 == 0:
        usdt, pepe = parse_balances(data2)
        return usdt, pepe, retcode2, "UNIFIED"
    log.warning(f"BALANCE FAIL UNIFIED: retCode={retcode2} msg={data2.get('retMsg')}")
    return 0.0, 0.0, retcode2, "NONE"

# ================= PRICE =================
def get_price():
    status, data = bybit_get("/v5/market/tickers", {
        "category": CATEGORY,
        "symbol": SYMBOL,
    })
    try:
        if data.get("retCode") == 0:
            lst = data.get("result", {}).get("list", [])
            if lst:
                return float(lst[0].get("lastPrice", "0"))
    except Exception as e:
        log.error(f"get_price error: {e}")
    return 0.0

def get_symbol_info():
    status, data = bybit_get("/v5/market/instruments-info", {
        "category": CATEGORY,
        "symbol": SYMBOL,
    })
    info = {"qty_step": 1000.0, "min_qty": 1000.0, "price_tick": 0.0000001}
    try:
        if data.get("retCode") == 0:
            lst = data.get("result", {}).get("list", [])
            if lst:
                lot = lst[0].get("lotSizeFilter", {})
                info["qty_step"] = float(lot.get("qtyStep", "1000"))
                info["min_qty"] = float(lot.get("minOrderQty", "1000"))
                price_filter = lst[0].get("priceFilter", {})
                info["price_tick"] = float(price_filter.get("tickSize", "0.0000001"))
    except Exception as e:
        log.error(f"get_symbol_info error: {e}")
    return info

def round_qty(qty, step):
    if step <= 0:
        return qty
    return int(qty / step) * step

# ================= ORDERS =================
def place_market_buy(usdt_amount, price):
    info = get_symbol_info()
    qty = usdt_amount / price if price > 0 else 0
    qty = round_qty(qty, info["qty_step"])
    if qty < info["min_qty"]:
        log.warning(f"Buy qty te klein: {qty} < {info['min_qty']}")
        return False, {"retMsg": "qty too small"}
    body = {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": "Buy",
        "orderType": "Market",
        "qty": str(int(qty)) if qty == int(qty) else str(qty),
        "timeInForce": "GTC",
    }
    status, data = bybit_post("/v5/order/create", body)
    log.info(f"BUY order: {data}")
    if data.get("retCode") == 0:
        return True, data
    return False, data

def place_market_sell(qty):
    info = get_symbol_info()
    qty = round_qty(qty, info["qty_step"])
    if qty < info["min_qty"]:
        log.warning(f"Sell qty te klein: {qty} < {info['min_qty']}")
        return False, {"retMsg": "qty too small"}
    body = {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": "Sell",
        "orderType": "Market",
        "qty": str(int(qty)) if qty == int(qty) else str(qty),
        "timeInForce": "GTC",
    }
    status, data = bybit_post("/v5/order/create", body)
    log.info(f"SELL order: {data}")
    if data.get("retCode") == 0:
        return True, data
    return False, data

def get_open_orders():
    status, data = bybit_get("/v5/order/realtime", {
        "category": CATEGORY,
        "symbol": SYMBOL,
    })
    if data.get("retCode") == 0:
        return data.get("result", {}).get("list", [])
    return []

# ================= STRATEGY =================
def check_once():
    if not STATE["bot_running"]:
        return
    now = datetime.now(timezone.utc).isoformat()
    STATE["last_check"] = now
    try:
        usdt, pepe, retcode, acc = get_balance()
        price = get_price()
        STATE["last_usdt"] = usdt
        STATE["last_pepe"] = pepe
        STATE["last_price"] = price
        STATE["last_error"] = None

        log.info(f"CHECK: USDT={usdt} PEPE={pepe} price={price} in_position={STATE['in_position']} retCode={retcode} acc={acc}")

        if retcode != 0:
            STATE["last_action"] = f"geen saldo / geen rechten retCode={retcode}"
            STATE["last_error"] = f"retCode {retcode} - check Bybit permissions"
            log.warning(f"Bot stopt: geen saldo / geen rechten retCode={retcode}")
            return

        if price <= 0:
            STATE["last_action"] = "geen price"
            return

        # In positie? Check TP / SL
        if STATE["in_position"] and STATE["entry_price"] > 0:
            change_pct = ((price - STATE["entry_price"]) / STATE["entry_price"]) * 100.0
            log.info(f"POSITION: entry={STATE['entry_price']} now={price} change={change_pct:.2f}%")
            if change_pct >= TAKE_PROFIT_PCT:
                ok, res = place_market_sell(STATE["entry_qty"])
                if ok:
                    profit = (price - STATE["entry_price"]) * STATE["entry_qty"]
                    STATE["total_profit_usdt"] += profit
                    STATE["total_trades"] += 1
                    STATE["in_position"] = False
                    STATE["entry_price"] = 0.0
                    STATE["entry_qty"] = 0.0
                    STATE["entry_time"] = None
                    STATE["last_action"] = f"SELL TP +{change_pct:.2f}% profit={profit:.2f}"
                else:
                    STATE["last_action"] = f"SELL TP mislukt: {res.get('retMsg')}"
                return
            if change_pct <= -STOP_LOSS_PCT:
                ok, res = place_market_sell(STATE["entry_qty"])
                if ok:
                    profit = (price - STATE["entry_price"]) * STATE["entry_qty"]
                    STATE["total_profit_usdt"] += profit
                    STATE["total_trades"] += 1
                    STATE["in_position"] = False
                    STATE["entry_price"] = 0.0
                    STATE["entry_qty"] = 0.0
                    STATE["entry_time"] = None
                    STATE["last_action"] = f"SELL SL {change_pct:.2f}% profit={profit:.2f}"
                else:
                    STATE["last_action"] = f"SELL SL mislukt: {res.get('retMsg')}"
                return
            STATE["last_action"] = f"hold {change_pct:.2f}%"
            return

        # Niet in positie? Kijk of we PEPE al hebben uit oude trade
        if pepe > 0 and price > 0:
            pepe_value = pepe * price
            if pepe_value >= MIN_USDT_TO_TRADE:
                STATE["in_position"] = True
                STATE["entry_price"] = price
                STATE["entry_qty"] = pepe
                STATE["entry_time"] = now
                STATE["last_action"] = f"positie hersteld: {pepe} PEPE waarde {pepe_value:.2f} USDT"
                return

        # Nieuw kopen?
        if usdt < MIN_USDT_TO_TRADE:
            STATE["last_action"] = f"te weinig USDT ({usdt:.2f})"
            return

        open_orders = get_open_orders()
        if len(open_orders) >= MAX_OPEN_ORDERS:
            STATE["last_action"] = f"open orders: {len(open_orders)}"
            return

        amount = min(TRADE_USDT, usdt)
        ok, res = place_market_buy(amount, price)
        if ok:
            qty = amount / price
            STATE["in_position"] = True
            STATE["entry_price"] = price
            STATE["entry_qty"] = qty
            STATE["entry_time"] = now
            STATE["total_trades"] += 1
            STATE["last_action"] = f"BUY {qty:.0f} PEPE voor {amount} USDT @ {price}"
            log.info(f"BUY gelukt: {STATE['last_action']}")
        else:
            STATE["last_action"] = f"BUY mislukt: {res.get('retMsg')}"
            log.warning(f"BUY mislukt: {res}")

    except Exception as e:
        STATE["last_error"] = str(e)
        STATE["last_action"] = f"error: {e}"
        log.error(f"check_once error: {e}", exc_info=True)

def bot_loop():
    log.info("Bot loop gestart")
    while True:
        try:
            check_once()
        except Exception as e:
            log.error(f"bot_loop error: {e}")
        time.sleep(CHECK_INTERVAL)

# ================= FLASK ROUTES =================
@app.route("/")
def home():
    usdt, pepe, retcode, acc = get_balance()
    price = get_price()
    return jsonify({
        "bot": "Pepebot-3",
        "status": "live",
        "symbol": SYMBOL,
        "category": CATEGORY,
        "price": price,
        "usdt": usdt,
        "pepe": pepe,
        "in_position": STATE["in_position"],
        "entry_price": STATE["entry_price"],
        "entry_qty": STATE["entry_qty"],
        "last_action": STATE["last_action"],
        "last_check": STATE["last_check"],
        "last_retcode": retcode,
        "account": acc,
        "api_key_set": bool(BYBIT_API_KEY),
        "total_trades": STATE["total_trades"],
        "total_profit_usdt": STATE["total_profit_usdt"],
        "bot_running": STATE["bot_running"],
    })

@app.route("/test-key")
def test_key():
    usdt, pepe, retcode, acc = get_balance()
    price = get_price()
    return jsonify({
        "api_key_set": bool(BYBIT_API_KEY),
        "api_secret_set": bool(BYBIT_API_SECRET),
        "base_url": BASE_URL,
        "category": CATEGORY,
        "symbol": SYMBOL,
        "balances": {"USDT": usdt, "PEPE": pepe},
        "price": price,
        "retCode": retcode,
        "account": acc,
        "ok": retcode == 0,
    })

@app.route("/balance")
def balance():
    usdt, pepe, retcode, acc = get_balance()
    return jsonify({
        "USDT": usdt,
        "PEPE": pepe,
        "retCode": retcode,
        "account": acc,
        "price": get_price(),
    })

@app.route("/price")
def price_route():
    return jsonify({"symbol": SYMBOL, "price": get_price()})

@app.route("/status")
def status_route():
    return jsonify(STATE)

@app.route("/check", methods=["GET", "POST"])
def manual_check():
    check_once()
    return jsonify(STATE)

@app.route("/start", methods=["POST"])
def start_bot():
    STATE["bot_running"] = True
    return jsonify({"bot_running": True})

@app.route("/stop", methods=["POST"])
def stop_bot():
    STATE["bot_running"] = False
    return jsonify({"bot_running": False})

@app.route("/sell-all", methods=["POST"])
def sell_all():
    usdt, pepe, retcode, acc = get_balance()
    if pepe <= 0:
        return jsonify({"ok": False, "msg": "geen PEPE om te verkopen"})
    ok, res = place_market_sell(pepe)
    if ok:
        STATE["in_position"] = False
        STATE["entry_price"] = 0.0
        STATE["entry_qty"] = 0.0
        return jsonify({"ok": True, "sold": pepe, "result": res})
    return jsonify({"ok": False, "result": res})

@app.errorhandler(500)
def handle_500(e):
    log.error(f"500 error: {e}")
    return jsonify({"error": "internal error", "detail": str(e), "state": STATE}), 500

@app.errorhandler(404)
def handle_404(e):
    return jsonify({"error": "not found", "path": request.path}), 404

# ================= START =================
_bot_thread_started = False

def start_background():
    global _bot_thread_started
    if _bot_thread_started:
        return
    _bot_thread_started = True
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()
    log.info("Background thread gestart")

# Start meteen bij import, zodat Gunicorn hem ook start
start_background()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
