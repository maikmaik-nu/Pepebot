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

print(f"KEY LOADED: key_len={len(API_KEY)} secret_len={len(API_SECRET)} base={BASE_URL} symbol={SYMBOL} category={CATEGORY}", flush=True)

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
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json"
    }
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=15)
    print(f"【entity-BYBIT¦canonical_name=Bybit】 GET {path} -> {r.status_code} {r.text[:300]}", flush=True)
    return r.json()

def 【entity-bybit_post¦canonical_name=Bybit】(path, body):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    payload = json.dumps(body)
    to_sign = ts + API_KEY + recv + payload
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json"
    }
    r = requests.post(BASE_URL + path, data=payload, headers=headers, timeout=15)
    print(f"【entity-BYBIT¦canonical_name=Bybit】 POST {path} -> {r.status_code} {r.text[:500]}", flush=True)
    return r.json()

def get_balances():
    out = {}
    for acct in ["UNIFIED", "SPOT"]:
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

def get_klines():
    try:
        r = requests.get(BASE_URL + "/v5/market/kline", params={
            "category": "spot",
            "symbol": SYMBOL,
            "interval": "15",
            "limit": 50
        }, timeout=10).json()
        if r.get("retCode") != 0:
            print(f"KLINE FAIL retCode={r.get('retCode')} {r.get('retMsg')}", flush=True)
            return []
        lst = r.get("result", {}).get("list", [])
        if not lst:
            print("KLINE LEEG - result list is leeg", flush=True)
            return []
        return lst
    except Exception as e:
        print(f"KLINE EXCEPT: {e}", flush=True)
        return []

def get_utbot_trend(candles):
    # Simpele UTBot-logica: prijs boven gemiddelde van laatste 20 candles = buy, anders sell
    if len(candles) < 20:
        print(f"UTBOT: te weinig candles ({len(candles)}) -> HOLD", flush=True)
        return "hold"
    closes = [float(c[4]) for c in candles]
    avg = sum(closes[-20:]) / 20
    price = closes[-1]
    trend = "buy" if price > avg else "sell"
    print(f"UTBOT: trend={trend} price={price} avg20={avg}", flush=True)
    return trend

def place_order(qty):
    qty = float(qty)
    body = {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": "Buy",
        "orderType": "Market",
        "qty": str(qty)
    }
    return 【entity-bybit_post¦canonical_name=Bybit】("/v5/order/create", body)

def buy_all(usdc, price):
    spend = round(usdc * 0.999, 4)
    total_qty = int(spend / price)
    print(f"KOOP ALLES: spend={spend} qty={total_qty} price={price}", flush=True)

    chunk = 100000
    done = 0
    poging = 0

    while done < total_qty:
        qty = min(chunk, total_qty - done)
        poging += 1
        print(f"ORDER Buy {SYMBOL} qty {qty} chunk {qty}/{total_qty} poging {poging}", flush=True)
        r = place_order(qty)
        print(f"ORDER: {r}", flush=True)

        if r.get("retCode") == 0:
            done += qty
            print(f"ORDER OK: done {done} van {total_qty}", flush=True)
        elif r.get("retCode") == 170381:
            chunk = chunk // 2
            print(f"170381 -> chunk halveren naar {chunk}", flush=True)
            if chunk < 1000:
                print("ORDER STOP: chunk te klein geworden", flush=True)
                break
        else:
            print(f"ORDER STOP: retCode={r.get('retCode')} {r.get('retMsg')}", flush=True)
            break

    print(f"ORDER KLAAR: done {done} van {total_qty}", flush=True)
    return done

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
        total = usdc + usdt + (pepe * get_price() if pepe else 0)
        print(f"totalEquity = {total}", flush=True)
        print(f"CHECK: cash={usdc} PEPE={pepe}", flush=True)

        candles = get_klines()
        if len(candles) < 20:
            print(f"UTBOT: te weinig candles ({len(candles)}) -> HOLD", flush=True)
            return {"status": "hold", "reason": "te weinig candles"}

        trend = get_utbot_trend(candles)
        price = get_price()

        if trend == "buy" and usdc >= 5:
            done = buy_all(usdc, price)
            send_telegram(f"BUY PEPEUSDC: {done} gekocht op {price}")
            return {"status": "buy", "done": done, "price": price}
        else:
            print(f"HOLD: trend={trend} usdc={usdc}", flush=True)
            return {"status": "hold", "trend": trend, "usdc": usdc, "pepe": pepe}

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
    return "Pepebot-3 live"

@app.route("/test-key")
def test_key():
    bal = get_balances()
    try:
        price = get_price()
    except Exception as e:
        price = f"FAIL {e}"
    return jsonify({"key_len": len(API_KEY), "secret_len": len(API_SECRET), "base": BASE_URL, "balances": bal, "PEPEUSDC_price": price})

@app.route("/check")
def check():
    return jsonify(do_check())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
