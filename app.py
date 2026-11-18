import os
import time
import hmac
import hashlib
import json
import requests
from urllib.parse import urlencode
from flask import Flask, request, jsonify

app = Flask(__name__)

KEY = os.getenv("BYBIT_API_KEY", "").strip()
SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

print("KEY_LEN " + str(len(KEY)) + " SECRET_LEN " + str(len(SECRET)) + " START " + KEY[:4], flush=True)

def tg(m):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage", json={"chat_id": TG_CHAT, "text": m}, timeout=10)
    except Exception as e:
        print("TG fail " + str(e), flush=True)

def s_get_one(base, path, params):
    ts = str(int(time.time() * 1000))
    q = urlencode(sorted(params.items()))
    msg = ts + KEY + "5000" + q
    s = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    h = {"X-BAPI-API-KEY": KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": s, "X-BAPI-SIGN-TYPE": "2"}
    try:
        r = requests.get(base + path + "?" + q, headers=h, timeout=15)
        try:
            return base, r.json()
        except Exception:
            return base, {"retCode": -1, "retMsg": "empty body " + r.text[:100]}
    except Exception as e:
        return base, {"retCode": -1, "retMsg": str(e)}

def s_get(path, params):
    # FOUT IN OUDE CODE: alleen api.bybit.eu. Nu probeert hij beide.
    for base in ["https://api.bybit.eu", "https://api.bybit.com", "https://api.bytick.com"]:
        b, d = s_get_one(base, path, params)
        print("TRY " + b + " " + str(d)[:300], flush=True)
        if d.get("retCode") == 0:
            return d
        # 10003 = verkeerde domein of verkeerde secret, probeer volgende domein
        if d.get("retCode") == 10003:
            continue
        if d.get("retCode") == 0:
            return d
    # geen enkele werkte, geef laatste terug voor debug
    return d

def bal():
    for a in ["SPOT", "UNIFIED"]:
        d = s_get("/v5/account/wallet-balance", {"accountType": a, "coin": "USDT"})
        print("BAL " + a + " " + str(d)[:400], flush=True)
        if d.get("retCode") == 0:
            try:
                coins = d["result"]["list"][0]["coin"]
                for c in coins:
                    if c["coin"] == "USDT":
                        return float(c["walletBalance"]), a, d
            except Exception as e:
                print("BAL parse " + str(e), flush=True)
    return 0.0, None, {}

def price():
    try:
        for base in ["https://api.bybit.eu", "https://api.bybit.com"]:
            r = requests.get(base + "/v5/market/tickers", params={"category": "spot", "symbol": "PEPEUSDT"}, timeout=10)
            return float(r.json()["result"]["list"][0]["lastPrice"])
    except Exception:
        return 0.0
    return 0.0

def klines():
    try:
        for base in ["https://api.bybit.eu", "https://api.bybit.com"]:
            r = requests.get(base + "/v5/market/kline", params={"category": "spot", "symbol": "PEPEUSDT", "interval": "15", "limit": 100}, timeout=15)
            k = r.json()["result"]["list"][::-1]
            c = [float(x[4]) for x in k]
            h = [float(x[2]) for x in k]
            l = [float(x[3]) for x in k]
            return c, h, l
    except Exception:
        return [], [], []
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

def order(side, val):
    try:
        p = price()
        if p == 0:
            return {"retCode": -1, "retMsg": "no price"}
        q = str(int(val / p))
        ts = str(int(time.time() * 1000))
        body = {"category": "spot", "symbol": "PEPEUSDT", "side": side, "orderType": "Market", "qty": q}
        bs = json.dumps(body)
        msg = ts + KEY + "5000" + bs
        s = hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
        h = {"X-BAPI-API-KEY": KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": s, "X-BAPI-SIGN-TYPE": "2", "Content-Type": "application/json"}
        for base in ["https://api.bybit.eu", "https://api.bybit.com"]:
            rr = requests.post(base + "/v5/order/create", headers=h, data=bs, timeout=15)
            print("ORDER " + side + " " + base + " " + rr.text[:300], flush=True)
            j = rr.json()
            if j.get("retCode") == 0:
                return j
        return j
    except Exception as e:
        return {"retCode": -1, "retMsg": str(e)}

@app.route("/")
def home():
    return "Pepebot-3 live"

@app.route("/test-key")
def tk():
    u, a, raw = bal()
    return jsonify({"status": "OK" if a else "FOUT", "account": a, "usdt": u, "key_len": len(KEY), "secret_len": len(SECRET), "key_start": KEY[:4], "bybit_raw": raw})

@app.route("/debug")
def dbg():
    out = {}
    for a in ["SPOT", "UNIFIED"]:
        d = s_get("/v5/account/wallet-balance", {"accountType": a, "coin": "USDT"})
        out[a] = d
    return jsonify(out)

@app.route("/check")
def chk():
    u, a, raw = bal()
    if u < 5:
        return jsonify({"status": "geen saldo", "usdt": u, "acct": a, "bybit_raw": raw})
    c, h, l = klines()
    s = sig(c, h, l)
    if s == "BUY" and u >= 20:
        r = order("Buy", 20)
        tg("BUY PEPE 20 USDT " + str(r.get("retMsg")))
        return jsonify({"signal": s, "order": r})
    if s == "SELL":
        r = order("Sell", 20)
        tg("SELL PEPE " + str(r.get("retMsg")))
        return jsonify({"signal": s, "order": r})
    return jsonify({"status": "geen signaal", "signal": s, "usdt": u, "acct": a})

@app.route("/webhook", methods=["POST"])
def wh():
    return chk()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
