import os, time, math, hmac, hashlib, threading, requests
from flask import Flask

# ===== 7-UUR VERSIE + OPKNIP FIX =====
# Hij kocht toen voor 10, nu kan hij ook voor je hele saldo kopen
# omdat hij de order opknipt in stukjes van max 2.500.000

API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE = "https://api.bybit.com"
SYMBOL = "PEPEUSDT"

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot draait"

# ===== LOG + SIGN =====
def log(msg):
    print(msg, flush=True)

def sign_request(msg, ts, recv):
    return hmac.new(API_SECRET.encode(), (ts + API_KEY + recv + msg).encode(), hashlib.sha256).hexdigest()

def bybit_get(path, params_str):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign_request(params_str, ts, recv)
    }
    r = requests.get(f"{BASE}{path}?{params_str}", headers=headers, timeout=10)
    return r.json()

def bybit_post(path, body_dict):
    import json
    ts = str(int(time.time() * 1000))
    recv = "5000"
    body = json.dumps(body_dict)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign_request(body, ts, recv),
        "Content-Type": "application/json"
    }
    r = requests.post(f"{BASE}{path}", headers=headers, data=body, timeout=10)
    return r.json()

# ===== PRIJS / SALDO / KLINES =====
bought_price = 0
bought_qty = 0

def get_prijs():
    try:
        r = requests.get(f"{BASE}/v5/market/tickers", params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        return float(r.json()["result"]["list"][0]["lastPrice"])
    except Exception as e:
        log(f"PRIJS FAIL: {e}")
        return 0.0

def get_saldo(coin):
    try:
        data = bybit_get("/v5/account/wallet-balance", f"accountType=UNIFIED&coin={coin}")
        if data.get("retCode") != 0:
            log(f"Saldo fout {coin}: {data}")
            return 0.0
        for c in data["result"]["list"][0]["coin"]:
            if c["coin"] == coin:
                return float(c["walletBalance"])
        return 0.0
    except Exception as e:
        log(f"Saldo fout {coin}: {e}")
        return 0.0

def get_klines():
    try:
        r = requests.get(f"{BASE}/v5/market/kline", params={"category": "spot", "symbol": SYMBOL, "interval": "15", "limit": "200"}, timeout=10)
        klines = r.json()["result"]["list"]
        if not klines:
            return []
        return klines[::-1]  # oud -> nieuw
    except Exception as e:
        log(f"KLINE FAIL: {e}")
        return []

# ===== ORDERS - 7-UUR VERSIE + OPKNIP =====
def place_buy(symbol, usdc_amount, price):
    global bought_price, bought_qty
    if price <= 0:
        return {}
    qty_total = math.floor((usdc_amount / price) / 1000) * 1000
    if qty_total <= 0:
        return {}

    MAX_PER_ORDER = 2500000
    done_qty = 0
    last_res = {}

    while done_qty < qty_total:
        qty = min(MAX_PER_ORDER, qty_total - done_qty)
        qty = math.floor(qty / 1000) * 1000
        if qty <= 0:
            break
        body = {"category": "spot", "symbol": symbol, "side": "Buy", "orderType": "Market", "qty": str(int(qty))}
        log(f"ORDER Buy {symbol} qty {qty} chunk {done_qty + qty}/{qty_total}")
        last_res = bybit_post("/v5/order/create", body)
        log(f"ORDER Buy resultaat: {last_res}")
        if last_res.get("retCode") != 0:
            break
        done_qty += qty
        if bought_price == 0:
            bought_price = price
        bought_qty = done_qty
        time.sleep(1)
    return last_res

def place_sell(symbol, qty_total, price):
    global bought_price, bought_qty
    if qty_total <= 0:
        return {}
    qty_total = math.floor(qty_total / 1000) * 1000
    MAX_PER_ORDER = 2500000
    done_qty = 0
    last_res = {}
    while done_qty < qty_total:
        qty = min(MAX_PER_ORDER, qty_total - done_qty)
        qty = math.floor(qty / 1000) * 1000
        if qty <= 0:
            break
        body = {"category": "spot", "symbol": symbol, "side": "Sell", "orderType": "Market", "qty": str(int(qty))}
        log(f"ORDER Sell {symbol} qty {qty} chunk {done_qty + qty}/{qty_total}")
        last_res = bybit_post("/v5/order/create", body)
        log(f"ORDER Sell resultaat: {last_res}")
        if last_res.get("retCode") != 0:
            break
        done_qty += qty
        time.sleep(1)
    bought_price = 0
    bought_qty = 0
    return last_res

# ===== AUTO CHECK / UTBOT =====
def auto_check():
    log("=== AUTO CHECK ===")
    prijs = get_prijs()
    usdc = get_saldo("USDC")
    pepe = get_saldo("PEPE")
    klines = get_klines()
    log(f"Prijs {prijs} - USDC {usdc} - PEPE {pepe} - Candles {len(klines)}")

    if len(klines) < 50:
        log(f"UTBOT: te weinig candles ({len(klines)}) -> HOLD")
        return

    closes = [float(k[4]) for k in klines]
    # Simpele UTBOT-vervanger: EMA 10 vs 50, zoals in je 7-uur versie
    def ema(data, p):
        k = 2 / (p + 1)
        v = data[0]
        for x in data[1:]:
            v = x * k + v * (1 - k)
        return v

    ema_fast = ema(closes, 10)
    ema_slow = ema(closes, 50)

    if ema_fast > ema_slow and usdc >= 10:
        log(f"BUY signaal - koop voor {usdc} USDC")
        place_buy(SYMBOL, usdc, prijs)
    elif ema_fast < ema_slow and pepe > 0:
        log(f"SELL signaal - verkoop {pepe} PEPE")
        place_sell(SYMBOL, pepe, prijs)
    else:
        log("HOLD - geen signaal")

def bot_loop():
    while True:
        try:
            auto_check()
        except Exception as e:
            log(f"BOT FOUT: {e}")
        time.sleep(60)

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
