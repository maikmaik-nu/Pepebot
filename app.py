import os, time, threading, json, hmac, hashlib, urllib.parse, urllib.request
from decimal import Decimal
from flask import Flask, jsonify

app = Flask(__name__)

SYMBOL = "PEPEUSDT"
INTERVAL = "15"
UTBOT_FACTOR = 4.0
ATR_PERIOD = 14
BASE = "https://api.bybit.com"

API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

def send_tg(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = json.dumps({"chat_id": TELEGRAM_CHAT, "text": msg}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print("TG ERR", e, flush=True)

def bybit_get(path, params):
    ts = str(int(time.time() * 1000))
    recv = "20000"
    qs = urllib.parse.urlencode(params)
    to_sign = ts + API_KEY + recv + qs
    sign = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    url = BASE + path + "?" + qs
    req = urllib.request.Request(url, headers={
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "X-BAPI-SIGN-TYPE": "2",
        "Content-Type": "application/json"
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())

def bybit_post(path, params):
    ts = str(int(time.time() * 1000))
    recv = "20000"
    body = json.dumps(params)
    to_sign = ts + API_KEY + recv + body
    sign = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    req = urllib.request.Request(BASE + path, data=body.encode(), headers={
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "X-BAPI-SIGN-TYPE": "2",
        "Content-Type": "application/json"
    }, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())

# --- FIX: auto-detect accountType ---
ACCOUNT_TYPE = [None]

def get_coin_balance(coin):
    for at in ["UNIFIED", "SPOT", "FUND"]:
        try:
            r = bybit_get("/v5/account/wallet-balance", {"accountType": at, "coin": coin})
            print(f"BAL {at} {coin} -> {r}", flush=True)
            if r.get("retCode") == 0:
                if ACCOUNT_TYPE[0] is None:
                    ACCOUNT_TYPE[0] = at
                for w in r.get("result", {}).get("list", []):
                    for c in w.get("coin", []):
                        if c.get("coin") == coin:
                            return Decimal(str(c.get("walletBalance", "0")))
                return Decimal("0")
        except Exception as e:
            print(f"BAL {at} ERR {e}", flush=True)
    return None

def get_balance():
    return get_coin_balance("USDT")

def get_klines():
    url = BASE + f"/v5/market/kline?category=spot&symbol={SYMBOL}&interval={INTERVAL}&limit=200"
    with urllib.request.urlopen(url, timeout=20) as r:
        d = json.loads(r.read().decode())
    return d["result"]["list"][::-1]

def calc_utbot(klines):
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    trs = []
    for i in range(1, len(klines)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr = sum(trs[:ATR_PERIOD]) / ATR_PERIOD
    for i in range(ATR_PERIOD, len(trs)):
        atr = (atr * (ATR_PERIOD-1) + trs[i]) / ATR_PERIOD
    last_close = closes[-1]
    prev_close = closes[-2]
    trailing_prev = prev_close - UTBOT_FACTOR * atr
    return last_close > trailing_prev, last_close < trailing_prev

state = {"running": False, "last": "gestart", "balance": "?"}

def bot_loop():
    time.sleep(5)
    send_tg("🤖 Pepebot-3 gestart - origineel + UTBOT")
    in_position = False
    while True:
        try:
            bal = get_balance()
            if bal is None:
                state["last"] = "Kan saldo niet lezen"
                send_tg("⚠️ Kan saldo niet lezen")
                time.sleep(60)
                continue
            state["balance"] = str(bal)
            klines = get_klines()
            is_buy, is_sell = calc_utbot(klines)
            pepe_bal = get_coin_balance("PEPE")
            if pepe_bal is None:
                pepe_bal = Decimal("0")
            state["last"] = f"bal={bal} PEPE={pepe_bal} buy={is_buy} sell={is_sell} acct={ACCOUNT_TYPE[0]}"
            print(state["last"], flush=True)
            if is_buy and not in_position and pepe_bal == 0 and bal > 5:
                price = float(klines[-1][4])
                qty = int(float(bal / Decimal(str(price))))
                if qty > 0:
                    o = bybit_post("/v5/order/create", {"category": "spot", "symbol": SYMBOL, "side": "Buy", "orderType": "Market", "qty": str(qty)})
                    print("BUY", o, flush=True)
                    send_tg(f"🟢 BUY PEPE {qty} @ {price}")
                    in_position = True
            elif is_sell and (in_position or pepe_bal > 0):
                qty = int(pepe_bal)
                if qty > 0:
                    o = bybit_post("/v5/order/create", {"category": "spot", "symbol": SYMBOL, "side": "Sell", "orderType": "Market", "qty": str(qty)})
                    print("SELL", o, flush=True)
                    send_tg(f"🔴 SELL PEPE {qty}")
                    in_position = False
            time.sleep(60)
        except Exception as e:
            print("LOOP ERR", e, flush=True)
            state["last"] = f"ERR {e}"
            time.sleep(60)

@app.route("/")
def home():
    return jsonify({"status": "Pepebot-3 live", "state": state, "key_start": API_KEY[:4] if API_KEY else "LEEG"})

@app.route("/test-key")
def test_key():
    try:
        r = bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": "USDT"})
        return jsonify({"key_start": API_KEY[:4] if API_KEY else "LEEG", "bybit_raw": r})
    except Exception as e:
        return jsonify({"key_start": API_KEY[:4] if API_KEY else "LEEG", "error": str(e)})

if not state["running"]:
    state["running"] = True
    threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
