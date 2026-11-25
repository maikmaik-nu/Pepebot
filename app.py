import os, time, hmac, hashlib, json, threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# --- CONFIG - JOUW WERKENDE CODE ---
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# --- STRATEGIE - ALLEEN DIT IS NIEUW ---
KEY_VALUE = 4
ATR_PERIOD = 14
INTERVAL = "60"
FEE = 0.01

print(f"KEY LOADED: key_len={len(API_KEY)} symbol={SYMBOL}", flush=True)

_lock = threading.Lock()
_last_check = 0

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"TELEGRAM SKIP: {msg}", flush=True)
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print(f"TELEGRAM FAIL: {e}", flush=True)

def _headers_for_get(params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    to_sign = ts + API_KEY + recv + query
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json"
    }

def bybit_get(path, params):
    headers = _headers_for_get(params)
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=15)
    print(f"【entity-BYBIT¦canonical_name=Bybit】 GET {path} -> {r.status_code} {r.text[:300]}", flush=True)
    return r.json()

def bybit_post(path, body):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    body_str = json.dumps(body)
    to_sign = ts + API_KEY + recv + body_str
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json"
    }
    r = requests.post(BASE_URL + path, data=body_str, headers=headers, timeout=15)
    print(f"【entity-BYBIT¦canonical_name=Bybit】 POST {path} -> {r.status_code} {r.text[:500]}", flush=True)
    return r.json()

def get_balances():
    # JOUW OUDE LOOP - laat staan, werkt
    # SPOT geeft 10001 fout bij 【entity-Bybit¦canonical_name=Bybit】 EU, die slaan we gewoon over
    out = {}
    for acct in ["SPOT", "UNIFIED"]:
        try:
            d = bybit_get("/v5/account/wallet-balance", {"accountType": acct})
            if d.get("retCode") != 0:
                print(f"Saldo {acct} overgeslagen: {d.get('retMsg')}", flush=True)
                continue
            lst = d.get("result", {}).get("list", [])
            if not lst:
                continue
            for coin in lst[0].get("coin", []):
                out[coin["coin"]] = float(coin.get("walletBalance", 0))
                print(f"Saldo {acct} {coin['coin']} = {coin.get('walletBalance')}", flush=True)
        except Exception as e:
            print(f"Saldo check {acct} FAIL: {e}", flush=True)
    return out

def get_price():
    r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    return float(r["result"]["list"][0]["lastPrice"])

def get_qty_step():
    r = requests.get(BASE_URL + "/v5/market/instruments-info", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    return float(r["result"]["list"][0]["lotSizeFilter"]["qtyStep"])

# --- UT BOT 4 / 14 - ECHTE TRAILING STOP ---
def get_klines(limit=100):
    r = requests.get(BASE_URL + "/v5/market/kline", params={
        "category": CATEGORY, "symbol": SYMBOL,
        "interval": INTERVAL, "limit": limit
    }, timeout=10).json()
    if r.get("retCode") != 0:
        print(f"KLINE FAIL: {r}", flush=True)
        return []
    rows = r.get("result", {}).get("list", [])
    if not rows:
        print("KLINE LEEG -> HOLD", flush=True)
        return []
    rows = rows[::-1]  # oud -> nieuw
    return [(float(x[2]), float(x[3]), float(x[4])) for x in rows]

def utbot_signal():
    klines = get_klines(100)
    if len(klines) < ATR_PERIOD + 5:
        print(f"UTBOT: te weinig candles ({len(klines)}) -> HOLD", flush=True)
        return "hold", get_price()

    # ATR 14
    trs = []
    for i in range(1, len(klines)):
        high, low, close = klines[i]
        prev_close = klines[i-1][2]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    atr = sum(trs[-ATR_PERIOD:]) / ATR_PERIOD
    xATR = atr * KEY_VALUE

    # Trailing stop
    trend = 1
    trailing = klines[0][2] - xATR
    for i in range(1, len(klines)):
        close = klines[i][2]
        if trend == 1:
            trailing = max(trailing, close - xATR)
            if close < trailing:
                trend = -1
                trailing = close + xATR
        else:
            trailing = min(trailing, close + xATR)
            if close > trailing:
                trend = 1
                trailing = close - xATR

    signal = "buy" if trend == 1 else "sell"
    price = klines[-1][2]
    print(f"UTBOT: trend={signal} price={price} atr={atr} trailing={trailing}", flush=True)
    return signal, price

def place_order(side, qty):
    body = {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": side,
        "orderType": "Market",
        "qty": str(qty)
    }
    return bybit_post("/v5/order/create", body)

def do_check():
    global _last_check
    now = time.time()
    if not _lock.acquire(blocking=False):
        return {"status": "already running"}
    try:
        if now - _last_check < 30:
            return {"status": "too soon, wait 30s"}
        _last_check = now

        bal = get_balances()
        cash = bal.get("USDT", 0) + bal.get("USDC", 0)
        pepe = bal.get("PEPE", 0)
        print(f"CHECK: cash={cash} PEPE={pepe}", flush=True)

        signal, price = utbot_signal()
        if signal == "hold":
            return {"status": "hold", "reason": "geen candles / geen signaal"}

        # Positie uit saldo - geen geheugen, dus restart-veilig
        # Je zit nu in PEPE, dus alleen SELL mag. Pas na SELL mag BUY weer.
        in_position = pepe > 0 and pepe * price > 5

        if signal == "buy" and in_position:
            print("UTBOT buy, maar al in positie -> HOLD", flush=True)
            return {"status": "hold", "signal": "buy", "reason": "al in positie"}
        if signal == "sell" and not in_position:
            print("UTBOT sell, maar geen positie -> HOLD", flush=True)
            return {"status": "hold", "signal": "sell", "reason": "geen positie"}

        qty_step = get_qty_step()

        # KOOP: ALLES erin, fee eraf - rente op rente
        if signal == "buy" and cash >= 5:
            spend = cash * (1 - FEE)
            qty = int((spend / price) / qty_step) * qty_step
            if qty <= 0:
                return {"status": "hold", "reason": "qty te klein"}
            print(f"KOOP ALLES: spend={spend} qty={qty} price={price}", flush=True)
            res = place_order("Buy", qty)
            if res.get("retCode") == 0:
                send_telegram(f"UTBOT KOOP ALLES: {qty} PEPE voor {spend:.2f}")
            return {"status": "buy", "qty": qty, "price": price, "bybit": res}

        # VERKOOP: ALLES eruit, fee eraf - rente op rente
        if signal == "sell" and in_position:
            qty = int((pepe * (1 - FEE)) / qty_step) * qty_step
            if qty <= 0:
                return {"status": "hold", "reason": "qty te klein"}
            print(f"VERKOOP ALLES: qty={qty} price={price}", flush=True)
            res = place_order("Sell", qty)
            if res.get("retCode") == 0:
                send_telegram(f"UTBOT VERKOOP ALLES: {qty} PEPE")
            return {"status": "sell", "qty": qty, "price": price, "bybit": res}

        return {"status": "hold", "signal": signal, "cash": cash, "pepe": pepe}
    except Exception as e:
        print(f"CHECK FAIL: {e}", flush=True)
        return {"status": "fail", "error": str(e)}
    finally:
        _lock.release()

def bot_loop():
    time.sleep(10)
    while True:
        try:
            print("AUTO CHECK gestart", flush=True)
            res = do_check()
            print(f"AUTO CHECK klaar: {res}", flush=True)
        except Exception as e:
            print(f"AUTO CHECK FAIL: {e}", flush=True)
        time.sleep(300)

threading.Thread(target=bot_loop, daemon=True).start()

@app.route("/")
def home():
    return "Pepebot-3 live"

@app.route("/test-key")
def test_key():
    bal = get_balances()
    try:
        price = get_price()
    except Exception as e:
        price = f"FAIL {e}"
    return jsonify({"balances": bal, "PEPEUSDT_price": price})

@app.route("/check")
def check():
    return jsonify(do_check())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
