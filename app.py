import os, time, hmac, hashlib, json, threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDC"
CATEGORY = "spot"
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# UTBOT 4-14 - alleen dit, geen Supertrend
UT_KEY = 4
UT_ATR = 14
MAX_PER_ORDER = 2500000

print(f"KEY LOADED: key_len={len(API_KEY)} secret_len={len(API_SECRET)}", flush=True)

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

def bybit_get(path, params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    to_sign = ts + API_KEY + recv + query
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sig, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "Content-Type": "application/json"}
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=15)
    return r.json()

def bybit_post(path, params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    body = json.dumps(params)
    to_sign = ts + API_KEY + recv + body
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-SIGN": sig, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "Content-Type": "application/json"}
    r = requests.post(BASE_URL + path, data=body, headers=headers, timeout=15)
    print(f" BYBIT POST {path} -> {r.status_code} {r.text[:500]}", flush=True)
    return r.json()

def get_balances():
    out = {}
    for acct in ["UNIFIED", "SPOT"]:
        try:
            d = bybit_get("/v5/account/wallet-balance", {"accountType": acct})
            if d.get("retCode") != 0:
                print(f"Saldo check {acct}: {d.get('retMsg')}", flush=True)
                continue
            lst = d.get("result", {}).get("list", [])
            if not lst:
                continue
            for coin in lst[0].get("coin", []):
                out[coin["coin"]] = float(coin.get("walletBalance", 0))
            if out:
                break
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
    for attempt in range(2):
        try:
            r = requests.get(BASE_URL + "/v5/market/kline", params={"category": CATEGORY, "symbol": SYMBOL, "interval": "15", "limit": "200"}, timeout=10)
            lst = r.json()["result"]["list"]
            if lst:
                return lst[::-1]
        except Exception as e:
            print(f"KLINE FAIL poging {attempt + 1}: {e}", flush=True)
            time.sleep(2)
    return []

def get_utbot(candles):
    if len(candles) < 50:
        return "hold"
    closes = [float(c[4]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr = sum(trs[:UT_ATR]) / UT_ATR
    for tr in trs[UT_ATR:]:
        atr = (atr * (UT_ATR-1) + tr) / UT_ATR
    stop = 0
    pos = 0
    for i in range(1, len(closes)):
        if closes[i] > stop and closes[i-1] > stop:
            stop = max(stop, closes[i] - UT_KEY * atr)
        elif closes[i] < stop and closes[i-1] < stop:
            stop = min(stop, closes[i] + UT_KEY * atr)
        elif closes[i] > stop:
            stop = closes[i] - UT_KEY * atr
        else:
            stop = closes[i] + UT_KEY * atr
        if closes[i] > stop:
            pos = 1
        elif closes[i] < stop:
            pos = -1
    return "buy" if pos == 1 else "sell" if pos == -1 else "hold"

def place_order_chunked(side, qty_total, qty_step):
    qty_total = int(qty_total)
    if qty_total <= 0:
        return {}
    done = 0
    last_res = {}
    while done < qty_total:
        chunk = min(MAX_PER_ORDER, qty_total - done)
        chunk = int(chunk // qty_step * qty_step)
        if chunk <= 0:
            break
        last_res = bybit_post("/v5/order/create", {"category": CATEGORY, "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(chunk), "marketUnit": "baseCoin"})
        if last_res.get("retCode") != 0:
            break
        done += chunk
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
        pepe = bal.get("PEPE", 0)
        price = get_price()
        klines = get_klines()
        signal = get_utbot(klines)
        print(f"CHECK: USDC={usdc} PEPE={pepe} PRICE={price} UTBOT={signal} Candles={len(klines)}", flush=True)

        if len(klines) < 50:
            return {"status": "hold", "reason": "te weinig candles"}

        if signal == "buy" and pepe < 1000 and usdc >= 5:
            qty_step = get_qty_step()
            qty = round(((usdc * 0.99) / price) / qty_step) * qty_step
            print(f"BUY qty={qty} price={price} usdc={usdc}", flush=True)
            res = place_order_chunked("Buy", qty, qty_step)
            if res.get("retCode") == 0:
                send_telegram(f"UTBOT BUY: {qty} PEPE voor {usdc:.2f} USDC @ {price}")
                return {"status": "buy", "qty": qty, "price": price}
            return {"status": "buy_failed", "res": res}

        if signal == "sell" and pepe >= 1000:
            qty_step = get_qty_step()
            qty = round(pepe / qty_step) * qty_step
            print(f"SELL qty={qty} price={price}", flush=True)
            res = place_order_chunked("Sell", qty, qty_step)
            if res.get("retCode") == 0:
                send_telegram(f"UTBOT SELL: {qty} PEPE @ {price}")
                return {"status": "sell", "qty": qty, "price": price}
            return {"status": "sell_failed", "res": res}

        return {"status": "hold", "signal": signal, "usdc": usdc, "pepe": pepe, "price": price}
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
        time.sleep(60)

threading.Thread(target=bot_loop, daemon=True).start()

@app.route("/")
def home():
    return "Pepebot-3 live - UTBot 4-14"

@app.route("/check")
def check():
    return jsonify(do_check())

@app.route("/status")
def status():
    bal = get_balances()
    try:
        price = get_price()
    except Exception as e:
        price = f"FAIL {e}"
    return jsonify({"balances": bal, "price": price, "utbot": get_utbot(get_klines())})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
