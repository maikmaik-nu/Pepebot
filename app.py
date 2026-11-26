# ============================================================
# PEPEBOT - COMPLETE VERSIE
# 【entity-Bybit¦canonical_name=BYBIT】 EU - PEPEUSDC - UNIFIED - SPOT
# Supertrend + UTBOT + Trendline - alle 3 moeten UP zijn
# ============================================================
import os, time, math, hmac, hashlib, json, threading, traceback
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# ============================================================
# 1. CONFIG
# ============================================================
API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
BASE = "https://api.bybit.eu"
SYMBOL = "PEPEUSDC"
CATEGORY = "spot"

TRADE_USDC = float(os.getenv("TRADE_USDC", "10"))  # per trade, zoals om 19:00
MAX_PER_ORDER = 2500000  # opknip-fix tegen error 170381
CHECK_EVERY = 60

# Telegram - optioneel, laat leeg als je het niet wil
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# State - blijft in geheugen + wordt hersteld uit saldo na restart
bot_state = {
    "in_positie": False,
    "buy_price": 0.0,
    "buy_qty": 0.0,
    "buy_time": 0,
    "last_signal": "NONE",
    "last_error": "",
    "total_trades": 0,
    "total_wins": 0,
    "total_losses": 0,
}
state_lock = threading.Lock()

# ============================================================
# 2. LOG + TELEGRAM
# ============================================================
def log(msg):
    print(msg, flush=True)

def telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception as e:
        log(f"TELEGRAM FAIL: {e}")

# ============================================================
# 3. 【entity-BYBIT¦canonical_name=BYBIT】 V5 SIGNING - CORRECT
# ============================================================
def sign_request(payload, ts, recv):
    return hmac.new(
        API_SECRET.encode(),
        (ts + API_KEY + recv + payload).encode(),
        hashlib.sha256,
    ).hexdigest()

def 【entity-bybit_get¦canonical_name=BYBIT】(path, params_str):
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
# 4. PRIJS / SALDO / KLINES
# ============================================================
def get_prijs():
    for poging in range(3):
        try:
            r = requests.get(
                f"{BASE}/v5/market/tickers",
                params={"category": CATEGORY, "symbol": SYMBOL},
                timeout=10,
            )
            prijs = float(r.json()["result"]["list"][0]["lastPrice"])
            if prijs > 0:
                return prijs
        except Exception as e:
            log(f"PRIJS FAIL poging {poging+1}: {e}")
            time.sleep(2)
    return 0.0

def get_saldo(coin):
    # ALLEEN UNIFIED - SPOT geeft: accountType only support UNIFIED
    try:
        data = 【entity-bybit_get¦canonical_name=BYBIT】("/v5/account/wallet-balance", f"accountType=UNIFIED&coin={coin}")
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
    # Retry, want klines waren 1x leeg -> HOLD
    for poging in range(3):
        try:
            r = requests.get(
                f"{BASE}/v5/market/kline",
                params={"category": CATEGORY, "symbol": SYMBOL, "interval": "15", "limit": "200"},
                timeout=12,
            )
            klines = r.json()["result"]["list"]
            if klines:
                return klines[::-1]  # oud -> nieuw
        except Exception as e:
            log(f"KLINE FAIL poging {poging+1}: {e}")
        time.sleep(2)
    return []

# ============================================================
# 5. INDICATOREN - SUPERTREND + UTBOT + TRENDLINE
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
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return sum(trs) / max(1, len(trs)) if trs else 0.0
    return sum(trs[-period:]) / period

def supertrend_up(highs, lows, closes):
    # Supertrend (10, 3) - True = up
    period = 10
    mult = 3.0
    a = atr(highs, lows, closes, period)
    if a == 0:
        return False
    hl2 = (highs[-1] + lows[-1]) / 2
    upper = hl2 + mult * a
    lower = hl2 - mult * a
    # Simpele, stabiele versie: prijs boven middenlijn = up
    return closes[-1] > (upper + lower) / 2

def utbot_up(closes):
    # UTBOT-vervanger uit je 19:00 versie: EMA 10 vs EMA 50
    if len(closes) < 50:
        return False
    return ema(closes, 10) > ema(closes, 50)

def trendline_up(closes):
    # Trendline: prijs boven EMA 200 / stijgende lijn
    if len(closes) < 50:
        return False
    # Korte trendlijn: laatste 20 closes stijgend
    kort = sum(closes[-10:]) / 10
    lang = sum(closes[-20:-10]) / 10
    return kort > lang

def get_signalen(klines):
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    st = supertrend_up(highs, lows, closes)
    ut = utbot_up(closes)
    tr = trendline_up(closes)
    return st, ut, tr, closes

# ============================================================
# 6. ORDERS - MET OPKNIP-FIX
# ============================================================
def place_buy(usdc_amount, prijs):
    if prijs <= 0:
        return {}
    qty_total = math.floor((usdc_amount / prijs) / 1000) * 1000
    if qty_total <= 0:
        log(f"BUY STOP: qty te klein voor {usdc_amount} USDC @ {prijs}")
        return {}
    done = 0
    last_res = {}
    while done < qty_total:
        qty = min(MAX_PER_ORDER, qty_total - done)
        qty = math.floor(qty / 1000) * 1000
        if qty <= 0:
            break
        body = {
            "category": CATEGORY, "symbol": SYMBOL, "side": "Buy",
            "orderType": "Market", "qty": str(int(qty)),
        }
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
        telegram(f"✅ BUY: {done} PEPE @ {prijs} voor ~{usdc_amount} USDC")
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
        body = {
            "category": CATEGORY, "symbol": SYMBOL, "side": "Sell",
            "orderType": "Market", "qty": str(int(qty)),
        }
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
        telegram(f"✅ SELL: {done} PEPE @ {prijs}")
    return last_res

# ============================================================
# 7. AUTO CHECK - DE KERN
# ============================================================
def auto_check():
    log("=== AUTO CHECK ===")
    prijs = get_prijs()
    usdc = get_saldo("USDC")
    pepe = get_saldo("PEPE")
    klines = get_klines()
    log(f"Prijs {prijs} - USDC {usdc} - PEPE {pepe} - Candles {len(klines)}")

    if prijs <= 0:
        log("HOLD: geen prijs")
        return
    if len(klines) < 50:
        log(f"HOLD: te weinig candles ({len(klines)})")
        return

    # --- HERSTEL NA HERSTART ---
    # Als er PEPE in je wallet staat, zit je in positie, ook al is de bot net opnieuw opgestart
    with state_lock:
        if pepe > 1000 and not bot_state["in_positie"]:
            log(f"HERSTEL: PEPE {pepe} gevonden, herstel positie @ {prijs}")
            bot_state["in_positie"] = True
            bot_state["buy_price"] = prijs
            bot_state["buy_qty"] = pepe
            bot_state["buy_time"] = int(time.time())
        if pepe <= 1000 and bot_state["in_positie"]:
            log("HERSTEL: geen PEPE meer, reset positie")
            bot_state["in_positie"] = False
            bot_state["buy_price"] = 0.0
            bot_state["buy_qty"] = 0.0

    st, ut, tr, closes = get_signalen(klines)
    log(f"Signalen - Supertrend up: {st} | UTBOT up: {ut} | Trendline up: {tr}")

    # --- VERKOOP LOGICA ---
    if bot_state["in_positie"]:
        buy_price = bot_state["buy_price"]
        if buy_price > 0:
            change = (prijs - buy_price) / buy_price
            log(f"In positie: buy {buy_price} -> nu {prijs} = {change*100:.2f}%")

            # Stop-loss -4% - ALTIJD EERST
            if change <= -0.04:
                log(f"SELL: STOP-LOSS {change*100:.2f}%")
                res = place_sell(pepe, prijs)
                if res.get("retCode") == 0:
                    with state_lock:
                        bot_state["total_trades"] += 1
                        bot_state["total_losses"] += 1
                        bot_state["last_signal"] = "SELL STOPLOSS"
                return

            # Take-profit +3%
            if change >= 0.03:
                log(f"SELL: TAKE-PROFIT {change*100:.2f}%")
                res = place_sell(pepe, prijs)
                if res.get("retCode") == 0:
                    with state_lock:
                        bot_state["total_trades"] += 1
                        bot_state["total_wins"] += 1
                        bot_state["last_signal"] = "SELL TAKE-PROFIT"
                return

            # Alle 3 signalen naar beneden = eruit, ook zonder TP
            if not st and not ut and not tr:
                log("SELL: alle 3 signalen down")
                res = place_sell(pepe, prijs)
                if res.get("retCode") == 0:
                    with state_lock:
                        bot_state["total_trades"] += 1
                        bot_state["last_signal"] = "SELL SIGNALS DOWN"
                return

        log("HOLD: in positie, geen verkoopsignaal")
        with state_lock:
            bot_state["last_signal"] = "HOLD"
        return

    # --- KOOP LOGICA ---
    # NIET dubbel kopen: alleen als je geen PEPE hebt
    if pepe > 1000:
        log("HOLD: heb al PEPE, koop niet dubbel")
        return
    if usdc < TRADE_USDC:
        log(f"HOLD: te weinig USDC ({usdc} < {TRADE_USDC})")
        with state_lock:
            bot_state["last_signal"] = "HOLD - TE WEINIG USDC"
        return

    # Triple-check: alle 3 moeten UP zijn
    if st and ut and tr:
        log(f"BUY signaal - alle 3 UP - koop voor {TRADE_USDC} USDC")
        res = place_buy(TRADE_USDC, prijs)
        if res.get("retCode") == 0:
            with state_lock:
                bot_state["last_signal"] = "BUY"
    else:
        log("HOLD: niet alle 3 signalen UP")
        with state_lock:
            bot_state["last_signal"] = "HOLD - GEEN TRIPLE BUY"

def bot_loop():
    time.sleep(3)
    while True:
        try:
            auto_check()
        except Exception as e:
            log(f"BOT FOUT: {e}")
            traceback.print_exc()
        time.sleep(CHECK_EVERY)

threading.Thread(target=bot_loop, daemon=True).start()

# ============================================================
# 8. FLASK ROUTES
# ============================================================
@app.route("/")
def home():
    return "Bot draait - open /check"

@app.route("/check")
def check():
    prijs = get_prijs()
    usdc = get_saldo("USDC")
    pepe = get_saldo("PEPE")
    klines = get_klines()
    if len(klines) >= 50:
        st, ut, tr, _ = get_signalen(klines)
    else:
        st, ut, tr = False, False, False
    return jsonify({
        "live": True,
        "symbol": SYMBOL,
        "prijs": prijs,
        "usdc": usdc,
        "pepe": pepe,
        "candles": len(klines),
        "supertrend_up": st,
        "utbot_up": ut,
        "trendline_up": tr,
        "triple_buy": bool(st and ut and tr),
        "api_key_set": bool(API_KEY),
        "state": bot_state,
    })

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
