# ============================================================
# PEPEBOT - FIX VOOR LOPENDE TRADE
# Gister 19:00 versie + fix: buy_price wordt NOOIT overschreven
# ============================================================
import os, time, math, hmac, hashlib, json, threading, traceback
import requests
from flask import Flask, jsonify

app = Flask(__name__)

API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
BASE = "https://api.bybit.eu"
SYMBOL = "PEPEUSDC"
CATEGORY = "spot"

TRADE_USDC = float(os.getenv("TRADE_USDC", "10"))
MAX_PER_ORDER = 2500000
CHECK_EVERY = 60
STATE_FILE = "bot_state.json"

# Zet hier je ECHTE koopprijs als je hem weet.
# Jij kocht rond 0.000003485. Laat leeg als je hem niet weet,
# dan gebruikt hij de prijs van de eerste check en raakt hij hem daarna niet meer aan.
MANUAL_BUY_PRICE = os.getenv("BUY_PRICE", "").strip()

bot_state = {
    "in_positie": False,
    "buy_price": 0.0,
    "buy_qty": 0.0,
    "buy_time": 0,
    "last_signal": "NONE",
    "last_error": "",
}
state_lock = threading.Lock()

def log(msg):
    print(msg, flush=True)

def load_state():
    global bot_state
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                for k in bot_state:
                    if k in data:
                        bot_state[k] = data[k]
            log(f"STATE GELEDEN: in_positie={bot_state['in_positie']} buy_price={bot_state['buy_price']}")
    except Exception as e:
        log(f"STATE LOAD FAIL: {e}")

def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(bot_state, f)
    except Exception as e:
        log(f"STATE SAVE FAIL: {e}")

load_state()

# Als je BUY_PRICE in Render hebt gezet, is DIE leidend. Altijd.
if MANUAL_BUY_PRICE:
    try:
        bot_state["buy_price"] = float(MANUAL_BUY_PRICE)
        bot_state["in_positie"] = True
        log(f"MANUAL BUY_PRICE UIT RENDER: {bot_state['buy_price']}")
        save_state()
    except:
        pass

# ============================================================
# BYBIT V5 SIGNING - CORRECT
# ============================================================
def sign_request(payload, ts, recv):
    return hmac.new(API_SECRET.encode(), (ts + API_KEY + recv + payload).encode(), hashlib.sha256).hexdigest()

def bybit_get(path, params_str):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign_request(params_str, ts, recv),
    }
    r = requests.get(f"{BASE}{path}?{params_str}", headers=headers, timeout=12)
    return r.json()

def bybit_post(path, body_dict):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    body = json.dumps(body_dict)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign_request(body, ts, recv),
        "Content-Type": "application/json",
    }
    r = requests.post(f"{BASE}{path}", headers=headers, data=body, timeout=12)
    return r.json()

# ============================================================
# PRIJS / SALDO / KLINES
# ============================================================
def get_prijs():
    for poging in range(3):
        try:
            r = requests.get(f"{BASE}/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
            prijs = float(r.json()["result"]["list"][0]["lastPrice"])
            if prijs > 0:
                return prijs
        except Exception as e:
            log(f"PRIJS FAIL: {e}")
            time.sleep(2)
    return 0.0

def get_saldo(coin):
    try:
        data = bybit_get("/v5/account/wallet-balance", f"accountType=UNIFIED&coin={coin}")
        if data.get("retCode") != 0:
            log(f"Saldo fout {coin}: {data}")
            with state_lock:
                bot_state["last_error"] = str(data.get("retMsg"))
            return 0.0
        for c in data["result"]["list"][0]["coin"]:
            if c["coin"] == coin:
                return float(c["walletBalance"])
        return 0.0
    except Exception as e:
        log(f"Saldo fout {coin}: {e}")
        return 0.0

def get_klines():
    for poging in range(3):
        try:
            r = requests.get(f"{BASE}/v5/market/kline", params={"category": CATEGORY, "symbol": SYMBOL, "interval": "15", "limit": "200"}, timeout=12)
            klines = r.json()["result"]["list"]
            if klines:
                return klines[::-1]
        except Exception as e:
            log(f"KLINE FAIL: {e}")
        time.sleep(2)
    return []

# ============================================================
# SIGNALEN - SUPERTREND + UTBOT + TRENDLINE
# ============================================================
def ema(data, p):
    k = 2 / (p + 1)
    v = data[0]
    for x in data[1:]:
        v = x * k + v * (1 - k)
    return v

def atr(highs, lows, closes, period=10):
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    if not trs:
        return 0.0
    if len(trs) < period:
        return sum(trs) / len(trs)
    return sum(trs[-period:]) / period

def supertrend_up(highs, lows, closes):
    a = atr(highs, lows, closes, 10)
    if a == 0:
        return False
    hl2 = (highs[-1] + lows[-1]) / 2
    midden = hl2
    return closes[-1] > midden

def utbot_up(closes):
    if len(closes) < 50:
        return False
    return ema(closes, 10) > ema(closes, 50)

def trendline_up(closes):
    if len(closes) < 20:
        return False
    kort = sum(closes[-10:]) / 10
    lang = sum(closes[-20:-10]) / 10
    return kort > lang

def get_signalen(klines):
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    return supertrend_up(highs, lows, closes), utbot_up(closes), trendline_up(closes)

# ============================================================
# ORDERS - MET OPKNIP
# ============================================================
def place_buy(usdc_amount, prijs):
    if prijs <= 0:
        return {}
    qty_total = math.floor((usdc_amount / prijs) / 1000) * 1000
    if qty_total <= 0:
        return {}
    done = 0
    last_res = {}
    while done < qty_total:
        qty = min(MAX_PER_ORDER, qty_total - done)
        qty = math.floor(qty / 1000) * 1000
        if qty <= 0:
            break
        body = {"category": CATEGORY, "symbol": SYMBOL, "side": "Buy", "orderType": "Market", "qty": str(int(qty))}
        log(f"ORDER Buy {qty} chunk {done + qty}/{qty_total}")
        last_res = bybit_post("/v5/order/create", body)
        log(f"ORDER Buy resultaat: {last_res}")
        if last_res.get("retCode") != 0:
            break
        done += qty
        time.sleep(1)
    if last_res.get("retCode") == 0:
        with state_lock:
            bot_state["in_positie"] = True
            bot_state["buy_price"] = prijs
            bot_state["buy_qty"] = done
            bot_state["buy_time"] = int(time.time())
            save_state()
    return last_res

def place_sell(qty_total, prijs):
    if qty_total <= 0:
        return {}
    qty_total = math.floor(qty_total / 1000) * 1000
    done = 0
    last_res = {}
    while done < qty_total:
        qty = min(MAX_PER_ORDER, qty_total - done)
        qty = math.floor(qty / 1000) * 1000
        if qty <= 0:
            break
        body = {"category": CATEGORY, "symbol": SYMBOL, "side": "Sell", "orderType": "Market", "qty": str(int(qty))}
        log(f"ORDER Sell {qty} chunk {done + qty}/{qty_total}")
        last_res = bybit_post("/v5/order/create", body)
        log(f"ORDER Sell resultaat: {last_res}")
        if last_res.get("retCode") != 0:
            break
        done += qty
        time.sleep(1)
    if last_res.get("retCode") == 0:
        with state_lock:
            bot_state["in_positie"] = False
            bot_state["buy_price"] = 0.0
            bot_state["buy_qty"] = 0.0
            bot_state["buy_time"] = 0
            save_state()
    return last_res

# ============================================================
# AUTO CHECK - HIER ZIT DE FIX
# ============================================================
def auto_check():
    log("=== AUTO CHECK ===")
    prijs = get_prijs()
    usdc = get_saldo("USDC")
    pepe = get_saldo("PEPE")
    klines = get_klines()
    log(f"Prijs {prijs} - USDC {usdc} - PEPE {pepe} - Candles {len(klines)}")

    if prijs <= 0:
        return
    if len(klines) < 50:
        log(f"HOLD: te weinig candles ({len(klines)})")
        return

    # --- FIX: HERSTEL ZONDER BUY_PRICE TE OVERSCHRIJVEN ---
    with state_lock:
        if pepe > 1000 and not bot_state["in_positie"]:
            log(f"HERSTEL: PEPE {pepe} gevonden, je zit in een trade")
            bot_state["in_positie"] = True
            bot_state["buy_qty"] = pepe
            bot_state["buy_time"] = int(time.time())
            # ALLEEN als we echt nog geen buy_price hebben, zet hem 1x op huidige prijs
            # Daarna raakt niemand hem meer aan
            if bot_state["buy_price"] <= 0:
                bot_state["buy_price"] = prijs
                log(f"HERSTEL: buy_price 1x gezet op {prijs} - wordt nu vastgehouden")
            else:
                log(f"HERSTEL: buy_price blijft {bot_state['buy_price']} - NIET overschreven")
            save_state()
        if pepe <= 1000 and bot_state["in_positie"]:
            log("HERSTEL: geen PEPE meer, reset positie")
            bot_state["in_positie"] = False
            bot_state["buy_price"] = 0.0
            bot_state["buy_qty"] = 0.0
            save_state()

    st, ut, tr = get_signalen(klines)
    log(f"Signalen - Supertrend: {st} | UTBOT: {ut} | Trendline: {tr}")

    # --- ALS JE IN EEN TRADE ZIT: ALLEEN VERKOPEN, NOOIT KOPEN ---
    if bot_state["in_positie"]:
        buy_price = bot_state["buy_price"]
        if buy_price > 0:
            change = (prijs - buy_price) / buy_price
            log(f"In positie: buy {buy_price} -> nu {prijs} = {change*100:.2f}%")
            if change <= -0.04:
                log(f"SELL: STOP-LOSS {change*100:.2f}%")
                place_sell(pepe, prijs)
                return
            if change >= 0.03:
                log(f"SELL: TAKE-PROFIT {change*100:.2f}%")
                place_sell(pepe, prijs)
                return
            if not st and not ut and not tr:
                log("SELL: alle 3 signalen down")
                place_sell(pepe, prijs)
                return
        log("HOLD: in trade, geen verkoopsignaal")
        with state_lock:
            bot_state["last_signal"] = "HOLD - IN TRADE"
        return

    # --- GEEN TRADE: DAN PAS KOPEN ---
    if pepe > 1000:
        log("HOLD: heb al PEPE, koop niet dubbel")
        return
    if usdc < TRADE_USDC:
        log(f"HOLD: te weinig USDC ({usdc} < {TRADE_USDC})")
        return
    if st and ut and tr:
        log(f"BUY: alle 3 UP - koop
