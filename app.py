import os, time, hmac, hashlib, json, threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

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
    print(f"【entity-BYBIT¦canonical_name=BYBIT】 GET {path} -> {r.status_code} {r.text[:300]}", flush=True)
    return r.json()

def 【entity-bybit_post¦canonical_name=BYBIT】(path, params):
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
    print(f"【entity-BYBIT¦canonical_name=BYBIT】 POST {path} -> {r.status_code} {r.text[:500]}", flush=True)
    return r.json()

def get_balances():
    out = {}
    try:
        d = bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
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
    try:
        r = requests.get(BASE_URL + "/v5/market/kline", params={
            "category": "spot",
            "symbol": SYMBOL,
            "interval": "15",
            "limit": 50
        }, timeout=10).json()
        print(f"KLINE RAW: {str(r)[:300]}", flush=True)
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

        # FIX KLINE: eerst checken of er wel candles zijn
        candles = get_klines()
        if len(candles) < 20:
            print(f"UTBOT: te weinig candles ({len(candles)}) -> HOLD", flush=True)
            return {"status": "hold", "reason": "te weinig candles", "cash": cash, "pepe": pepe}

        # Als je al PEPE hebt, niet opnieuw kopen
        if pepe > 100000:
            print(f"HOLD: al PEPE in bezit ({pepe}), niet opnieuw kopen", flush=True)
            return {"status": "hold", "reason": "al PEPE in bezit", "cash": cash, "pepe": pepe}

        if cash < 5:
            print(f"HOLD: te weinig cash ({cash})", flush=True)
            return {"status": "hold", "reason": "te weinig cash", "cash": cash, "pepe": pepe}

        price = get_price()
        qty_step = get_qty_step()
        # Koop voor max 90 USDC, nooit alles
        buy_amount = min(cash * 0.9, 90)
        qty = round((buy_amount / price) / qty_step) * qty_step
        print(f"BUY qty={qty} price={price}", flush=True)

        res = 【entity-bybit_post¦canonical_name=BYBIT】("/v5/order/create", {
            "category": "spot",
            "symbol": SYMBOL,
            "side": "Buy",
            "orderType": "Market",
            "qty": str(qty)
        })
        if res.get("retCode") == 0:
            send_telegram(f"PEPE gekocht: {qty} @ {price}")
            return {"status": "ok", "qty": qty, "price": price}
        else:
            return {"status": "fail", "error": res.get("retMsg"), "cash": cash, "pepe": pepe}

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
