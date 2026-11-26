import os, time, math, hmac, hashlib, json, threading, traceback
import requests
from flask import Flask, jsonify
app = Flask(__name__)
API_KEY = os.getenv("【entity-BYBIT_API_KEY¦canonical_name=Bybit】", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
BASE = "https://api.bybit.eu"
SYMBOL = "PEPEUSDC"
CATEGORY = "spot"
TRADE_USDC = float(os.getenv("TRADE_USDC", "10"))
MAX_PER_ORDER = 2500000
CHECK_EVERY = 60
STATE_FILE = "bot_state.json"
MANUAL_BUY_PRICE = os.getenv("BUY_PRICE", "").strip()
bot_state = {"in_positie": False, "buy_price": 0.0, "buy_qty": 0.0, "buy_time": 0, "last_signal": "NONE", "last_error": ""}
state_lock = threading.Lock()
def log(msg):
    print(str(msg), flush=True)
def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                for k in bot_state:
                    if k in data:
                        bot_state[k] = data[k]
            log("STATE GELEDEN in_positie=" + str(bot_state["in_positie"]) + " buy_price=" + str(bot_state["buy_price"]))
    except Exception as e:
        log("STATE LOAD FAIL " + str(e))
def save_state():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(bot_state, f)
    except Exception as e:
        log("STATE SAVE FAIL " + str(e))
load_state()
if MANUAL_BUY_PRICE:
    try:
        bot_state["buy_price"] = float(MANUAL_BUY_PRICE)
        bot_state["in_positie"] = True
        log("MANUAL BUY_PRICE UIT RENDER: " + str(bot_state["buy_price"]))
        save_state()
    except Exception:
        pass
def sign_request(payload, ts, recv):
    return hmac.new(API_SECRET.encode(), (ts + API_KEY + recv + payload).encode(), hashlib.sha256).hexdigest()
def bybit_get(path, params_str):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    headers = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "X-BAPI-SIGN": sign_request(params_str, ts, recv)}
    r = requests.get(BASE + path + "?" + params_str, headers=headers, timeout=12)
    return r.json()
def bybit_post(path, body_dict):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    body = json.dumps(body_dict)
    headers = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": recv, "X-BAPI-SIGN": sign_request(body, ts, recv), "Content-Type": "application/json"}
    r = requests.post(BASE + path, headers=headers, data=body, timeout=12)
    return r.json()
def get_prijs():
    for poging in range(3):
        try:
            r = requests.get(BASE + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10)
            prijs = float(r.json()["result"]["list"][0]["lastPrice"])
            if prijs > 0:
                return prijs
        except Exception as e:
            log("PRIJS FAIL " + str(e))
            time.sleep(2)
    return 0.0
def get_saldo(coin):
    try:
        data = bybit_get("/v5/account/wallet-balance", "accountType=UNIFIED&coin=" + coin)
        if data.get("retCode") != 0:
            log("Saldo fout " + coin + ": " + str(data))
            with state_lock:
                bot_state["last_error"] = str(data.get("retMsg"))
            return 0.0
        for c in data["result"]["list"][0]["coin"]:
            if c["coin"] == coin:
                return float(c["walletBalance"])
        return 0.0
    except Exception as e:
        log("Saldo fout " + coin + ": " + str(e))
        return 0.0
def get_klines():
    for poging in range(3):
        try:
            r = requests.get(BASE + "/v5/market/kline", params={"category": CATEGORY, "symbol": SYMBOL, "interval": "15", "limit": "200"}, timeout=12)
            klines = r.json()["result"]["list"]
            if klines:
                return klines[::-1]
        except Exception as e:
            log("KLINE FAIL " + str(e))
        time.sleep(2)
    return []
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
    return closes[-1] > hl2
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
        log("ORDER Buy " + str(qty) + " chunk " + str(done + qty) + "/" + str(qty_total))
        last_res = bybit_post("/v5/order/create", body)
        log("ORDER Buy resultaat: " + str(last_res))
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
        log("ORDER Sell " + str(qty) + " chunk " + str(done + qty) + "/" + str(qty_total))
        last_res = bybit_post("/v5/order/create", body)
        log("ORDER Sell resultaat: " + str(last_res))
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
def auto_check():
    log("=== AUTO CHECK ===")
    prijs = get_prijs()
    usdc = get_saldo("USDC")
    pepe = get_saldo("PEPE")
    klines = get_klines()
    log("Prijs " + str(prijs) + " - USDC " + str(usdc) + " - PEPE " + str(pepe) + " - Candles " + str(len(klines)))
    if prijs <= 0:
        return
    if len(klines) < 50:
        log("HOLD: te weinig candles " + str(len(klines)))
        return
    with state_lock:
        if pepe > 1000 and not bot_state["in_positie"]:
            log("HERSTEL: PEPE gevonden, je zit in een trade")
            bot_state["in_positie"] = True
            bot_state["buy_qty"] = pepe
            bot_state["buy_time"] = int(time.time())
            if bot_state["buy_price"] <= 0:
                bot_state["buy_price"] = prijs
                log("HERSTEL: buy_price 1x gezet op " + str(prijs))
            else:
                log("HERSTEL: buy_price blijft " + str(bot_state["buy_price"]))
            save_state()
        if pepe <= 1000 and bot_state["in_positie"]:
            log("HERSTEL: geen PEPE meer, reset positie")
            bot_state["in_positie"] = False
            bot_state["buy_price"] = 0.0
            bot_state["buy_qty"] = 0.0
            save_state()
    st, ut, tr = get_signalen(klines)
    log("Signalen - Supertrend: " + str(st) + " | UTBOT: " + str(ut) + " | Trendline: " + str(tr))
    if bot_state["in_positie"]:
        buy_price = bot_state["buy_price"]
        if buy_price > 0:
            change = (prijs - buy_price) / buy_price
            log("In positie: buy " + str(buy_price) + " -> nu " + str(prijs) + " = " + str(change*100) + "%")
            if change <= -0.04:
                log("SELL: STOP-LOSS " + str(change*100) + "%")
                place_sell(pepe, prijs)
                return
            if change >= 0.03:
                log("SELL: TAKE-PROFIT " + str(change*100) + "%")
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
    if pepe > 1000:
        log("HOLD: heb al PEPE, koop niet dubbel")
        return
    if usdc < TRADE_USDC:
        log("HOLD: te weinig USDC " + str(usdc) + " < " + str(TRADE_USDC))
        return
    if st and ut and tr:
        log("BUY: alle 3 UP - koop voor " + str(TRADE_USDC) + " USDC")
        place_buy(TRADE_USDC, prijs)
    else:
        log("HOLD: niet alle 3 UP")
def bot_loop():
    time.sleep(3)
    while True:
        try:
            auto_check()
        except Exception as e:
            log("BOT FOUT: " + str(e))
            traceback.print_exc()
        time.sleep(CHECK_EVERY)
threading.Thread(target=bot_loop, daemon=True).start()
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
        st, ut, tr = get_signalen(klines)
    else:
        st, ut, tr = False, False, False
    return jsonify({"live": True, "symbol": SYMBOL, "prijs": prijs, "usdc": usdc, "pepe": pepe, "candles": len(klines), "supertrend_up": st, "utbot_up": ut, "trendline_up": tr, "state": bot_state})
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
