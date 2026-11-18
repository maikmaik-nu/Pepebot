import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

KEY = os.getenv("BYBIT_API_KEY", "").strip().strip('"').strip("'")
SECRET = os.getenv("BYBIT_API_SECRET", "").strip().strip('"').strip("'")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

print("KEY LOADED: " + str(len(KEY)) + " chars, start=" + KEY[:4] + "****", flush=True)

def tg(msg):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post("https://api.telegram.org/bot" + TG_TOKEN + "/sendMessage", json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
    except Exception as e:
        print("TG fail", e, flush=True)

def get_balance():
    try:
        from pybit.unified_trading import HTTP
        for domain in ["bybit.eu", "bytick.com"]:
            try:
                s = HTTP(testnet=False, api_key=KEY, api_secret=SECRET, domain=domain)
                r = s.get_wallet_balance(accountType="UNIFIED", coin="USDT")
                print("PYBIT " + domain + " UNIFIED: " + str(r), flush=True)
                if r.get("retCode") == 0:
                    c = r["result"]["list"][0]["coin"][0]
                    return float(c["walletBalance"]), "UNIFIED-" + domain
                r2 = s.get_wallet_balance(accountType="SPOT", coin="USDT")
                print("PYBIT " + domain + " SPOT: " + str(r2), flush=True)
                if r2.get("retCode") == 0:
                    c = r2["result"]["list"][0]["coin"][0]
                    return float(c["walletBalance"]), "SPOT-" + domain
            except Exception as e:
                print("PYBIT " + domain + " fail: " + str(e), flush=True)
                continue
    except Exception as e:
        print("pybit fail", e, flush=True)
    return 0, None

def get_price():
    for domain in ["api.bybit.eu", "api.bytick.com"]:
        try:
            r = requests.get("https://" + domain + "/v5/market/tickers", params={"category": "spot", "symbol": "PEPEUSDT"}, timeout=10)
            return float(r.json()["result"]["list"][0]["lastPrice"])
        except Exception:
            continue
    return 0

def get_klines():
    try:
        r = requests.get("https://api.bybit.eu/v5/market/kline", params={"category": "spot", "symbol": "PEPEUSDT", "interval": "15", "limit": 100}, timeout=15)
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
        from pybit.unified_trading import HTTP
        price = get_price()
        if price == 0:
            return {"retCode": -1, "retMsg": "no price"}
        qty = str(int(usdt_val / price))
        s = HTTP(testnet=False, api_key=KEY, api_secret=SECRET, domain="bybit.eu")
        r = s.place_order(category="spot", symbol="PEPEUSDT", side=side, orderType="Market", qty=qty)
        print("ORDER " + side + " " + qty + " -> " + str(r), flush=True)
        return r
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
    print("=== Bot check gestart ===", flush=True)
    usdt, acct = get_balance()
    if usdt < 5:
        tg("Bot stopt: geen saldo. USDT=" + str(usdt) + " acct=" + str(acct))
        return jsonify({"status": "geen saldo", "usdt": usdt, "acct": acct})
    closes, highs, lows = get_klines()
    sig = utbot(closes, highs, lows)
    print("UTBOT sig=" + str(sig) + " usdt=" + str(usdt) + " acct=" + str(acct), flush=True)
    if sig == "BUY" and usdt >= 20:
        res = place_order("Buy", 20)
        tg("BUY PEPE 20 USDT -> " + str(res.get("retMsg")) + " " + str(res.get("retCode")))
        return jsonify({"signal": sig, "order": res})
    if sig == "SELL":
        res = place_order("Sell", 20)
        tg("SELL PEPE -> " + str(res.get("retMsg")) + " " + str(res.get("retCode")))
        return jsonify({"signal": sig, "order": res})
    return jsonify({"status": "geen signaal", "signal": sig, "usdt": usdt, "acct": acct})

@app.route("/webhook", methods=["POST"])
def webhook():
    print("Webhook:", request.get_json(force=True, silent=True), flush=True)
    return check()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
