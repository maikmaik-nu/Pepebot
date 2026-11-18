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
BASE = "https://api.bybit.eu"

print("KEY LOADED " + str(len(KEY)) + " " + KEY[:4], flush=True)

def tg(msg):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage", json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
    except Exception as e:
        print("TG fail", e, flush=True)

def signed_get(path, params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    query = urlencode(sorted(params.items()))
    sign_str = ts + KEY + recv + query
    sign = hmac.new(SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "X-BAPI-SIGN-TYPE": "2",
        "Content-Type": "application/json"
    }
    url = BASE + path + "?" + query
    r = requests.get(url, headers=headers, timeout=15)
    try:
        return r.json()
    except Exception:
        return {"retCode": -1, "retMsg": "empty", "status": r.status_code}

def get_balance():
    for acct in ["UNIFIED", "SPOT"]:
        d = signed_get("/v5/account/wallet-balance", {"accountType": acct, "coin": "USDT"})
        print("BALANCE " + acct + " " + str(d), flush=True)
        if d.get("retCode") == 0:
            try:
                coins = d["result"]["list"][0]["coin"]
                for c in coins:
                    if c["coin"] == "USDT":
                        return float(c["walletBalance"]), acct
            except Exception:
                pass
    return 0, None

def get_price():
    try:
        r = requests.get(BASE + "/v5/market/tickers", params={"category": "spot", "symbol": "PEPEUSDT"}, timeout=10)
        return float(r.json()["result"]["list"][0]["lastPrice"])
    except Exception:
        return 0

def get_klines():
    try:
        r = requests.get(BASE + "/v5/market/kline", params={"category": "spot", "symbol": "PEPEUSDT", "interval": "15", "limit": 100}, timeout=15)
        kl = r.json()["result"]["list"][::-1]
        closes = [float(x[4]) for x in kl]
        highs = [float(x[2]) for x in kl]
        lows = [float(x[3]) for x in kl]
        return closes, highs, lows
    except Exception:
        return [], [], []

def utbot(closes, highs, lows):
    if len(closes) < 30:
        return None
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1])))
    atr = sum(trs[-14:]) / 14
    hl2 = (highs[-1] + lows[-1]) / 2
    upper = hl2 + 4.0 * atr
    lower = hl2 - 4.0 * atr
    if closes[-2] < lower and closes[-1] >= lower:
        return "BUY"
    if closes[-2] > upper and closes[-1] <= upper:
        return "SELL"
    return None

def place_order(side, usdt_val):
    try:
        price = get_price()
        if price == 0:
            return {"retCode": -1, "retMsg": "no price"}
        qty = str(int(usdt_val / price))
        ts = str(int(time.time() * 1000))
        recv = "5000"
        body = {"category": "spot", "symbol": "PEPEUSDT", "side": side, "orderType": "Market", "qty": qty}
        body_str = json.dumps(body)
        sign_str = ts + KEY + recv + body_str
        sign = hmac.new(SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": KEY,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
            "X-BAPI-SIGN": sign,
            "X-BAPI-SIGN-TYPE": "2",
            "Content-Type": "application/json"
        }
        r = requests.post(BASE + "/v5/order/create", headers=headers, data=body_str, timeout=15)
        print("ORDER " + side + " " + qty + " " + r.text[:300], flush=True)
        return r.json()
    except Exception as e:
        return {"retCode": -1, "retMsg": str(e)}

@app.route("/")
def home():
    return "Pepebot-3 live"

@app.route("/test-key")
def test_key():
    usdt, acct = get_balance()
    return jsonify({"status": "OK" if acct else "FOUT", "account": acct, "usdt": usdt, "key_start": KEY[:4]})

@app.route("/check")
def check():
    print("BOT CHECK", flush=True)
    usdt, acct = get_balance()
    if usdt < 5:
        tg("Bot stopt geen saldo USDT=" + str(usdt))
        return jsonify({"status": "geen saldo", "usdt": usdt, "acct": acct})
    closes, highs, lows = get_klines()
    sig = utbot(closes, highs, lows)
    print("SIG " + str(sig) + " USDT " + str(usdt), flush=True)
    if sig == "BUY" and usdt >= 20:
        res = place_order("Buy", 20)
        tg("BUY PEPE 20 USDT " + str(res.get("retMsg")))
        return jsonify({"signal": sig, "order": res})
    if sig == "SELL":
        res = place_order("Sell", 20)
        tg("SELL PEPE " + str(res.get("retMsg")))
        return jsonify({"signal": sig, "order": res})
    return jsonify({"status": "geen signaal", "signal": sig, "usdt": usdt, "acct": acct})

@app.route("/webhook", methods=["POST"])
def webhook():
    print("Webhook", request.get_json(force=True, silent=True), flush=True)
    return check()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
