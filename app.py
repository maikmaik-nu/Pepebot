import os, time, hmac, hashlib, json, threading, requests, math
from flask import Flask, jsonify
app = Flask(__name__)

# ============ CONFIG ============
BASE_URL = os.getenv("BASE_URL", "https://api.bybit.eu").strip()
API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SYMBOL = "PEPEUSDC"
BUY_AMOUNT = 10.0
CHECK_SECONDS = 60
TAKE_PROFIT_PCT = 5.0
STOP_LOSS_PCT = -3.0

running = False
bought_price = 0.0
bought_qty = 0
last_signal = "NONE"
trade_count = 0
last_buy_time = 0

# ============ LOG + TELEGRAM ============
def log(msg):
    print(msg, flush=True)

def send_tg(msg):
    if not TG_TOKEN or not TG_CHAT:
        log("Telegram overgeslagen: geen token/chat")
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
        log(f"Telegram gestuurd: {r.status_code}")
    except Exception as e:
        log(f"Telegram fout: {e}")

# ============ SIGN + HEADERS ============
def make_sign(timestamp, recv_window, payload_str):
    msg = timestamp + API_KEY + recv_window + payload_str
    return hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        log(f"Geen JSON terug, status {resp.status_code}, body: {resp.text[:300]}")
        return {}

def headers_for(ts, recv, sign):
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "X-BAPI-SIGN-TYPE": "2",
        "Content-Type": "application/json"
    }

# ============ BALANCE ============
def get_balance_unified(coin):
    ts = str(int(time.time() * 1000)); recv = "20000"
    query = "accountType=UNIFIED&coin=" + coin
    sign = make_sign(ts, recv, query)
    try:
        r = safe_json(requests.get(BASE_URL + "/v5/account/wallet-balance?" + query, headers=headers_for(ts, recv, sign), timeout=10))
    except Exception as e:
        log(f"Balance UNIFIED exception {coin}: {e}")
        return 0.0
    if r.get("retCode") != 0:
        log(f"Balance UNIFIED fail {coin}: {r}")
        return 0.0
    lst = r.get("result", {}).get("list", []) or []
    if not lst:
        return 0.0
    for c in lst[0].get("coin", []) or []:
        if c.get("coin") == coin:
            try:
                return float(c.get("walletBalance") or 0)
            except:
                return 0.0
    return 0.0

def get_balance_funding(coin):
    ts = str(int(time.time() * 1000)); recv = "20000"
    query = "accountType=FUND&coin=" + coin
    sign = make_sign(ts, recv, query)
    try:
        r = safe_json(requests.get(BASE_URL + "/v5/asset/transfer/query-account-coins-balance?" + query, headers=headers_for(ts, recv, sign), timeout=10))
    except Exception as e:
        log(f"Balance FUND exception {coin}: {e}")
        return 0.0
    if r.get("retCode") != 0:
        log(f"Balance FUND fail {coin}: {r}")
        return 0.0
    res = r.get("result", {})
    lst = res.get("list", []) if isinstance(res, dict) else res if isinstance(res, list) else []
    if isinstance(res, dict) and not lst and res.get("coin"):
        lst = [res]
    for c in lst:
        if isinstance(c, dict) and c.get("coin") == coin:
            try:
                return float(c.get("balance") or c.get("walletBalance") or 0)
            except:
                return 0.0
    return 0.0

def get_balance():
    try:
        if not API_KEY or not API_SECRET:
            log("Balance fout: geen API key in Render")
            return 0.0, 0.0, 0.0
        usdc = get_balance_unified("USDC")
        usdt = get_balance_unified("USDT")
        pepe = get_balance_unified("PEPE")
        if usdc == 0:
            usdc = get_balance_funding("USDC")
        if usdt == 0:
            usdt = get_balance_funding("USDT")
        if pepe == 0:
            pepe = get_balance_funding("PEPE")
        return usdt, usdc, pepe
    except Exception as e:
        log(f"Balance fout: {e}")
        return 0.0, 0.0, 0.0

# ============ KLINES ============
def get_klines(symbol):
    try:
        r = safe_json(requests.get(BASE_URL + "/v5/market/kline", params={"category": "spot", "symbol": symbol, "interval": "15", "limit": 200}, timeout=10))
        if r.get("retCode") != 0:
            log(f"Kline fail: {r}")
            return []
        lst = r.get("result", {}).get("list", []) or []
        lst = list(reversed(lst))
        out = []
        for k in lst:
            try:
                out.append({"open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])})
            except:
                continue
        return out
    except Exception as e:
        log(f"Kline fout: {e}")
        return []

def get_current_price(symbol):
    klines = get_klines(symbol)
    if not klines:
        return 0.0
    return klines[-1]["close"]

# ============ INDICATOREN ============
def calc_atr(klines, period):
    trs = []
    for i in range(1, len(klines)):
        high = klines[i]["high"]
        low = klines[i]["low"]
        prev = klines[i-1]["close"]
        tr = max(high - low, abs(high - prev), abs(low - prev))
        trs.append(tr)
    if len(trs) < period:
        return sum(trs) / len(trs) if trs else 0
    return sum(trs[-period:]) / period

def calc_supertrend(klines):
    atr = calc_atr(klines, 10)
    hl2 = [(k["high"] + k["low"]) / 2 for k in klines]
    closes = [k["close"] for k in klines]
    upper = [hl2[i] + 3 * atr for i in range(len(klines))]
    lower = [hl2[i] - 3 * atr for i in range(len(klines))]
    st_up = True
    st_line = lower[-1]
    for i in range(1, len(klines)):
        if closes[i] > upper[i-1]:
            st_up = True
            st_line = lower[i]
        elif closes[i] < lower[i-1]:
            st_up = False
            st_line = upper[i]
    return st_up, st_line, atr

def calc_utbot(klines):
    atr14 = calc_atr(klines, 14)
    x_atr = 4 * atr14
    closes = [k["close"] for k in klines]
    hl2 = [(k["high"] + k["low"]) / 2 for k in klines]
    src = closes[-1]
    prev_close = closes[-2]
    trailing = hl2[-1] - x_atr
    ut_up = src > prev_close and src > trailing
    return ut_up, trailing, x_atr

def calc_signals(klines):
    if len(klines) < 50:
        log(f"Te weinig klines: {len(klines)}")
        return False, False, 0.0, 0.0, 0.0
    closes = [k["close"] for k in klines]
    price = closes[-1]
    st_up, st_line, atr10 = calc_supertrend(klines)
    ut_up, ut_line, x_atr = calc_utbot(klines)
    log(f"Signals: ST={st_up} lijn {st_line} | UT={ut_up} lijn {ut_line} | prijs {price} | ATR10 {atr10}")
    return st_up, ut_up, price, st_line, ut_line

# ============ ORDERS ============
def place_buy(symbol, usdc_amount, price):
    global bought_price, bought_qty
    if price <= 0:
        log("Buy afgebroken: prijs 0")
        return {}
    qty = math.floor(usdc_amount / price)
    if qty <= 0:
        log(f"Buy afgebroken: qty 0, bedrag {usdc_amount} prijs {price}")
        return {}
    ts = str(int(time.time() * 1000)); recv = "20000"
    body = {"category": "spot", "symbol": symbol, "side": "Buy", "orderType": "Market", "qty": str(qty)}
    payload = json.dumps(body)
    sign = make_sign(ts, recv, payload)
    log(f"ORDER Buy {symbol} qty {qty} voor {usdc_amount} USDC @ {price}")
    r = safe_json(requests.post(BASE_URL + "/v5/order/create", headers=headers_for(ts, recv, sign), data=payload, timeout=10))
    log(f"ORDER Buy resultaat: {r}")
    if r.get("retCode") == 0:
        bought_price = price
        bought_qty = qty
    return r

def place_sell(symbol, qty, price, reason):
    if qty <= 0:
        log("Sell afgebroken: qty 0")
        return {}
    if price <= 0:
        log("Sell afgebroken: prijs 0")
        return {}
    ts = str(int(time.time() * 1000)); recv = "20000"
    body = {"category": "spot", "symbol": symbol, "side": "Sell", "orderType": "Market", "qty": str(int(qty))}
    payload = json.dumps(body)
    sign = make_sign(ts, recv, payload)
    log(f"ORDER Sell {symbol} qty {qty} reden {reason} @ {price}")
    r = safe_json(requests.post(BASE_URL + "/v5/order/create", headers=headers_for(ts, recv, sign), data=payload, timeout=10))
    log(f"ORDER Sell resultaat: {r}")
    return r

# ============ BOT LOOP ============
def bot_loop():
    global running, last_signal, trade_count, last_buy_time, bought_price, bought_qty
    if running:
        return
    running = True
    log("Bot loop gestart - Pepebot-3 FULL")
    send_tg("Pepebot-3 gestart - FULL versie live")
    while True:
        try:
            usdt, usdc, pepe = get_balance()
            klines = get_klines(SYMBOL)
            st_up, ut_up, price, st_line, ut_line = calc_signals(klines)
            up = st_up and ut_up
            down = (not st_up) and (not ut_up)
            sig = "UP" if up else "DOWN" if down else "NEUTRAAL"
            if sig != last_signal:
                log(f"Signaal veranderd: {last_signal} -> {sig}")
                last_signal = sig
            log(f"Check: USDT {usdt:.2f} USDC {usdc:.2f} PEPE {pepe:.0f} SYMBOL {SYMBOL} UP={up} ST={st_up} UT={ut_up} prijs {price} bought@{bought_price} qty {bought_qty}")

            # SELL LOGICA
            if pepe > 0:
                pnl_pct = ((price - bought_price) / bought_price * 100) if bought_price > 0 else 0
                log(f"Holding PEPE: winst {pnl_pct:.2f}% TP {TAKE_PROFIT_PCT}% SL {STOP_LOSS_PCT}%")
                should_sell = False
                reason = ""
                if bought_price > 0 and pnl_pct >= TAKE_PROFIT_PCT:
                    should_sell = True
                    reason = f"TAKE PROFIT {pnl_pct:.2f}%"
                elif bought_price > 0 and pnl_pct <= STOP_LOSS_PCT:
                    should_sell = True
                    reason = f"STOP LOSS {pnl_pct:.2f}%"
                elif down:
                    should_sell = True
                    reason = "DOWN signaal ST+UT"
                if should_sell:
                    res = place_sell(SYMBOL, pepe, price, reason)
                    if res.get("retCode") == 0:
                        send_tg(f"PEPE VERKOCHT {reason} @ {price} winst {pnl_pct:.2f}%")
                        bought_price = 0.0
                        bought_qty = 0
                        trade_count += 1
                    else:
                        send_tg(f"SELL afgewezen: {res.get('retMsg')}")
                else:
                    log("Houden: nog geen sell reden")
            # BUY LOGICA
            elif up and usdc >= BUY_AMOUNT and pepe == 0:
                res = place_buy(SYMBOL, BUY_AMOUNT, price)
                if res.get("retCode") == 0:
                    send_tg(f"PEPE GEKOCHT {SYMBOL} voor {BUY_AMOUNT} USDC @ {price}")
                    last_buy_time = time.time()
                    trade_count += 1
                else:
                    send_tg(f"BUY afgewezen: {res.get('retMsg')}")
                    log(f"BUY afgewezen volledig: {res}")
            elif up and pepe > 0:
                log("Niet opnieuw kopen: heb al PEPE")
            elif not up:
                log("Niet traden: geen UP signaal")
            elif usdc < BUY_AMOUNT:
                log("Niet traden: te weinig USDC")
        except Exception as e:
            log(f"Loop fout: {e}")
        time.sleep(CHECK_SECONDS)

# ============ ROUTES ============
@app.route("/")
def home():
    return "Pepebot-3 live - FULL"

@app.route("/status")
def status():
    usdt, usdc, pepe = get_balance()
    price = get_current_price(SYMBOL)
    return jsonify({"symbol": SYMBOL, "usdt": usdt, "usdc": usdc, "pepe": pepe, "price": price, "bought_price": bought_price, "bought_qty": bought_qty, "last_signal": last_signal, "trades": trade_count})

@app.route("/debug-key")
def debug_key():
    return jsonify({"key_len": len(API_KEY), "key_start": API_KEY[:4] if API_KEY else "", "base_url": BASE_URL, "symbol": SYMBOL, "buy_amount": BUY_AMOUNT})

@app.route("/test-key")
def test_key():
    usdt, usdc, pepe = get_balance()
    return jsonify({"retCode": 0, "usdt": usdt, "usdc": usdc, "pepe": pepe, "price": get_current_price(SYMBOL)})

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
