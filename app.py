import os, time, hmac, hashlib, json, threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()

# UTBOT 4 / 14
INTERVAL = "15"
ATR_PERIOD = 14
UT_FACTOR = 4.0
STATE_FILE = "state.json"

print(f"KEY LOADED: key_len={len(API_KEY)} secret_len={len(API_SECRET)} base={BASE_URL}", flush=True)

STATE = {"in_position": False, "trailing": 0.0, "prev_close": 0.0}
_lock = threading.Lock()
_last_check = 0

def load_state():
    global STATE
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f:
                STATE.update(json.load(f))
    except Exception:
        pass

def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(STATE, f)
    except Exception as e:
        print(f"save_state error: {e}", flush=True)

def bybit_get(path, params):
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
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=15)
    print(f"BYBIT GET {path} -> {r.status_code} {r.text[:300]}", flush=True)
    return r.json()

def bybit_post(path, body):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    body_str = json.dumps(body, separators=(",", ":"))
    to_sign = ts + API_KEY + recv + body_str
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json"
    }
    r = requests.post(BASE_URL + path, headers=headers, data=body_str, timeout=15)
    print(f"BYBIT POST {path} -> {r.status_code} {r.text[:500]}", flush=True)
    return r.json()

def get_balances():
    out = {}
    for acct in ["SPOT", "UNIFIED"]:
        try:
            d = bybit_get("/v5/account/wallet-balance", {"accountType": acct})
            if d.get("retCode") != 0:
                print(f"Saldo check {acct}: retCode={d.get('retCode')} retMsg={d.get('retMsg')}", flush=True)
                continue
            lst = d.get("result", {}).get("list", [])
            if not lst:
                continue
            for coin in lst[0].get("coin", []):
                # tel SPOT + UNIFIED bij elkaar op, niet overschrijven
                out[coin["coin"]] = out.get(coin["coin"], 0) + float(coin.get("walletBalance", 0))
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

def get_klines():
    try:
        r = requests.get(BASE_URL + "/v5/market/kline", params={
            "category": CATEGORY,
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "limit": 100
        }, timeout=15).json()
        print(f"KLINE RAW: {str(r)[:300]}", flush=True)
        if r.get("retCode") != 0:
            print(f"KLINE FAIL retCode={r.get('retCode')} {r.get('retMsg')}", flush=True)
            return []
        lst = r.get("result", {}).get("list", [])
        if not lst:
            print("KLINE LEEG - result list is leeg", flush=True)
            return []
        return list(reversed(lst))  # oud -> nieuw
    except Exception as e:
        print(f"KLINE EXCEPT: {e}", flush=True)
        return []

def calc_atr(klines):
    if len(klines) < ATR_PERIOD + 1:
        return 0
    trs = []
    for i in range(1, len(klines)):
        high = float(klines[i][2])
        low = float(klines[i][3])
        prev_close = float(klines[i-1][4])
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(trs[-ATR_PERIOD:]) / ATR_PERIOD

def place_order(side, qty):
    qty_step = get_qty_step()
    qty = int(qty / qty_step) * qty_step
    body = {"category": CATEGORY, "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty)}
    print(f"Plaats order: {body}", flush=True)
    data = bybit_post("/v5/order/create", body)
    return data

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
        cash = usdc + usdt
        print(f"CHECK: USDC={usdc} USDT={usdt} PEPE={pepe}", flush=True)

        klines = get_klines()
        if len(klines) < ATR_PERIOD + 5:
            print(f"UTBOT: te weinig candles ({len(klines)}) -> HOLD", flush=True)
            return {"status": "hold", "reason": "te weinig candles", "pepe": pepe, "cash": cash}

        atr = calc_atr(klines)
        last_close = float(klines[-1][4])
        prev_close = float(klines[-2][4])
        print(f"Prijs: {last_close} | ATR: {atr} | Prev: {prev_close}", flush=True)

        # sync positie met echt saldo
        in_pos = pepe > 0
        STATE["in_position"] = in_pos

        # --- UTBOT ---
        prev_trailing = STATE.get("trailing", 0.0)
        stored_prev = STATE.get("prev_close", 0.0)

        if prev_trailing == 0.0:
            prev_trailing = last_close - UT_FACTOR * atr if last_close > prev_close else last_close + UT_FACTOR * atr
            stored_prev = prev_close

        if last_close > prev_trailing:
            trailing = max(prev_trailing, last_close - UT_FACTOR * atr)
        else:
            trailing = min(prev_trailing, last_close + UT_FACTOR * atr)

        buy_signal = (not in_pos) and (stored_prev <= prev_trailing) and (last_close > trailing)
        sell_signal = (in_pos) and (stored_prev >= prev_trailing) and (last_close < trailing)

        print(f"UTBOT: prev_trailing={prev_trailing} trailing={trailing} buy={buy_signal} sell={sell_signal} in_pos={in_pos}", flush=True)

        STATE["trailing"] = trailing
        STATE["prev_close"] = last_close
        save_state()

        if buy_signal:
            if cash < 5:
                print(f"Te weinig cash om te kopen: {cash}", flush=True)
                return {"status": "te weinig cash", "cash": cash, "pepe": pepe}
            price = get_price()
            qty = cash / price
            res = place_order("Buy", qty)
            if res.get("retCode") == 0:
                STATE["in_position"] = True
                save_state()
                return {"status": "ok", "action": "BUY", "qty": qty, "price": price}
            return {"status": "buy failed", "result": res}

        if sell_signal:
            if pepe <= 0:
                return {"status": "niks te verkopen"}
            res = place_order("Sell", pepe)
            if res.get("retCode") == 0:
                STATE["in_position"] = False
                save_state()
                return {"status": "ok", "action": "SELL", "qty": pepe, "price": last_close}
            return {"status": "sell failed", "result": res}

        return {"status": "hold", "reason": "geen UTBOT signaal", "trailing": trailing, "pepe": pepe, "cash": cash}
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

load_state()
threading.Thread(target=bot_loop, daemon=True).start()

@app.route("/")
def home():
    return "Pepebot-3 live - UTBOT 4/14"

@app.route("/check")
def check():
    return jsonify(do_check())

@app.route("/test-key")
def test_key():
    bal = get_balances()
    return jsonify({"balances": bal, "base": BASE_URL})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
