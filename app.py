import os, time, hmac, hashlib, json, threading, math, traceback
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# --- CONFIG ---
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDC"  # je cash staat in USDC
CATEGORY = "spot"
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# --- UT BOT 4 / 14 - ALLEEN DIT, GEEN SUPERTREND ---
KEY_VALUE = 4
ATR_PERIOD = 14
INTERVAL = "60"  # 1h candles
FEE = 0.001  # 0,1% Bybit Spot Taker
MAX_PER_ORDER = 2500000  # Bybit max per market-order, 10 -> 100 fix

print(f"KEY LOADED: key_len={len(API_KEY)} symbol={SYMBOL} fee={FEE}", flush=True)

_lock = threading.Lock()
_last_check = 0

def log(msg):
    print(msg, flush=True)

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log(f"TELEGRAM SKIP: {msg}")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        log(f"TELEGRAM FAIL: {e}")

def _sign(params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    to_sign = ts + API_KEY + recv + query
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json"
    }
    return headers

def bybit_get(path, params):
    headers = _sign(params)
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=15)
    log(f"BYBIT GET {path} -> {r.status_code} {r.text[:300]}")
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
    log(f"BYBIT POST {path} -> {r.status_code} {r.text[:500]}")
    return r.json()

def get_balances():
    out = {}
    # Alleen UNIFIED, je account is UNIFIED-only, SPOT geeft 10001
    try:
        d = bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        if d.get("retCode") != 0:
            log(f"Saldo UNIFIED fail: {d.get('retCode')} {d.get('retMsg')}")
            return out
        lst = d.get("result", {}).get("list", [])
        if not lst:
            return out
        for coin in lst[0].get("coin", []):
            out[coin["coin"]] = float(coin.get("walletBalance", 0))
            log(f"Saldo UNIFIED {coin['coin']} = {coin.get('walletBalance')}")
    except Exception as e:
        log(f"Saldo check UNIFIED FAIL: {e}")
        traceback.print_exc()
    return out

def get_price():
    r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    return float(r["result"]["list"][0]["lastPrice"])

def get_qty_step():
    r = requests.get(BASE_URL + "/v5/market/instruments-info", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    return float(r["result"]["list"][0]["lotSizeFilter"]["qtyStep"])

# --- UT BOT 4 / 14 - ECHTE BEREKENING ---
def get_klines(limit=100):
    try:
        r = requests.get(BASE_URL + "/v5/market/kline", params={
            "category": CATEGORY, "symbol": SYMBOL,
            "interval": INTERVAL, "limit": limit
        }, timeout=10).json()
        if r.get("retCode") != 0:
            log(f"KLINE FAIL retCode={r.get('retCode')} {r.get('retMsg')}")
            return []
        lst = r.get("result", {}).get("list", [])
        if not lst:
            log("KLINE LEEG - result list is leeg")
            return []
        # Bybit geeft nieuw -> oud, wij draaien om naar oud -> nieuw
        rows = lst[::-1]
        # [start, open, high, low, close, volume, turnover]
        return [(float(x[2]), float(x[3]), float(x[4])) for x in rows]
    except Exception as e:
        log(f"KLINE EXCEPT: {e}")
        traceback.print_exc()
        return []

def utbot_signal():
    klines = get_klines(100)
    if len(klines) < ATR_PERIOD + 5:
        log(f"UTBOT: te weinig candles ({len(klines)}) -> HOLD")
        return "hold", klines[-1][2] if klines else 0

    # ATR 14
    trs = []
    for i in range(1, len(klines)):
        high, low, close = klines[i]
        prev_close = klines[i-1][2]
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    atr = sum(trs[-ATR_PERIOD:]) / ATR_PERIOD
    xATR = atr * KEY_VALUE

    # Trailing stop - loopt door alle candles heen
    trend = 1  # 1 = up, -1 = down
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
    log(f"UTBOT: trend={signal} price={price} atr={atr} trailing={trailing}")
    return signal, price

def place_order_chunked(side, qty_total):
    # Opknippen in max 2.500.000, afgerond op 1000 - 10 -> 100 fix
    qty_total = math.floor(qty_total / 1000) * 1000
    if qty_total <= 0:
        return {"retCode": -1, "retMsg": "qty te klein"}

    done_qty = 0
    last_res = {}
    while done_qty < qty_total:
        qty = min(MAX_PER_ORDER, qty_total - done_qty)
        qty = math.floor(qty / 1000) * 1000
        if qty <= 0:
            break

        body = {
            "category": CATEGORY,
            "symbol": SYMBOL,
            "side": side,
            "orderType": "Market",
            "qty": str(int(qty))
        }
        log(f"ORDER {side} {SYMBOL} qty {qty} chunk {done_qty + qty}/{qty_total}")
        last_res = bybit_post("/v5/order/create", body)
        log(f"ORDER {side} resultaat: {last_res}")

        if last_res.get("retCode") != 0:
            log(f"ORDER STOP: chunk mislukt na {done_qty} van {qty_total}")
            break

        done_qty += qty
        time.sleep(1)

    return last_res

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
        usdc = bal.get("USDC", 0)
        usdt = bal.get("USDT", 0)
        pepe = bal.get("PEPE", 0)
        # Voor PEPEUSDC is USDC je cash. USDT tellen we mee als je die ooit hebt.
        cash = usdc + usdt
        log(f"CHECK: cash={cash} USDC={usdc} USDT={usdt} PEPE={pepe}")

        signal, price = utbot_signal()
        if signal == "hold" or price <= 0:
            return {"status": "hold", "signal": signal}

        # Positie bepaalt wat mag, niet geheugen - restart-veilig
        in_position = pepe > 0 and pepe * price > 5

        if signal == "buy" and in_position:
            log("UTBOT buy, maar al in positie -> HOLD")
            return {"status": "hold", "signal": "buy", "reason": "al in positie"}
        if signal == "sell" and not in_position:
            log("UTBOT sell, maar geen positie -> HOLD")
            return {"status": "hold", "signal": "sell", "reason": "geen positie"}

        qty_step = get_qty_step()

        # KOOP: ALLES erin, fee eraf, rente op rente
        if signal == "buy" and cash >= 5:
            spend = cash * (1 - FEE)
            qty = int((spend / price) / qty_step) * qty_step
            if qty <= 0:
                return {"status": "hold", "reason": "qty te klein"}
            log(f"KOOP ALLES: spend={spend} qty={qty} price={price}")
            res = place_order_chunked("Buy", qty)
            if res.get("retCode") == 0:
                send_telegram(f"UTBOT KOOP ALLES: {qty} PEPE voor {spend:.2f}")
            return {"status": "buy", "qty": qty, "price": price, "bybit": res}

        # VERKOOP: ALLES eruit, fee eraf, rente op rente
        if signal == "sell" and in_position:
            qty = int((pepe * (1 - FEE)) / qty_step) * qty_step
            if qty <= 0:
                return {"status": "hold", "reason": "qty te klein"}
            log(f"VERKOOP ALLES: qty={qty} price={price}")
            res = place_order_chunked("Sell", qty)
            if res.get("retCode") == 0:
                send_telegram(f"UTBOT VERKOOP ALLES: {qty} PEPE")
            return {"status": "sell", "qty": qty, "price": price, "bybit": res}

        return {"status": "hold", "signal": signal, "cash": cash, "pepe": pepe}
    except Exception as e:
        log(f"CHECK FAIL: {e}")
        traceback.print_exc()
        return {"status": "fail", "error": str(e)}
    finally:
        _lock.release()

def bot_loop():
    time.sleep(10)
    while True:
        try:
            log("AUTO CHECK gestart")
            res = do_check()
            log(f"AUTO CHECK klaar: {res}")
        except Exception as e:
            log(f"AUTO CHECK FAIL: {e}")
            traceback.print_exc()
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
    return jsonify({"balances": bal, "PEPEUSDC_price": price})

@app.route("/check")
def check():
    return jsonify(do_check())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
