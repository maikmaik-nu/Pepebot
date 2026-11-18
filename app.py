import os
import time
import threading
import hmac
import hashlib
import json
import requests
from urllib.parse import urlencode
from flask import Flask, request, jsonify

app = Flask(__name__)

KEY = os.getenv("BYBIT_API_KEY", "").strip()
SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
TG_TOKEN = (os.getenv("TELEGRAM_TOKEN", "") or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

print("KEY_LEN " + str(len(KEY)) + " SECRET_LEN " + str(len(SECRET)) + " START " + KEY[:4], flush=True)

_CHECK_LOCK = threading.Lock()
_LOOP_STARTED = False

def log(m):
    print(str(m), flush=True)

def tg(m):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage", json={"chat_id": TG_CHAT, "text": m}, timeout=10)
    except Exception as e:
        log("TG fail " + str(e))

def s_get_one(base, path, params):
    ts = str(int(time.time() * 1000))
    q = urlencode(sorted(params.items()))
    msg = ts + KEY + "5000" + q
    s = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    h = {"X-BAPI-API-KEY": KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": s, "X-BAPI-SIGN-TYPE": "2"}
    try:
        r = requests.get(base + path + "?" + q, headers=h, timeout=15)
        try:
            return r.json()
        except Exception:
            return {"retCode": -1, "retMsg": "empty body " + r.text[:100]}
    except Exception as e:
        return {"retCode": -1, "retMsg": str(e)}

def s_get(path, params):
    d = {"retCode": -1, "retMsg": "no base tried"}
    for base in ["https://api.bybit.eu", "https://api.bybit.com", "https://api.bytick.com"]:
        d = s_get_one(base, path, params)
        log("TRY " + base + " " + str(d)[:300])
        if d.get("retCode") == 0:
            return d
    return d

def get_balance(coin):
    for a in ["SPOT", "UNIFIED"]:
        d = s_get("/v5/account/wallet-balance", {"accountType": a, "coin": coin})
        if d.get("retCode") == 0:
            try:
                coins = d["result"]["list"][0]["coin"]
                for c in coins:
                    if c["coin"] == coin:
                        return float(c["walletBalance"]), a, d
            except Exception as e:
                log("BAL parse " + str(e))
    return 0.0, None, {}

def price():
    for base in ["https://api.bybit.eu", "https://api.bybit.com"]:
        try:
            r = requests.get(base + "/v5/market/tickers", params={"category": "spot", "symbol": "PEPEUSDT"}, timeout=10)
            return float(r.json()["result"]["list"][0]["lastPrice"])
        except Exception as e:
            log("PRICE fail " + base + " " + str(e))
            continue
    return 0.0

def klines():
    for base in ["https://api.bybit.eu", "https://api.bybit.com"]:
        try:
            r = requests.get(base + "/v5/market/kline", params={"category": "spot", "symbol": "PEPEUSDT", "interval": "15", "limit": 100}, timeout=15)
            k = r.json()["result"]["list"][::-1]
            c = [float(x[4]) for x in k]
            h = [float(x[2]) for x in k]
            l = [float(x[3]) for x in k]
            return c, h, l
        except Exception as e:
            log("KLINES fail " + base + " " + str(e))
            continue
    return [], [], []

def sig(c, h, l):
    if len(c) < 30:
        return None
    tr = []
    for i in range(1, len(c)):
        tr.append(max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1])))
    atr = sum(tr[-14:]) / 14
    mid = (h[-1] + l[-1]) / 2
    up = mid + 4.0 * atr
    lo = mid - 4.0 * atr
    if c[-2] < lo and c[-1] >= lo:
        return "BUY"
    if c[-2] > up and c[-1] <= up:
        return "SELL"
    return None

def get_qty_step():
    try:
        r = requests.get("https://api.bybit.eu/v5/market/instruments-info", params={"category": "spot", "symbol": "PEPEUSDT"}, timeout=10)
        lst = r.json()["result"]["list"]
        if lst:
            f = lst[0].get("lotSizeFilter", {})
            return float(f.get("qtyStep", 1)), float(f.get("minOrderQty", 1))
    except Exception as e:
        log("STEP fail " + str(e))
    return 1.0, 1.0

def order(side, val_or_qty, is_qty=False):
    try:
        p = price()
        if p == 0:
            return {"retCode": -1, "retMsg": "no price"}
        qty_step, min_qty = get_qty_step()
        q_num = val_or_qty if is_qty else val_or_qty / p
        q_num = int(q_num / qty_step) * qty_step
        if q_num < min_qty:
            return {"retCode": -1, "retMsg": "qty te klein: " + str(q_num)}
        q = str(int(q_num)) if qty_step == 1 else str(q_num)
        ts = str(int(time.time() * 1000))
        body = {"category": "spot", "symbol": "PEPEUSDT", "side": side, "orderType": "Market", "qty": q}
        bs = json.dumps(body)
        msg = ts + KEY + "5000" + bs
        s = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
        h = {"X-BAPI-API-KEY": KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": s, "X-BAPI-SIGN-TYPE": "2", "Content-Type": "application/json"}
        j = {"retCode": -1, "retMsg": "no base tried"}
        for base in ["https://api.bybit.eu", "https://api.bybit.com"]:
            rr = requests.post(base + "/v5/order/create", headers=h, data=bs, timeout=15)
            log("ORDER " + side + " " + base + " " + rr.text[:300])
            j = rr.json()
            if j.get("retCode") == 0:
                return j
        return j
    except Exception as e:
        return {"retCode": -1, "retMsg": str(e)}

def check_bot():
    if not _CHECK_LOCK.acquire(blocking=False):
        return {"status": "al bezig"}
    try:
        log("=== Bot check gestart ===")
        u, a, raw = get_balance("USDT")
        pepe, _, _ = get_balance("PEPE")
        log(f"Saldo USDT: {u} | PEPE: {pepe} | Acct: {a}")
        if a is None:
            log("Bot stopt: geen saldo / key fout: " + str(raw)[:300])
            return {"status": "geen saldo / key fout", "usdt": u, "bybit_raw": raw}
        if u < 5 and pepe <= 0:
            return {"status": "geen saldo", "usdt": u, "pepe": pepe}
        c, h, l = klines()
        s = sig(c, h, l)
        log(f"Signaal: {s} | USDT: {u} | PEPE: {pepe}")
        if s == "BUY" and pepe <= 0 and u >= 20:
            r = order("Buy", 20)
            tg("BUY PEPE 20 USDT " + str(r.get("retMsg")))
            return {"signal": s, "order": r}
        if s == "SELL" and pepe > 0:
            r = order("Sell", pepe, is_qty=True)
            tg("SELL PEPE alles " + str(r.get("retMsg")))
            return {"signal": s, "order": r}
        return {"status": "geen signaal", "signal": s, "usdt": u, "pepe": pepe, "acct": a}
    finally:
        _CHECK_LOCK.release()

def loop():
    while True:
        try:
            check_bot()
        except Exception as e:
            log("Loop error: " + str(e))
        time.sleep(900)

def start_loop_once():
    global _LOOP_STARTED
    if _LOOP_STARTED:
        return
    _LOOP_STARTED = True
    threading.Thread(target=loop, daemon=True).start()

@app.route("/")
def home():
    return "Pepebot-3 live"

@app.route("/test-key")
def tk():
    u, a, raw = get_balance("USDT")
    pepe, _, _ = get_balance("PEPE")
    return jsonify({"status": "OK" if a else "FOUT", "account": a, "usdt": u, "pepe": pepe, "key_len": len(KEY), "secret_len": len(SECRET), "key_start": KEY[:4], "bybit_raw": raw})

@app.route("/debug")
def dbg():
    out = {}
    for a in ["SPOT", "UNIFIED"]:
        out[a] = s_get("/v5/account/wallet-balance", {"accountType": a, "coin": "USDT"})
    return jsonify(out)

@app.route("/check")
def chk():
    return jsonify(check_bot())

@app.route("/webhook", methods=["POST"])
def wh():
    return jsonify(check_bot())

start_loop_once()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
