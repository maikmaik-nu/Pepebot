import os, time, hmac, hashlib, json, requests
from flask import Flask

app = Flask(__name__)
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
SYMBOL = "PEPEUSDT"
UTBOT_FACTOR = 4.0
UTBOT_ATR = 14
DOMAINS = ["https://api.bybit.eu", "https://api.bybit.com"]
WORKING = {"base": DOMAINS[0]}

def safe_json(r):
    try:
        return r.json()
    except Exception:
        return {"retCode": -1, "result": {}}

def headers(q=""):
    ts = str(int(time.time()*1000))
    recv = "5000"
    sig = hmac.new(API_SECRET.encode(), (ts + API_KEY + recv + q).encode(), hashlib.sha256).hexdigest()
    return {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "X-BAPI-SIGN": sig, "Content-Type": "application/json"}

def p_get(path, params):
    qs = "&".join(f"{k}={params[k]}" for k in sorted(params))
    # Probeer eerst laatst werkende domein, daarna de andere
    for base in [WORKING["base"]] + [d for d in DOMAINS if d != WORKING["base"]]:
        r = requests.get(base + path + "?" + qs, headers=headers(qs), timeout=15)
        j = safe_json(r)
        print(f"TRY {base} -> {j.get('retCode')}", flush=True)
        if j.get("retCode") == 0:
            WORKING["base"] = base
            return j
    return {"retCode": -1, "result": {}}

def p_post(path, body):
    payload = json.dumps(body, separators=(",", ":"))
    base = WORKING["base"]
    r = requests.post(base + path, headers=headers(payload), data=payload, timeout=15)
    return safe_json(r)

def get_balance(coin):
    for acct in ["UNIFIED", "SPOT"]:
        d = p_get("/v5/account/wallet-balance", {"accountType": acct, "coin": coin})
        print(f"BAL {acct} {coin} ->", d, flush=True)
        try:
            v = float(d["result"]["list"][0]["coin"][0]["walletBalance"])
            if v > 0:
                return v
        except Exception:
            pass
    return 0.0

def get_klines():
    try:
        r = requests.get("https://api.bybit.com/v5/market/kline", params={"category": "spot", "symbol": SYMBOL, "interval": "15", "limit": "100"}, timeout=15).json()
        lst = r.get("result", {}).get("list", []) or []
        return [float(x[4]) for x in lst][::-1] if lst else []
    except Exception:
        return []

def utbot(closes):
    if len(closes) < 30:
        return "HOLD"
    trs = [abs(closes[i]-closes[i-1]) for i in range(1, len(closes))]
    atr = sum(trs[-UTBOT_ATR:]) / UTBOT_ATR
    if closes[-1] > closes[-2] + UTBOT_FACTOR * atr * 0.1:
        return "BUY"
    if closes[-1] < closes[-2] - UTBOT_FACTOR * atr * 0.1:
        return "SELL"
    return "HOLD"

@app.route("/")
def home():
    bal = get_balance("USDT")
    closes = get_klines()
    if not closes:
        return f"OK BAL={bal} NO KLINES"
    sig = utbot(closes)
    price = closes[-1]
    print(f"BAL={bal} SIG={sig} PRICE={price} BASE={WORKING['base']}", flush=True)
    if bal > 5 and sig == "BUY":
        qty = str(int((bal*0.99)/price))
        res = p_post("/v5/order/create", {"category": "spot", "symbol": SYMBOL, "side": "Buy", "orderType": "Market", "qty": qty})
        print("BUY ->", res, flush=True)
        return f"BUY {qty} BAL={bal}"
    if sig == "SELL":
        pepe = get_balance("PEPE")
        if pepe > 0:
            res = p_post("/v5/order/create", {"category": "spot", "symbol": SYMBOL, "side": "Sell", "orderType": "Market", "qty": str(int(pepe))})
            print("SELL ->", res, flush=True)
            return f"SELL {pepe}"
    return f"OK BAL={bal} SIG={sig} PRICE={price}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
