import os
import time
import hmac
import hashlib
import json
import requests
import threading
from flask import Flask, jsonify

app = Flask(__name__)

# ===== CONFIG UIT RENDER =====
API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
BASE_URL = os.getenv("BASE_URL", "https://api.bybit.eu").strip().rstrip("/")
CATEGORY = os.getenv("CATEGORY", "spot").strip()
SYMBOL = os.getenv("SYMBOL", "PEPEUSDT").strip()
TRADE_USDT = float(os.getenv("TRADE_USDT", "10"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TAKE_PROFIT_PCT = 10.0
STOP_LOSS_PCT = 5.0

# ===== STATE =====
in_position = False
buy_price = 0.0
last_retcode = None
last_retmsg = ""
last_usdt = 0.0
last_pepe = 0.0
last_price = 0.0
last_error = ""
last_check = ""
working_account = "UNIFIED"
total_trades = 0

HEADERS_BASE = {"User-Agent": "Mozilla/5.0"}

def log(msg):
    print(f"INFO:pepebot:{msg}", flush=True)

def telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10,
            headers=HEADERS_BASE
        )
    except Exception as e:
        log(f"telegram error {e}")

def api_get(path, params):
    global last_retcode, last_retmsg
    ts = str(int(time.time() * 1000))
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    to_sign = ts + API_KEY + "5000" + query
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000",
        "X-BAPI-SIGN": sig,
        "X-BAPI-SIGN-TYPE": "2",
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json"
    }
    try:
        r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=15)
        log(f"api {path} -> {r.status_code} {r.text[:600]}")
        try:
            j = r.json()
        except:
            last_retcode = r.status_code
            last_retmsg = f"http {r.status_code} leeg"
            return {"retCode": r.status_code, "retMsg": "http leeg"}
        last_retcode = j.get("retCode")
        last_retmsg = j.get("retMsg", "")
        return j
    except Exception as e:
        log(f"api error {e}")
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
        "X-BAPI-SIGN-TYPE": "2",
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json"
    }
    try:
        r = requests.post(BASE_URL + path, data=body_str, headers=headers, timeout=15)
        log(f"api POST {path} -> {r.status_code} {r.text[:600]}")
        try:
            j = r.json()
        except:
            last_retcode = r.status_code
            last_retmsg = f"http {r.status_code} leeg"
            return {"retCode": r.status_code, "retMsg": "http leeg"}
        last_retcode = j.get("retCode")
        last_retmsg = j.get("retMsg", "")
        return j
    except Exception as e:
        log(f"api post error {e}")
        return {"retCode": -1, "retMsg": str(e)}

def get_balances():
    global last_usdt, last_pepe, working_account
    for acc_try in ["UNIFIED", "SPOT"]:
        d = api_get("/v5/account/wallet-balance", {"accountType": acc_try})
        if d.get("retCode") == 0:
            working_account = acc_try
            usdt = 0.0
            pepe = 0.0
            try:
                lst = d.get("result", {}).get("list", [])
                if lst:
                    for c in lst[0].get("coin", []):
                        if c["coin"] == "USDT":
                            usdt = float(c.get("walletBalance", 0))
                        if c["coin"] == "PEPE":
                            pepe = float(c.get("walletBalance", 0))
            except Exception as e:
                log(f"balance parse {e}")
            last_usdt = usdt
            last_pepe = pepe
            return usdt, pepe, acc_try
        if d.get("retCode") == 10003:
            continue
        break
    last_usdt = 0.0
    last_pepe = 0.0
    return 0.0, 0.0, "NONE"

def get_price():
    global last_price
    try:
        r = requests.get(
            BASE_URL + "/v5/market/tickers",
            params={"category": CATEGORY, "symbol": SYMBOL},
            timeout=10,
            headers={"User-Agent": "Mozilla/5.0"}
        ).json()
        price = float(r["result"]["list"][0]["lastPrice"])
        last_price = price
        return price
    except Exception as e:
        log(f"price error {e}")
        return 0.0

def buy_pepe(usdt_amount, price):
    global in_position, buy_price, total_trades
    if price <= 0:
        return False
    qty = int(usdt_amount / price)
    if qty <= 0:
        return False
    log(f"BUY {qty} {SYMBOL} voor {usdt_amount} USDT @ {price}")
    res = api_post("/v5/order/create", {"category": CATEGORY, "symbol": SYMBOL, "side": "Buy", "orderType": "Market", "qty": str(qty)})
    if res.get("retCode") == 0:
        in_position = True
        buy_price = price
        total_trades += 1
        telegram(f"✅ GEKOCHT: {qty} PEPE @ {price} ({usdt_amount} USDT)")
        return True
    log(f"BUY FAIL {res}")
    telegram(f"❌ BUY mislukt: {res.get('retMsg')}")
    return False

def sell_pepe(pepe_amount, price, reden=""):
    global in_position
    qty = int(pepe_amount)
    if qty <= 0:
        return False
    log(f"SELL {qty} {SYMBOL} @ {price} reden={reden}")
    res = api_post("/v5/order/create", {"category": CATEGORY, "symbol": SYMBOL, "side": "Sell", "orderType": "Market", "qty": str(qty)})
    if res.get("retCode") == 0:
        in_position = False
        telegram(f"💰 VERKOCHT: {qty} PEPE @ {price} - {reden}")
        return True
    log(f"SELL FAIL {res}")
    return False

def check_loop():
    global in_position, buy_price, last_error, last_check
    log(f"bot gestart BASE={BASE_URL} SYMBOL={SYMBOL} CATEGORY={CATEGORY} TRADE={TRADE_USDT}")
    try:
        usdt, pepe, acc = get_balances()
        if pepe > 0:
            in_position = True
            buy_price = get_price()
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
            if last_retcode == 10003:
                log("SKIP: key niet geautoriseerd")
                time.sleep(CHECK_INTERVAL)
                continue
            if in_position and buy_price > 0 and price > 0 and pepe > 0:
                winst = (price - buy_price) / buy_price * 100
                log(f"winst={winst:.2f}% buy={buy_price} now={price}")
                if winst >= TAKE_PROFIT_PCT:
                    sell_pepe(pepe, price, f"Take Profit +{winst:.1f}%")
                elif winst <= -STOP_LOSS_PCT:
                    sell_pepe(pepe, price, f"Stop Loss {winst:.1f}%")
        except Exception as e:
            last_error = str(e)
            log(f"CHECK error {e}")
        time.sleep(CHECK_INTERVAL)

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
        "working_account": working_account,
        "key_ok": bool(API_KEY and API_SECRET),
        "key_len": len(API_KEY),
        "secret_len": len(API_SECRET),
    })

@app.route("/status")
def status():
    return jsonify({
        "usdt": last_usdt, "pepe": last_pepe, "price": last_price,
        "in_position": in_position, "buy_price": buy_price,
        "retCode": last_retcode, "retMsg": last_retmsg,
        "last_error": last_error, "last_check": last_check,
        "total_trades": total_trades, "base_url": BASE_URL, "symbol": SYMBOL,
        "working_account": working_account,
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

if not hasattr(app, "started"):
    app.started = True
    threading.Thread(target=check_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
