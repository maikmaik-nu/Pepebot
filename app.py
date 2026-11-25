import os, time, hmac, hashlib, json, threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = "https://api." + "b" + "ybit" + ".eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"

API_KEY = next((v for k, v in os.environ.items() if k.endswith("API_KEY")), "").strip()
API_SECRET = next((v for k, v in os.environ.items() if k.endswith("API_SECRET")), "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

UT_KEY = 4
UT_ATR_PERIOD = 14

print(f"KEY LOADED: key_len={len(API_KEY)} secret_len={len(API_SECRET)} base={BASE_URL} symbol={SYMBOL}", flush=True)

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

def api_get(path, params):
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
    print(f"API GET {path} -> {r.status_code} {r.text[:300]}", flush=True)
    try:
        return r.json()
    except Exception:
        return {"retCode": r.status_code, "retMsg": "no json"}

def api_post(path, params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    body = json.dumps(params)
    to_sign = ts + API_KEY + recv + body
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json"
    }
    r = requests.post(BASE_URL + path, json=params, headers=headers, timeout=15)
    print(f"API POST {path} -> {r.status_code} {r.text[:500]}", flush=True)
    try:
        return r.json()
    except Exception:
        return {"retCode": r.status_code, "retMsg": "no json"}

def get_balances():
    out = {}
    try:
        d = api_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        if d.get("retCode") != 0:
            print(f"Saldo UNIFIED: retCode={d.get('retCode')} retMsg={d.get('retMsg')}", flush=True)
            return out
        lst = d.get("result", {}).get("list", [])
        if not lst:
            return out
        for coin in lst[0].get("coin", []):
            out[coin["coin"]] = float(coin.get("walletBalance", 0))
            print(f"Saldo UNIFIED {coin['coin']} = {coin.get('walletBalance')}", flush=True)
    except Exception as e:
        print(f"Saldo check FAIL: {e}", flush=True)
    return out

def get_price():
    r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    return float(r["result"]["list"][0]["lastPrice"])

def get_qty_step():
    r = requests.get(BASE_URL + "/v5/market/instruments-info", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    return float(r["result"]["list"][0]["lotSizeFilter"]["qtyStep"])

def get_klines():
    for interval in ["15", "60", "D"]:
        try:
            r = requests.get(BASE_URL + "/v5/market/kline", params={
                "category": "spot",
                "symbol": SYMBOL,
                "interval": interval,
                "limit": 100
            }, timeout=10)
            print(f"KLINE {interval} -> {r.status_code} {r.text[:200]}", flush=True)
            j = r.json()
            lst = j.get("result", {}).get("list", [])
            if lst:
                print(f"KLINE OK interval={interval} candles={len(lst)}", flush=True)
                return list(reversed(lst))
        except Exception as e:
            print(f"KLINE EXCEPT {interval}: {e}", flush=True)
    print("KLINE LEEG voor alle intervals", flush=True)
    return []

def utbot_signal(candles):
    if len(candles) < UT_ATR_PERIOD + 5:
        return "hold", 0, 0
    closes = [float(c[4]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    tr_list = []
    atr_list = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        tr_list.append(tr)
    for i in range(len(tr_list)):
        if i < UT_ATR_PERIOD:
            atr_list.append(sum(tr_list[:i+1]) / (i+1))
        else:
            atr_list.append((atr_list[-1] * (UT_ATR_PERIOD - 1) + tr_list[i]) / UT_ATR_PERIOD)
    atr = [0] + atr_list
    trailing = [0] * len(closes)
    pos = [0] * len(closes)
    for i in range(len(closes)):
        nloss = UT_KEY * atr[i]
        if i == 0:
            trailing[i] = closes[i] + nloss
            pos[i] = 0
            continue
        if closes[i] > trailing[i-1] and closes[i-1] > trailing[i-1]:
            trailing[i] = max(trailing[i-1], closes[i] - nloss)
        elif closes[i] < trailing[i-1] and closes[i-1] < trailing[i-1]:
            trailing[i] = min(trailing[i-1], closes[i] + nloss)
        elif closes[i] > trailing[i-1]:
            trailing[i] = closes[i] - nloss
        else:
            trailing[i] = closes[i] + nloss
        if closes[i] > trailing[i] and closes[i-1] <= trailing[i-1]:
            pos[i] = 1
        elif closes[i] < trailing[i] and closes[i-1] >= trailing[i-1]:
            pos[i] = -1
        else:
            pos[i] = pos[i-1]
    last_pos = pos[-1]
    prev_pos = pos[-2]
    last_close = closes[-1]
    last_trail = trailing[-1]
    print(f"UTBOT: close={last_close} trail={last_trail} pos={last_pos} prev={prev_pos}", flush=True)
    if last_pos == 1 and prev_pos != 1:
        return "buy", last_close, last_trail
    if last_pos == -1 and prev_pos != -1:
        return "sell", last_close, last_trail
    if last_pos == 1:
        return "hold_buy", last_close, last_trail
    if last_pos == -1:
        return "hold_sell", last_close, last_trail
    return "hold", last_close, last_trail

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
        print(f"CHECK: cash={cash} USDC={usdc} USDT={usdt} PEPE={pepe}", flush=True)
        candles = get_klines()
        if len(candles) < UT_ATR_PERIOD + 5:
            print(f"UTBOT: te weinig candles ({len(candles)}) -> HOLD", flush=True)
            return {"status": "hold", "reason": "te weinig candles", "cash": cash, "pepe": pepe}
        signal, price, trail = utbot_signal(candles)
        print(f"SIGNAL: {signal} price={price} trail={trail}", flush=True)
        if signal == "sell" and pepe > 100000:
            qty_step = get_qty_step()
            qty = round(pepe / qty_step) * qty_step
            print(f"SELL qty={qty} price={price}", flush=True)
            res = api_post("/v5/order/create", {
                "category": "spot",
                "symbol": SYMBOL,
                "side": "Sell",
                "orderType": "Market",
                "qty": str(qty)
            })
            if res.get("retCode") == 0:
                send_telegram(f"PEPE verkocht: {qty} @ {price} - UTBot SELL")
                return {"status": "sold", "qty": qty, "price": price}
            else:
                return {"status": "fail", "error": res.get("retMsg")}
        if signal == "buy" and pepe < 100000 and cash >= 5:
            qty_step = get_qty_step()
            buy_amount = min(cash * 0.9, 90)
            qty = round((buy_amount / price) / qty_step) * qty_step
            print(f"BUY qty={qty} price={price}", flush=True)
            res = api_post("/v5/order/create", {
                "category": "spot",
                "symbol": SYMBOL,
                "side": "Buy",
                "orderType": "Market",
                "qty": str(qty)
            })
            if res.get("retCode") == 0:
                send_telegram(f"PEPE gekocht: {qty} @ {price} - UTBot BUY")
                return {"status": "ok", "qty": qty, "price": price}
            else:
                return {"status": "fail", "error": res.get("retMsg")}
        return {"status": "hold", "signal": signal, "cash": cash, "pepe": pepe, "price": price}
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
    return jsonify({"key_len": len(API_KEY), "secret_len": len(API_SECRET), "base": BASE_URL, "balances": bal, "PEPEUSDT_price": price})

@app.route("/check")
def check():
    return jsonify(do_check())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
