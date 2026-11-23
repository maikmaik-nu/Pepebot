import os, time, hmac, hashlib, threading, requests, json, traceback
from flask import Flask, jsonify

app = Flask(__name__)

# ============================================================
# 1. CONFIG - 【entity-BYBIT¦canonical_name=BYBIT】 EU - NU MET USDC
# ============================================================
BASE_URL = "https://api.bybit.eu"
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()

# DIT WAS DE FOUT: eerst stond hier PEPEUSDT + USDT
# Jij hebt USDC, geen USDT - zie je screenshot: USDT 0.00, USDC 100.7091
SYMBOL = "PEPEUSDC"
BASE_COIN = "PEPE"
QUOTE_COIN = "USDC"

TRADE_USDC = 20.0        # Koop voor 20 USDC per keer
MIN_USDC = 10.0          # Onder 10 USDC doen we niks
TAKE_PROFIT_PCT = 5.0    # Verkoop bij +5%
STOP_LOSS_PCT = -3.0     # Verkoop bij -3%
CHECK_EVERY_SEC = 60

RECV_WINDOW = "5000"

STATUS = {
    "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    "last_check": "nog niet",
    "usdc": 0,
    "usdt": 0,
    "pepe": 0,
    "pepe_price": 0,
    "in_position": False,
    "entry_price": 0,
    "pnl_pct": 0,
    "last_action": "opstarten...",
    "last_error": None,
    "total_trades": 0,
    "total_buys": 0,
    "total_sells": 0,
}

_bot_started = False
_bot_lock = threading.Lock()

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

# ============================================================
# 2. SIGNATURE - BYBIT V5
# ============================================================
def sign(timestamp, api_key, recv_window, payload_str):
    param_str = str(timestamp) + api_key + str(recv_window) + payload_str
    return hmac.new(API_SECRET.encode("utf-8"), param_str.encode("utf-8"), hashlib.sha256).hexdigest()

def bybit_get(path, params):
    timestamp = int(time.time() * 1000)
    query = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    signature = sign(timestamp, API_KEY, RECV_WINDOW, query)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": str(timestamp),
        "X-BAPI-RECV-WINDOW": str(RECV_WINDOW),
        "Content-Type": "application/json"
    }
    r = requests.get(f"{BASE_URL}{path}", params=params, headers=headers, timeout=15)
    log(f"GET {path} {params} -> {r.status_code} {r.text[:600]}")
    return r.json()

def bybit_post(path, body_dict):
    timestamp = int(time.time() * 1000)
    body_str = json.dumps(body_dict, separators=(',', ':'))
    signature = sign(timestamp, API_KEY, RECV_WINDOW, body_str)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": str(timestamp),
        "X-BAPI-RECV-WINDOW": str(RECV_WINDOW),
        "Content-Type": "application/json"
    }
    r = requests.post(f"{BASE_URL}{path}", data=body_str, headers=headers, timeout=15)
    log(f"POST {path} {body_str} -> {r.status_code} {r.text[:600]}")
    return r.json()

# ============================================================
# 3. SALDO - SPOT & MARGIN, USDC + PEPE + USDT (ter controle)
# ============================================================
def get_balances():
    usdc = 0.0
    usdt = 0.0
    pepe = 0.0

    for coin_name in ["USDC", "USDT", "PEPE"]:
        try:
            data = bybit_get("/v5/account/wallet-balance", {"accountType": "SPOT", "coin": coin_name})
            if data.get("retCode") != 0:
                log(f"SALDO FOUT {coin_name}: {data}")
                if coin_name == "USDC":
                    return 0, 0, 0, data
                continue
            coins = data["result"]["list"][0]["coin"]
            for c in coins:
                val = float(c.get("walletBalance", 0) or 0)
                if c["coin"] == "USDC":
                    usdc = val
                elif c["coin"] == "USDT":
                    usdt = val
                elif c["coin"] == "PEPE":
                    pepe = val
        except Exception as e:
            log(f"SALDO EXCEPTION {coin_name}: {e}")
            if coin_name == "USDC":
                return 0, 0, 0, {"error": str(e)}

    log(f"SALDO OK: USDC={usdc} USDT={usdt} PEPE={pepe}")
    return usdc, usdt, pepe, None

# ============================================================
# 4. PRIJS - PEPEUSDC, PUBLIC
# ============================================================
def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        data = r.json()
        if data.get("retCode") == 0:
            price = float(data["result"]["list"][0]["lastPrice"])
            log(f"PRIJS {SYMBOL} = {price}")
            return price
        log(f"PRIJS FOUT: {data}")
    except Exception as e:
        log(f"PRIJS EXCEPTION: {e}")
    return 0

# ============================================================
# 5. ORDER PLAATSEN - SPOT MARKET
# ============================================================
def place_order(side, qty_or_quote):
    try:
        if side == "Buy":
            body = {"category": "spot", "symbol": SYMBOL, "side": "Buy", "orderType": "Market", "quoteQty": str(qty_or_quote)}
        else:
            body = {"category": "spot", "symbol": SYMBOL, "side": "Sell", "orderType": "Market", "qty": str(int(qty_or_quote))}
        log(f"ORDER PROBEREN: {side} {qty_or_quote} {SYMBOL}")
        result = bybit_post("/v5/order/create", body)
        if result.get("retCode") == 0:
            log(f"ORDER GELUKT: {side} {result}")
            STATUS["total_trades"] += 1
            if side == "Buy":
                STATUS["total_buys"] += 1
            else:
                STATUS["total_sells"] += 1
            return True, result
        else:
            log(f"ORDER GEFAALD: {result}")
            STATUS["last_error"] = str(result)
            return False, result
    except Exception as e:
        log(f"ORDER EXCEPTION: {e} {traceback.format_exc()}")
        STATUS["last_error"] = str(e)
        return False, {"error": str(e)}

# ============================================================
# 6. BOT LOGICA
# ============================================================
def bot_loop():
    log(f"Bot gestart. KEY: {bool(API_KEY)} SECRET: {bool(API_SECRET)} SYMBOL: {SYMBOL}")
    entry_price = 0
    while True:
        try:
            usdc, usdt, pepe, err = get_balances()
            price = get_price()

            STATUS["last_check"] = time.strftime("%Y-%m-%d %H:%M:%S")
            STATUS["usdc"] = usdc
            STATUS["usdt"] = usdt
            STATUS["pepe"] = pepe
            STATUS["pepe_price"] = price

            if err is not None:
                STATUS["last_error"] = str(err)
                STATUS["last_action"] = f"Saldo fout, wacht 60 sec - {err.get('retCode')} {err.get('retMsg')}"
                log(f"WACHT: saldo fout {err}")
                time.sleep(CHECK_EVERY_SEC)
                continue

            if price == 0:
                STATUS["last_action"] = "Prijs is 0, wacht 60 sec"
                time.sleep(CHECK_EVERY_SEC)
                continue

            pepe_value = pepe * price
            in_position = pepe_value > 5.0
            STATUS["in_position"] = in_position

            if in_position:
                if entry_price == 0:
                    entry_price = price
                    STATUS["entry_price"] = entry_price
                pnl_pct = ((price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
                STATUS["pnl_pct"] = round(pnl_pct, 2)
                STATUS["entry_price"] = entry_price
                log(f"IN POSITIE: PEPE={pepe} waarde=${pepe_value:.2f} entry={entry_price} nu={price} pnl={pnl_pct:.2f}%")

                if pnl_pct >= TAKE_PROFIT_PCT:
                    STATUS["last_action"] = f"TAKE PROFIT +{pnl_pct:.2f}% - verkoop {pepe} PEPE"
                    ok, res = place_order("Sell", pepe)
                    if ok:
                        entry_price = 0
                        STATUS["entry_price"] = 0
                        STATUS["pnl_pct"] = 0
                    time.sleep(CHECK_EVERY_SEC)
                    continue

                if pnl_pct <= STOP_LOSS_PCT:
                    STATUS["last_action"] = f"STOP LOSS {pnl_pct:.2f}% - verkoop {pepe} PEPE"
                    ok, res = place_order("Sell", pepe)
                    if ok:
                        entry_price = 0
                        STATUS["entry_price"] = 0
                        STATUS["pnl_pct"] = 0
                    time.sleep(CHECK_EVERY_SEC)
                    continue

                STATUS["last_action"] = f"In positie, wachten. PnL {pnl_pct:.2f}%"
                log("WACHT: in positie, TP/SL nog niet geraakt")

            else:
                STATUS["pnl_pct"] = 0
                STATUS["entry_price"] = 0
                log(f"GEEN POSITIE: USDC={usdc} USDT={usdt} prijs={price}")

                if usdc < MIN_USDC:
                    STATUS["last_action"] = f"Te weinig USDC ({usdc}), minimaal {MIN_USDC}. USDT staat op {usdt} - converteer USDT naar USDC als je USDT hebt."
                    log(f"WACHT: te weinig USDC {usdc} < {MIN_USDC}")
                    time.sleep(CHECK_EVERY_SEC)
                    continue

                buy_amount = min(TRADE_USDC, usdc)
                log(f"KOOP: koop voor {buy_amount} USDC")
                STATUS["last_action"] = f"KOOP voor {buy_amount} USDC"
                ok, res = place_order("Buy", buy_amount)
                if ok:
                    entry_price = price
                    STATUS["entry_price"] = entry_price
                    log(f"GEKOCHT op {price}")

            time.sleep(CHECK_EVERY_SEC)

        except Exception as e:
            log(f"BOT LOOP FOUT, GEEN STOP: {e} {traceback.format_exc()}")
            STATUS["last_error"] = str(e)
            STATUS["last_action"] = f"Loop fout, herstart over 60 sec: {e}"
            time.sleep(CHECK_EVERY_SEC)

# ============================================================
# 7. ROUTES
# ============================================================
@app.route("/")
def home():
    return "Pepebot live - Bybit EU - SPOT - PEPEUSDC"

@app.route("/status")
def status():
    return jsonify(STATUS)

@app.route("/saldo")
def saldo_route():
    usdc, usdt, pepe, err = get_balances()
    price = get_price()
    return jsonify({"usdc": usdc, "usdt": usdt, "pepe": pepe, "pepe_value_usdc": pepe * price if price else 0, "price": price, "error": err, "symbol": SYMBOL})

@app.route("/test-key")
def test_key():
    usdc, usdt, pepe, err = get_balances()
    return jsonify({"api_key_set": bool(API_KEY), "api_secret_set": bool(API_SECRET), "base_url": BASE_URL, "accountType_used": "SPOT", "symbol": SYMBOL, "usdc": usdc, "usdt": usdt, "pepe": pepe, "error": err, "price": get_price()})

@app.route("/health")
def health():
    return jsonify({"status": "ok", "key_loaded": bool(API_KEY)})

def start_bot_once():
    global _bot_started
    with _bot_lock:
        if not _bot_started:
            _bot_started = True
            threading.Thread(target=bot_loop, daemon=True).start()
            log("Bot thread gestart")

start_bot_once()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
