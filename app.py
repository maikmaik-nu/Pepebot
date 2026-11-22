import os, time, hmac, hashlib, threading, logging
import requests
from flask import Flask, jsonify

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pepebot")

# === CONFIG ===
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE_URL = os.getenv("BASE_URL", "https://api.bybit.eu").rstrip("/")
CATEGORY = os.getenv("CATEGORY", "spot")
SYMBOL = os.getenv("SYMBOL", "PEPEUSDT")
TRADE_USDT = float(os.getenv("TRADE_USDT", "10"))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "60"))

app = Flask(__name__)
state = {"last_check": None, "last_error": None, "in_position": False}

# === 【entity-BYBIT¦canonical_name=BYBIT】 ===
def bybit_get(path, params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    p = dict(params)
    p["api_key"] = API_KEY
    p["timestamp"] = ts
    p["recv_window"] = recv
    query = "&".join(f"{k}={p[k]}" for k in sorted(p))
    sign = hmac.new(API_SECRET.encode(), f"{ts}{API_KEY}{recv}{query}".encode(), hashlib.sha256).hexdigest()
    url = f"{BASE_URL}{path}?{query}"
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
    }
    r = requests.get(url, headers=headers, timeout=15)
    log.info(f"【entity-BYBIT¦canonical_name=BYBIT】 {path} -> {r.status_code} {r.text[:300]}")
    try:
        return r.json()
    except Exception as e:
        return {"retCode": -1, "retMsg": f"no-json: {e} raw:{r.text[:200]}"}

def bybit_post(path, params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    body = "&".join(f"{k}={params[k]}" for k in sorted(params))
    sign = hmac.new(API_SECRET.encode(), f"{ts}{API_KEY}{recv}{body}".encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json",
    }
    r = requests.post(f"{BASE_URL}{path}", headers=headers, json=params, timeout=15)
    log.info(f"BYBIT POST {path} -> {r.status_code} {r.text[:300]}")
    try:
        return r.json()
    except Exception as e:
        return {"retCode": -1, "retMsg": f"no-json: {e} raw:{r.text[:200]}"}

def get_balance():
    # Probeer SPOT, dan UNIFIED
    for acc in ["SPOT", "UNIFIED"]:
        res = bybit_get("/v5/account/wallet-balance", {"accountType": acc})
        if res.get("retCode") == 0:
            usdt = 0.0
            pepe = 0.0
            for lst in res.get("result", {}).get("list", []):
                for c in lst.get("coin", []):
                    if c.get("coin") == "USDT":
                        usdt = float(c.get("walletBalance", 0) or 0)
                    if c.get("coin") == "PEPE":
                        pepe = float(c.get("walletBalance", 0) or 0)
            return {"account": acc, "usdt": usdt, "pepe": pepe, "retCode": 0}
        else:
            last = res
    return {"account": "NONE", "usdt": 0.0, "pepe": 0.0, "retCode": last.get("retCode", -1), "retMsg": last.get("retMsg", "")}

def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
        data = r.json()
        lst = data.get("result", {}).get("list", [])
        if lst:
            return float(lst[0].get("lastPrice", 0) or 0)
    except Exception as e:
        log.warning(f"price fail: {e}")
    return 0.0

def place_order(side, qty):
    return bybit_post("/v5/order/create", {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": side,
        "orderType": "Market",
        "qty": str(qty),
    })

# === BOT LOOP ===
def bot_loop():
    while True:
        try:
            bal = get_balance()
            price = get_price()
            state["last_check"] = time.strftime("%Y-%m-%d %H:%M:%S")
            log.info(f"CHECK: USDT={bal['usdt']} PEPE={bal['pepe']} price={price} in_position={state['in_position']} retCode={bal['retCode']} acc={bal['account']}")

            if bal["retCode"] != 0:
                state["last_error"] = f"retCode={bal['retCode']} {bal.get('retMsg','')}"
                log.warning(f"Bot stopt: geen saldo / geen rechten retCode={bal['retCode']}")
                time.sleep(CHECK_INTERVAL)
                continue

            state["last_error"] = None

            # Simpele logica: koop als we geen PEPE hebben en genoeg USDT, verkoop als we PEPE hebben
            if not state["in_position"] and bal["usdt"] >= TRADE_USDT and price > 0:
                qty = int(TRADE_USDT / price)
                if qty > 0:
                    res = place_order("Buy", qty)
                    log.info(f"BUY {qty} -> {res}")
                    if res.get("retCode") == 0:
                        state["in_position"] = True
            elif state["in_position"] and bal["pepe"] > 0:
                qty = int(bal["pepe"])
                if qty > 0:
                    res = place_order("Sell", qty)
                    log.info(f"SELL {qty} -> {res}")
                    if res.get("retCode") == 0:
                        state["in_position"] = False
            # Sync positie met echt saldo
            if bal["pepe"] > 0:
                state["in_position"] = True
            elif bal["pepe"] == 0 and bal["usdt"] > 0:
                state["in_position"] = False

        except Exception as e:
            state["last_error"] = str(e)
            log.exception(f"bot error: {e}")
        time.sleep(CHECK_INTERVAL)

# === ROUTES ===
@app.route("/")
def home():
    return jsonify({"status": "live", "symbol": SYMBOL, "in_position": state["in_position"], "last_check": state["last_check"]})

@app.route("/test-key")
def test_key():
    bal = get_balance()
    price = get_price()
    return jsonify({
        "ok": bal["retCode"] == 0,
        "api_key_set": bool(API_KEY),
        "api_secret_set": bool(API_SECRET),
        "base_url": BASE_URL,
        "category": CATEGORY,
        "symbol": SYMBOL,
        "account": bal["account"],
        "balances": {"USDT": bal["usdt"], "PEPE": bal["pepe"]},
        "price": price,
        "retCode": bal["retCode"],
        "retMsg": bal.get("retMsg", ""),
        "last_error": state["last_error"],
    })

@app.route("/status")
def status():
    return jsonify(state)

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
