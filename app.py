import os, time, hmac, hashlib, json, requests
from urllib.parse import urlencode
from flask import Flask, request, jsonify

app = Flask(__name__)

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY","").strip().strip('"').strip("'")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET","").strip().strip('"').strip("'")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN","").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID","").strip()
BASE_URL = os.getenv("BYBIT_BASE_URL","https://api.bybit.eu").strip().rstrip("/")

TRADE_SIZE_USDT = 20.0
SYMBOL = "PEPEUSDT"
UTBOT_FACTOR = 4.0
UTBOT_ATR = 14

print(f"KEY LOADED: {len(BYBIT_API_KEY)} chars, start={BYBIT_API_KEY[:4]}****", flush=True)
print(f"BASE: {BASE_URL}", flush=True)

def send_tg(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print("TG fail", e, flush=True)

def get_server_time():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/time", timeout=10)
        d = r.json()
        return int(d["result"]["timeSecond"]) * 1000
    except Exception as e:
        print("time fail, use local", e, flush=True)
        return int(time.time()*1000)

def bybit_get(path, params):
    # FIX: 1 query-string voor tekenen EN verzenden
    ts = str(get_server_time())
    recv = "20000"
    # Bybit wil alfabetisch gesorteerd
    query = urlencode(sorted(params.items()))
    sign_str = ts + BYBIT_API_KEY + recv + query
    sign = hmac.new(BYBIT_API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "X-BAPI-SIGN-TYPE": "2",
        "Content-Type": "application/json"
    }
    url = f"{BASE_URL}{path}?{query}"
    print(f"GET {url}", flush=True)
    r = requests.get(url, headers=headers, timeout=15)
    print(f"RAW {r.status_code} {r.text[:300]}", flush=True)
    try:
        return r.json()
    except:
        return {"retCode": -1, "retMsg": "empty", "raw": r.text[:300]}

def get_balance():
    # EU = UNIFIED eerst, SPOT als fallback
    for acct in ["UNIFIED", "SPOT", "CONTRACT", "FUND"]:
        d = bybit_get("/v5/account/wallet-balance", {"accountType": acct})
        print(f"Saldo check {acct}: retCode={d.get('retCode')} retMsg={d.get('retMsg')}", flush=True)
        if d.get("retCode") == 0:
            try:
                lst = d["result"]["list"][0]
                coins = {c["coin"]: float(c.get("walletBalance", 0)) for c in lst.get("coin", [])}
                usdt = coins.get("USDT", 0)
                pepe = coins.get("PEPE", 0)
                print(f"Saldo OK {acct} USDT={usdt} PEPE={pepe}", flush=True)
                return usdt, pepe, acct
            except Exception as e:
                print("parse fail", e, flush=True)
                continue
    return 0, 0, None

def get_klines():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/kline",
                         params={"category": "spot", "symbol": SYMBOL, "interval": "15", "limit": 100}, timeout=15)
        d = r.json()
        kl = d["result"]["list"][::-1]
        closes = [float(x[4]) for x in kl]
        highs = [float(x[2]) for x in kl]
        lows = [float(x[3]) for x in kl]
        return closes, highs, lows
    except Exception as e:
        print("kline fail", e, flush=True)
        return [], [], []

def utbot_signal(closes, highs, lows):
    if len(closes) < 30:
        return None
    # ATR 14
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr = sum(trs[-UTBOT_ATR:]) / UTBOT_ATR
    # Supertrend-achtig trailing
    hl2 = [(h+l)/2 for h, l in zip(highs, lows)]
    upper = hl2[-1] + UTBOT_FACTOR * atr
    lower = hl2[-1] - UTBOT_FACTOR * atr
    last = closes[-1]
    prev = closes[-2]
    if prev < lower and last >= lower:
        return "BUY"
    if prev > upper and last <= upper:
        return "SELL"
    return None

def place_order(side, qty_usdt):
    # prijs ophalen
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers",
                         params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        price = float(r.json()["result"]["list"][0]["lastPrice"])
    except:
        return {"retCode": -1, "retMsg": "no price"}
    qty = str(int(qty_usdt / price))
    ts = str(get_server_time())
    recv = "20000"
    body = {"category": "spot", "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": qty}
    body_str = json.dumps(body)
    sign_str = ts + BYBIT_API_KEY + recv + body_str
    sign = hmac.new(BYBIT_API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "X-BAPI-SIGN-TYPE": "2",
        "Content-Type": "application/json"
    }
    r = requests.post(f"{BASE_URL}/v5/order/create", headers=headers, data=body_str, timeout=15)
    print(f"ORDER {side} {qty} -> {r.text[:500]}", flush=True)
    try:
        return r.json()
    except:
        return {"retCode": -1, "retMsg": "empty"}

@app.route("/")
def home():
    return "Pepebot-3 live"

@app.route("/test-key")
def test_key():
    usdt, pepe, acct = get_balance()
    ok = acct is not None
    return jsonify({"status": "OK" if ok else "FOUT", "account": acct, "usdt": usdt, "pepe": pepe, "key_start": BYBIT_API_KEY[:4], "base": BASE_URL})

@app.route("/check")
def check():
    print("=== Bot check gestart ===", flush=True)
    usdt, pepe, acct = get_balance()
    if usdt < 5:
        send_tg(f"Bot stopt: geen saldo. USDT={usdt} acct={acct}")
        return jsonify({"status": "geen saldo", "usdt": usdt, "acct": acct})
    closes, highs, lows = get_klines()
    sig = utbot_signal(closes, highs, lows)
    print(f"UTBOT sig={sig} last={closes[-1] if closes else None}", flush=True)
    if sig == "BUY" and usdt >= TRADE_SIZE_USDT:
        res = place_order("Buy", TRADE_SIZE_USDT)
        send_tg(f"BUY PEPE {TRADE_SIZE_USDT} USDT -> {res.get('retMsg')} {res.get('retCode')}")
        return jsonify({"signal": sig, "order": res})
    if sig == "SELL" and pepe > 0:
        # verkoop alles in USDT waarde
        try:
            r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": "spot", "symbol": SYMBOL}, timeout=10)
            price = float(r.json()["result"]["list"][0]["lastPrice"])
            val = pepe * price
            res = place_order("Sell", min(val, TRADE_SIZE_USDT))
            send_tg(f"SELL PEPE -> {res.get('retMsg')} {res.get('retCode')}")
            return jsonify({"signal": sig, "order": res})
        except Exception as e:
            return jsonify({"signal": sig, "error": str(e)})
    return jsonify({"status": "geen signaal", "signal": sig, "usdt": usdt, "pepe": pepe, "acct": acct})

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    print("Webhook:", data, flush=True)
    return check()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
