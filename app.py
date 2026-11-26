import os, time, json, math, hmac, hashlib, threading, requests
from flask import Flask, jsonify

app = Flask(__name__)

BASE = "https://api.bybit.eu"
SYMBOL = "PEPEUSDC"
CATEGORY = "spot"

API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# UTBOT 4-14
UT_KEY = 4
UT_ATR = 14
# SUPERTREND
ST_ATR = 10
ST_MULT = 3.0
MAX_PER_ORDER = 2500000

bot_state = {"in_positie": False}
_lock = threading.Lock()
_last_check = 0

def log(m):
    print(m, flush=True)

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        log(f"TELEGRAM FAIL: {e}")

def sign_request(msg, ts, recv):
    return hmac.new(API_SECRET.encode(), (ts + API_KEY + recv + msg).encode(), hashlib.sha256).hexdigest()

def bybit_get(path, params_str):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    headers = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "X-BAPI-SIGN": sign_request(params_str, ts, recv)}
    r = requests.get(f"{BASE}{path}?{params_str}", headers=headers, timeout=15)
    return r.json()

def bybit_post(path, body_dict):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    body = json.dumps(body_dict)
    headers = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "X-BAPI-SIGN": sign_request(body, ts, recv), "Content-Type": "application/json"}
    r = requests.post(f"{BASE}{path}", headers=headers, data=body, timeout=15)
    log(f"BYBIT POST -> {r.status_code} {r.text[:500]}")
    return r.json()

def get_saldo(coin):
    try:
        d = bybit_get("/v5/account/wallet-balance", f"accountType=UNIFIED&coin={coin}")
        if d.get("retCode") != 0:
            log(f"Saldo fout {coin}: {d}")
            return 0.0
        for c in d["result"]["list"][0]["coin"]:
            if c["coin"] == coin:
                return float(c["walletBalance"])
        return 0.0
    except Exception as e:
        log(f"Saldo fout {coin}: {e}")
        return 0.0

def get_prijs():
    try:
        r = requests.get(f"{BASE}/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
        return float(r.json()["result"]["list"][0]["lastPrice"])
    except Exception as e:
        log(f"PRIJS FAIL: {e}")
        return 0.0

def get_klines():
    for poging in range(3):
        try:
            r = requests.get(f"{BASE}/v5/market/kline", params={"category": CATEGORY, "symbol": SYMBOL, "interval": "15", "limit": "200"}, timeout=10)
            lst = r.json()["result"]["list"]
            if lst:
                return lst[::-1]
        except Exception as e:
            log(f"KLINE FAIL poging {poging}: {e}")
        time.sleep(2)
    return []

def calc_atr(highs, lows, closes, period):
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    if len(trs) < period:
        return 0
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period-1) + tr) / period
    return atr

def get_supertrend(candles):
    if len(candles) < 50:
        return "down"
    closes = [float(c[4]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    atr = calc_atr(highs, lows, closes, ST_ATR)
    if atr == 0:
        return "down"
    up = 0
    down = 0
    trend = 1
    for i in range(1, len(closes)):
        basic_up = (highs[i]+lows[i])/2 - ST_MULT*atr
        basic_down = (highs[i]+lows[i])/2 + ST_MULT*atr
        if closes[i-1] > up:
            up = max(basic_up, up)
        else:
            up = basic_up
        if closes[i-1] < down:
            down = min(basic_down, down)
        else:
            down = basic_down
        if closes[i] > down:
            trend = 1
        elif closes[i] < up:
            trend = -1
    return "up" if trend == 1 else "down"

def get_utbot(candles):
    if len(candles) < 50:
        return "down"
    closes = [float(c[4]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    atr = calc_atr(highs, lows, closes, UT_ATR)
    if atr == 0:
        return "down"
    stop = 0
    pos = 0
    for i in range(1, len(closes)):
        if closes[i] > stop and closes[i-1] > stop:
            stop = max(stop, closes[i] - UT_KEY*atr)
        elif closes[i] < stop and closes[i-1] < stop:
            stop = min(stop, closes[i] + UT_KEY*atr)
        elif closes[i] > stop:
            stop = closes[i] - UT_KEY*atr
        else:
            stop = closes[i] + UT_KEY*atr
        if closes[i] > stop:
            pos = 1
        elif closes[i] < stop:
            pos = -1
    return "up" if pos == 1 else "down"

def get_trendline(candles):
    if len(candles) < 50:
        return "down"
    closes = [float(c[4]) for c in candles][-50:]
    n = len(closes)
    x = list(range(n))
    mx = sum(x)/n
    my = sum(closes)/n
    num = sum((x[i]-mx)*(closes[i]-my) for i in range(n))
    den = sum((x[i]-mx)**2 for i in range(n))
    slope = num/den if den else 0
    return "up" if slope > 0 and closes[-1] > my else "down"

def place_buy(usdc_amount, price):
    if price <= 0 or usdc_amount <= 0:
        return {}
    qty_total = math.floor((usdc_amount / price) / 1000) * 1000
    if qty_total <= 0:
        return {}
    done = 0
    last = {}
    while done < qty_total:
        qty = min(MAX_PER_ORDER, qty_total - done)
        qty = math.floor(qty / 1000) * 1000
        if qty <= 0:
            break
        body = {"category": CATEGORY, "symbol": SYMBOL, "side": "Buy", "orderType": "Market", "qty": str(int(qty))}
        log(f"ORDER Buy {SYMBOL} qty {qty} chunk {done+qty}/{qty_total}")
        last = bybit_post("/v5/order/create", body)
        if last.get("retCode") != 0:
            break
        done += qty
        time.sleep(1)
    if done > 0:
        bot_state["in_positie"] = True
    return last

def place_sell(pepe_qty, price):
    if pepe_qty <= 0:
        return {}
    qty_total = math.floor(pepe_qty / 1000) * 1000
    done = 0
    last = {}
    while done < qty_total:
        qty = min(MAX_PER_ORDER, qty_total - done)
        qty = math.floor(qty / 1000) * 1000
        if qty <= 0:
            break
        body = {"category": CATEGORY, "symbol": SYMBOL, "side": "Sell", "orderType": "Market", "qty": str(int(qty))}
        log(f"ORDER Sell {SYMBOL} qty {qty} chunk {done+qty}/{qty_total}")
        last = bybit_post("/v5/order/create", body)
        if last.get("retCode") != 0:
            break
        done += qty
        time.sleep(1)
    if done > 0:
        bot_state["in_positie"] = False
    return last

def do_check():
    global _last_check
    now = time.time()
    if not _lock.acquire(blocking=False):
        return {"status": "already running"}
    try:
        if now - _last_check < 30:
            return {"status": "too soon"}
        _last_check = now

        prijs = get_prijs()
        usdc = get_saldo("USDC")
        pepe = get_saldo("PEPE")
        klines = get_klines()
        log(f"Prijs {prijs} - USDC {usdc} - PEPE {pepe} - Candles {len(klines)} - in_positie {bot_state['in_positie']}")

        if len(klines) < 50:
            log(f"UTBOT: te weinig candles ({len(klines)}) -> HOLD")
            return {"status": "hold", "reason": "te weinig candles"}

        st = get_supertrend(klines)
        ut = get_utbot(klines)
        tl = get_trendline(klines)
        log(f"TRIPLE: Supertrend={st} UTBot={ut} Trendline={tl}")

        if st == "up" and ut == "up" and tl == "up" and not bot_state["in_positie"] and usdc >= 5:
            log(f"BUY signaal - koop voor {usdc} USDC")
            r = place_buy(usdc * 0.99, prijs)
            if r.get("retCode") == 0:
                send_telegram(f"BUY PEPEUSDC: {usdc:.2f} USDC op {prijs} - ST/UT/TL up")
                return {"status": "buy", "usdc": usdc, "price": prijs}
            return {"status": "buy-fail", "error": r}

        if (st == "down" or ut == "down") and bot_state["in_positie"] and pepe > 0:
            log(f"SELL signaal - verkoop {pepe} PEPE")
            r = place_sell(pepe, prijs)
            if r.get("retCode") == 0:
                send_telegram(f"SELL PEPEUSDC: {int(pepe)} op {prijs}")
                return {"status": "sell", "pepe": pepe}
            return {"status": "sell-fail", "error": r}

        if pepe > 1000:
            bot_state["in_positie"] = True
        elif pepe < 1000 and usdc > 5:
            bot_state["in_positie"] = False

        log("HOLD - geen triple signaal")
        return {"status": "hold", "st": st, "ut": ut, "tl": tl, "usdc": usdc, "pepe": pepe}
    except Exception as e:
        log(f"CHECK FAIL: {e}")
        return {"status": "fail", "error": str(e)}
    finally:
        _lock.release()

def bot_loop():
    time.sleep(10)
    while True:
        try:
            do_check()
        except Exception as e:
            log(f"BOT FOUT: {e}")
        time.sleep(60)

threading.Thread(target=bot_loop, daemon=True).start()

@app.route("/")
def home():
    return "Pepebot-3 live - Triple ST+UT+TL"

@app.route("/check")
def check():
    return jsonify(do_check())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
