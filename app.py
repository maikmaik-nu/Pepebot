import os
import time
import hmac
import hashlib
import json
import requests
import threading
from flask import Flask, jsonify

app = Flask(__name__)

# ================= CONFIG =================
API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
BASE_URL = os.getenv("BASE_URL", "https://api.bybit.eu").strip().rstrip("/")
CATEGORY = os.getenv("CATEGORY", "spot").strip()
SYMBOL = os.getenv("SYMBOL", "PEPEUSDT").strip()
TRADE_USDT = float(os.getenv("TRADE_USDT", "10"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "10"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "5"))

# ================= STATE =================
in_position = False
buy_price = 0.0
last_retcode = None
last_retmsg = ""
last_usdt = 0.0
last_pepe = 0.0
last_price = 0.0
last_error = ""
last_check = ""
total_trades = 0

def log(msg):
    print(f"INFO:pepebot:{msg}", flush=True)

def telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        log(f"telegram error {e}")

# ================= 【entity-BYBIT¦canonical_name=Bybit】 SIGN =================
def sign_get(params):
    ts = str(int(time.time() * 1000))
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    to_sign = ts + API_KEY + "5000" + query
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    return ts, sig

def api_get(path, params):
    global last_retcode, last_retmsg
    ts, sig = sign_get(params)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000",
        "X-BAPI-SIGN": sig,
        "Content-Type": "application/json"
    }
    try:
        r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=15)
        log(f"api {path} -> {r.status_code} {r.text[:600]}")
        try:
            j = r.json()
        except:
            return {}
        last_retcode = j.get("retCode")
        last_retmsg = j.get("retMsg", "")
        return j
    except Exception as e:
        log(f"api error {path} {e}")
        return {"retCode": -1, "retMsg": str(e)}

def api_post(path, body):
    global last_retcode, last_retmsg
    ts = str(int(time.time() * 1000))
    body_str = json.dumps(body, separators=(",", ":"))
    to_sign = ts + API_KEY + "5000" + body_str
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000",
        "X-BAPI-SIGN": sig,
        "Content-Type": "application/json"
    }
    try:
        r = requests.post(BASE_URL + path, data=body_str, headers=headers, timeout=15)
        log(f"api POST {path} -> {r.status_code} {r.text[:600]}")
        try:
            j = r.json()
        except:
            return {}
        last_retcode = j.get("retCode")
        last_retmsg = j.get("retMsg", "")
        return j
    except Exception as e:
        log(f"api post error {e}")
        return {"retCode": -1, "retMsg": str(e)}

# ================= BALANCE / PRICE =================
def get_balances():
    global last_usdt, last_pepe
    d = api_get("/v5/account/wallet-balance", {"accountType": "SPOT"})
    usdt = 0.0
    pepe = 0.0
    acc = "NONE"
    try:
        lst = d.get("result", {}).get("list", [])
        if lst:
            acc = lst[0].get("accountType", "NONE")
            for c in lst[0].get("coin", []):
                if c["coin"] == "USDT":
                    usdt = float(c.get("walletBalance", 0))
                if c["coin"] == "PEPE":
                    pepe = float(c.get("walletBalance", 0))
    except Exception as e:
        log(f"balance parse error {e}")
    last_usdt = usdt
    last_pepe = pepe
    return usdt, pepe, acc

def get_price():
    global last_price
    try:
        r = requests.get(
            BASE_URL + "/v5/market/tickers",
            params={"category": CATEGORY, "symbol": SYMBOL},
            timeout=10
        ).json()
        price = float(r["result"]["list"][0]["lastPrice"])
        last_price = price
        return price
    except Exception as e:
        log(f"price error {e}")
        return 0.0

def get_open_orders():
    d = api_get("/v5/order/realtime", {"category": CATEGORY, "symbol": SYMBOL, "limit": 5})
    try:
        return d.get("result", {}).get("list", [])
    except:
        return []

# ================= TRADING =================
def buy_pepe(usdt_amount, price):
    global in_position, buy_price, total_trades
    if price <= 0:
        log("buy skipped: price 0")
        return False
    qty = int(usdt_amount / price)
    if qty <= 0:
        log(f"buy skipped qty 0 usdt={usdt_amount} price={price}")
        return False
    log(f"BUY {qty} {SYMBOL} voor {usdt_amount} USDT @ {price}")
    res = api_post("/v5/order/create", {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": "Buy",
        "orderType": "Market",
        "qty": str(qty)
    })
    if res.get("retCode") == 0:
        in_position = True
        buy_price = price
        total_trades += 1
        telegram(f"✅ GEKOCHT: {qty} PEPE @ {price} ({usdt_amount} USDT)")
        log(f"BUY OK orderId={res.get('result', {}).get('orderId')}")
        return True
    else:
        log(f"BUY FAIL {res}")
        telegram(f"❌ BUY mislukt: {res.get('retMsg')}")
        return False

def sell_pepe(pepe_amount, price, reden=""):
    global in_position
    qty = int(pepe_amount)
    if qty <= 0:
        return False
    log(f"SELL {qty} {SYMBOL} @ {price} reden={reden}")
    res = api_post("/v5/order/create", {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": "Sell",
        "orderType": "Market",
        "qty": str(qty)
    })
    if res.get("retCode") == 0:
        in_position = False
        telegram(f"💰 VERKOCHT: {qty} PEPE @ {price} - {reden}")
        log("SELL OK")
        return True
    else:
        log(f"SELL FAIL {res}")
        telegram(f"❌ SELL mislukt: {res.get('retMsg')}")
        return False

# ================= MAIN LOOP =================
def check_loop():
    global in_position, buy_price, last_error, last_check
    log(f"bot gestart BASE={BASE_URL} SYMBOL={SYMBOL} CATEGORY={CATEGORY} TRADE={TRADE_USDT} TP={TAKE_PROFIT_PCT}% SL={STOP_LOSS_PCT}%")
    # Bij start: kijk of we al PEPE hebben
    try:
        usdt, pepe, acc = get_balances()
        if pepe > 0:
            in_position = True
            buy_price = get_price()
            log(f"start: al in positie PEPE={pepe} buy_price={buy_price}")
    except Exception as e:
        last_error = str(e)

    while True:
        try:
            usdt, pepe, acc = get_balances()
            price = get_price()

            if pepe > 0:
                in_position = True
            elif pepe == 0:
                in_position = False

            last_check = f"USDT={usdt} PEPE={pepe} price={price} in_position={in_position} retCode={last_retcode} acc={acc}"
            log(f"CHECK: {last_check}")

            # Als 【entity-Bybit¦canonical_name=Bybit】 10003 geeft, niet blijven proberen te kopen
            if last_retcode == 10003:
                log("SKIP trading: retCode 10003 = key niet geautoriseerd, check FINAL key in Render")
                time.sleep(CHECK_INTERVAL)
                continue

            # Auto-verkoop: take profit / stop loss
            if in_position and buy_price > 0 and price > 0 and pepe > 0:
                winst = (price - buy_price) / buy_price * 100
                log(f"positie winst={winst:.2f}% buy={buy_price} now={price}")
                if winst >= TAKE_PROFIT_PCT:
                    sell_pepe(pepe, price, f"Take Profit +{winst:.1f}%")
                elif winst <= -STOP_LOSS_PCT:
                    sell_pepe(pepe, price, f"Stop Loss {winst:.1f}%")

        except Exception as e:
            last_error = str(e)
            log(f"CHECK error {e}")
        time.sleep(CHECK_INTERVAL)

# ================= ROUTES =================
@app.route("/")
def home():
    return "Pepebot-3 live - /test-key /status /buy /sell"

@app.route("/test-key")
def test_key():
    usdt, pepe, acc = get_balances()
    price = get_price()
    return jsonify({
        "base_url": BASE_URL,
        "symbol": SYMBOL,
        "category": CATEGORY,
        "usdt": usdt,
        "pepe": pepe,
        "price": price,
        "in_position": in_position,
        "buy_price": buy_price,
        "retCode": last_retcode,
        "retMsg": last_retmsg,
        "acc": acc,
        "key_ok": bool(API_KEY and API_SECRET),
        "key_len": len(API_KEY),
        "secret_len": len(API_SECRET),
    })

@app.route("/status")
def status():
    return jsonify({
        "usdt": last_usdt,
        "pepe": last_pepe,
        "price": last_price,
        "in_position": in_position,
        "buy_price": buy_price,
        "retCode": last_retcode,
        "retMsg": last_retmsg,
        "last_error": last_error,
        "last_check": last_check,
        "total_trades": total_trades,
        "base_url": BASE_URL,
        "symbol": SYMBOL,
    })

@app.route("/buy")
def buy_route():
    usdt, pepe, acc = get_balances()
    price = get_price()
    if in_position:
        return jsonify({"ok": False, "msg": "al in positie", "pepe": pepe})
    if last_retcode == 10003:
        return jsonify({"ok": False, "msg": "key niet geautoriseerd 10003", "retMsg": last_retmsg})
    if usdt < TRADE_USDT:
        return jsonify({"ok": False, "msg": f"te weinig USDT {usdt}", "usdt": usdt})
    ok = buy_pepe(TRADE_USDT, price)
    return jsonify({"ok": ok, "usdt": usdt, "price": price})

@app.route("/sell")
def sell_route():
    usdt, pepe, acc = get_balances()
    price = get_price()
    ok = sell_pepe(pepe, price, "handmatig via /sell")
    return jsonify({"ok": ok, "pepe": pepe, "price": price})

@app.route("/orders")
def orders_route():
    return jsonify({"orders": get_open_orders()})

# Start loop 1x
if not hasattr(app, "started"):
    app.started = True
    threading.Thread(target=check_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
