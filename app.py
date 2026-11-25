import os, time, hmac, hashlib, json, threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# --- BASIS 19:00, GOED GEHOUDEN ---
BASE_URL = "https://api.bybit.eu"
CATEGORY = "spot"

# --- AANPASSING VANAVOND: PEPEUSDC + USDC, niet meer USDT ---
SYMBOL = "PEPEUSDC"
QUOTE = "USDC"
BUY_AMOUNT = 10.0  # USDC per koop

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
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        print(f"TELEGRAM FAIL: {e}", flush=True)

def make_headers(ts, recv, sig):
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json"
    }

def bybit_get(path, params):
    # 19:00-fix gehouden: alleen echte params in query
    if not API_KEY or not API_SECRET:
        print("Balance fout: geen API key in Render", flush=True)
        return {"retCode": -1, "retMsg": "no api key"}
    try:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        query = "&".join(f"{k}={params[k]}" for k in sorted(params))
        to_sign = ts + API_KEY + recv + query
        sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
        r = requests.get(BASE_URL + path, params=params, headers=make_headers(ts, recv, sig), timeout=15)
        print(f"BYBIT GET {path} -> {r.status_code} {r.text[:300]}", flush=True)
        return r.json()
    except Exception as e:
        print(f"BYBIT GET FAIL {path}: {e}", flush=True)
        return {"retCode": -1, "retMsg": str(e)}

def bybit_post(path, body):
    # VANAVOND-FIX 170003: echte POST, sign met body, alleen toegestane velden
    if not API_KEY or not API_SECRET:
        return {"retCode": -1, "retMsg": "no api key"}
    try:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        body_str = json.dumps(body, separators=(",", ":"))
        to_sign = ts + API_KEY + recv + body_str
        sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
        r = requests.post(BASE_URL + path, data=body_str, headers=make_headers(ts, recv, sig), timeout=15)
        print(f"BYBIT POST {path} body={body_str} -> {r.status_code} {r.text[:500]}", flush=True)
        return r.json()
    except Exception as e:
        print(f"BYBIT POST FAIL {path}: {e}", flush=True)
        return {"retCode": -1, "retMsg": str(e)}

def get_balances():
    # VANAVOND-FIX: SPOT + UNIFIED + FUNDING, geen coin-param, list veilig
    out = {}
    for acct in ["SPOT", "UNIFIED", "FUNDING"]:
        try:
            d = bybit_get("/v5/account/wallet-balance", {"accountType": acct})
            if d.get("retCode") != 0:
                print(f"Saldo {acct}: retCode={d.get('retCode')} retMsg={d.get('retMsg')}", flush=True)
                continue
            lst = d.get("result", {}).get("list", [])
            if not lst:
                continue
            coins = lst[0].get("coin", [])
            if not coins:
                continue
            for coin in coins:
                name = coin.get("coin")
                if not name:
                    continue
                try:
                    val = float(coin.get("walletBalance", 0) or 0)
                except Exception:
                    val = 0
                if val > out.get(name, 0):
                    out[name] = val
                print(f"Saldo {acct} {name} = {val}", flush=True)
        except Exception as e:
            print(f"Saldo {acct} FAIL: {e}", flush=True)
    return out

def get_price():
    try:
        r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
        return float(r["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print(f"PRICE FAIL: {e}", flush=True)
        raise

def get_qty_step():
    try:
        r = requests.get(BASE_URL + "/v5/market/instruments-info", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
        lot = r["result"]["list"][0].get("lotSizeFilter", {})
        return float(lot.get("qtyStep", 1)), float(lot.get("minOrderQty", 0))
    except Exception as e:
        print(f"QTYSTEP FAIL: {e}", flush=True)
        return 1.0, 0.0

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
        print(f"Check: USDT {bal.get('USDT', 0)} USDC {usdc} PEPE {pepe}", flush=True)

        if usdc < 5:
            return {"status": "te weinig USDC", "usdc": usdc, "pepe": pepe}

        price = get_price()
        qty_step, min_qty = get_qty_step()

        # VANAVOND-FIX: naar beneden afronden, nooit naar boven
        raw_qty = BUY_AMOUNT / price
        qty = int(raw_qty / qty_step) * qty_step
        if qty_step > 0 and qty < min_qty:
            return {"status": "qty te klein", "qty": qty, "min_qty": min_qty, "price": price}

        print(f"ORDER Buy {SYMBOL} {qty} prijs {price} USDC {usdc}", flush=True)

        # VANAVOND-FIX: alleen deze 5 velden, niks anders = geen 170003 meer
        order = bybit_post("/v5/order/create", {
            "category": "spot",
            "symbol": SYMBOL,
            "side": "Buy",
            "orderType": "Market",
            "qty": str(qty)
        })

        if order.get("retCode") != 0:
            print(f"BUY afgewezen: {order.get('retCode')} {order.get('retMsg')}", flush=True)
            return {"status": "buy afgewezen", "order": order, "usdc": usdc, "pepe": pepe}

        send_telegram(f"Gekocht: {qty} PEPE voor {BUY_AMOUNT} USDC @ {price}")
        return {"status": "ok", "usdc": usdc, "pepe": pepe, "price": price, "qty": qty, "order": order}
    except Exception as e:
        print(f"CHECK FAIL: {e}", flush=True)
        return {"status": "check fail", "error": str(e)}
    finally:
        _lock.release()

def bot_loop():
    time.sleep(10)
    print("Bot loop gestart - definitieve versie - PEPEUSDC / SPOT+UNIFIED+FUNDING", flush=True)
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
    return jsonify({"api_key_set": bool(API_KEY), "balances": bal, "price": price, "symbol": SYMBOL})

@app.route("/check")
def check():
    return jsonify(do_check())

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
