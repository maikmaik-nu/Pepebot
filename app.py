import os, time, hmac, hashlib, requests, threading, traceback
from flask import Flask, jsonify, request

app = Flask(__name__)

# ============================================================
# CONFIG - ALLES UIT JOUW INFO
# ============================================================
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDC"  # JOUW FIX: ER MOET USDC STAAN, GEEN USDT
CATEGORY = "spot"

UT_FACTOR = 4.0
UT_PERIOD = 14
ST_PERIOD = 10
ST_MULT = 3.0
KLINE_INTERVAL = "15"
KLINE_LIMIT = 200
CHECK_INTERVAL = 60
USE_PERCENT = 0.95

last_telegram = 0
last_uur_msg = 0
CHECK_RUNNING = False
bot_started_at = time.time()
total_checks = 0
total_buys = 0
total_sells = 0
last_signal = {}
last_error = ""

# ============================================================
# TELEGRAM - UIT JE OUDE CODE
# Max 1x per uur, behalve berichten met "UUR"
# ============================================================
def send_msg(msg, force=False):
    global last_telegram
    now = time.time()
    if not force and "UUR" not in msg and now - last_telegram < 3600:
        return False
    last_telegram = now
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"TELEGRAM (geen token): {msg}", flush=True)
        return False
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
        print(f"Telegram verstuurd: {msg[:100]}", flush=True)
        return True
    except Exception as e:
        print(f"Telegram fout: {e}", flush=True)
        return False

def send_uur_update(usdc, pepe, price, ut_up, st_up, ut_stop, st_val):
    global last_uur_msg
    now = time.time()
    if now - last_uur_msg < 3600:
        return
    last_uur_msg = now
    send_msg(
        f"UUR UPDATE Pepebot-3\n"
        f"USDC: {usdc}\nPEPE: {pepe}\nPrijs: {price}\nSymbol: {SYMBOL}\n"
        f"UT: {'UP' if ut_up else 'DOWN'} (stop {ut_stop})\n"
        f"ST: {'UP' if st_up else 'DOWN'} (val {st_val})\n"
        f"Checks: {total_checks} Buys: {total_buys} Sells: {total_sells}",
        force=True
    )

# ============================================================
# BYBIT SIGN
# ============================================================
def sign(params):
    timestamp = str(int(time.time() * 1000))
    recv = "5000"
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    to_sign = timestamp + API_KEY + recv + query
    signature = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": signature,
        "Content-Type": "application/json"
    }

# ============================================================
# SALDO - UIT JE OUDE CODE + FIX VOOR 'list' FOUT
# accountType SPOT, walletBalance, None,None bij fout
# ============================================================
def get_balance():
    try:
        params = {"accountType": "SPOT"}
        headers = sign(params)
        r = requests.get(f"{BASE_URL}/v5/account/wallet-balance", params=params, headers=headers, timeout=10)
        data = r.json()
        if data.get("retCode") != 0:
            print(f"Balance fail: retCode={data.get('retCode')} retMsg={data.get('retMsg')} full={data}", flush=True)
            return None, None
        # FIX: Bybit geeft result.list als LIST, niet als dict
        result_list = data.get("result", {}).get("list", [])
        if not result_list:
            print(f"Balance fail: 'list' leeg - {data}", flush=True)
            return None, None
        coins = result_list[0].get("coin", [])
        usdc = 0.0
        pepe = 0.0
        for c in coins:
            if c.get("coin") == "USDC":
                usdc = float(c.get("walletBalance", 0))
            if c.get("coin") == "PEPE":
                pepe = float(c.get("walletBalance", 0))
        print(f"Saldo: USDC {usdc} PEPE {pepe}", flush=True)
        return usdc, pepe
    except Exception as e:
        print(f"Balance fail: {e}", flush=True)
        traceback.print_exc()
        return None, None

def get_all_balances_debug():
    try:
        params = {"accountType": "SPOT"}
        headers = sign(params)
        r = requests.get(f"{BASE_URL}/v5/account/wallet-balance", params=params, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

# ============================================================
# PRIJS + CANDLES - UIT JE OUDE CODE
# 200 candles, 15min, PEPEUSDC
# ============================================================
def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        return float(r.json()["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print(f"Prijs fout: {e}", flush=True)
        return 0.0

def get_klines():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/kline", params={"category": "spot", "symbol": SYMBOL, "interval": KLINE_INTERVAL, "limit": KLINE_LIMIT}, timeout=10)
        raw = r.json()["result"]["list"]
        raw = raw[::-1]  # nieuw -> oud omdraaien naar oud -> nieuw
        highs = [float(x[2]) for x in raw]
        lows = [float(x[3]) for x in raw]
        closes = [float(x[4]) for x in raw]
        opens = [float(x[1]) for x in raw]
        volumes = [float(x[5]) for x in raw]
        return highs, lows, closes, opens, volumes
    except Exception as e:
        print(f"Klines fout: {e}", flush=True)
        traceback.print_exc()
        return [], [], [], [], []

# ============================================================
# ATR - NODIG VOOR UT + ST
# ============================================================
def calc_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return [0.0] * len(closes)
    tr_list = []
    for i in range(len(closes)):
        if i == 0:
            tr_list.append(highs[i] - lows[i])
        else:
            tr1 = highs[i] - lows[i]
            tr2 = abs(highs[i] - closes[i-1])
            tr3 = abs(lows[i] - closes[i-1])
            tr_list.append(max(tr1, tr2, tr3))
    atr = []
    for i in range(len(closes)):
        if i < period:
            atr.append(sum(tr_list[:i+1]) / (i+1))
        elif i == period:
            atr.append(sum(tr_list[:period]) / period)
        else:
            atr.append((atr[-1] * (period - 1) + tr_list[i]) / period)
    return atr

# ============================================================
# UT BOT EXACT - UIT JE OUDE CODE
# factor 4.0, period 14, trailing stop loop
# ============================================================
def ut_bot_exact(highs, lows, closes, factor=4.0, period=14):
    if len(closes) < period + 5:
        return False, 0.0, 0.0
    atr_list = calc_atr(highs, lows, closes, period)
    trailing_stop = closes[0] - factor * atr_list[0]
    pos = 0
    for i in range(1, len(closes)):
        close = closes[i]
        prev_close = closes[i-1]
        xatr = atr_list[i] * factor
        if close > trailing_stop and prev_close > trailing_stop:
            trailing_stop = max(trailing_stop, close - xatr)
        elif close < trailing_stop and prev_close < trailing_stop:
            trailing_stop = min(trailing_stop, close + xatr)
        elif close > trailing_stop:
            trailing_stop = close - xatr
        else:
            trailing_stop = close + xatr
        if close > trailing_stop and prev_close > trailing_stop:
            pos = 1
        elif close < trailing_stop and prev_close < trailing_stop:
            pos = -1
        if close < trailing_stop and prev_close > trailing_stop:
            pos = -1
        if close > trailing_stop and prev_close < trailing_stop:
            pos = 1
    return (pos == 1), trailing_stop, atr_list[-1] * factor

# ============================================================
# SUPERTREND EXACT - UIT JE OUDE CODE
# period 10, mult 3.0, upper/lower band
# ============================================================
def supertrend_exact(highs, lows, closes, period=10, mult=3.0):
    if len(closes) < period + 5:
        return False, 0.0
    atr_list = calc_atr(highs, lows, closes, period)
    upperband = []
    lowerband = []
    supertrend = []
    direction = []
    for i in range(len(closes)):
        hl2 = (highs[i] + lows[i]) / 2.0
        atr_val = atr_list[i] * mult
        ub = hl2 + atr_val
        lb = hl2 - atr_val
        upperband.append(ub)
        lowerband.append(lb)
        if i == 0:
            supertrend.append(ub)
            direction.append(-1)
        else:
            if closes[i-1] > upperband[i-1]:
                ub = max(ub, upperband[i-1])
            if closes[i-1] < lowerband[i-1]:
                lb = min(lb, lowerband[i-1])
            upperband[i] = ub
            lowerband[i] = lb
            if closes[i] > supertrend[i-1]:
                direction.append(1)
            elif closes[i] < supertrend[i-1]:
                direction.append(-1)
            else:
                direction.append(direction[i-1])
            if direction[i] == 1:
                supertrend.append(lb)
            else:
                supertrend.append(ub)
            if direction[i] == 1 and direction[i-1] == -1:
                supertrend[i] = lb
            if direction[i] == -1 and direction[i-1] == 1:
                supertrend[i] = ub
    return (direction[-1] == 1), supertrend[-1]

# ============================================================
# CHECK SIGNAL - UIT JE OUDE CODE
# closes[:-1] = laatste nog niet gesloten candle eraf
# ============================================================
def check_signal():
    global last_signal
    highs, lows, closes, opens, volumes = get_klines()
    if len(closes) < 30:
        print(f"check_signal: te weinig candles ({len(closes)})", flush=True)
        return False, False, 0.0, 0.0, 0.0
    highs_c = highs[:-1]
    lows_c = lows[:-1]
    closes_c = closes[:-1]
    ut_up, ut_stop, ut_xatr = ut_bot_exact(highs_c, lows_c, closes_c, factor=UT_FACTOR, period=UT_PERIOD)
    st_up, st_val = supertrend_exact(highs_c, lows_c, closes_c, period=ST_PERIOD, mult=ST_MULT)
    price = get_price()
    print(f"Signal: UT {'UP' if ut_up else 'DOWN'} (stop {ut_stop}) ST {'UP' if st_up else 'DOWN'} (st {st_val}) prijs {price}", flush=True)
    last_signal = {"ut": ut_up, "st": st_up, "price": price, "ut_stop": ut_stop, "st_val": st_val, "time": int(time.time())}
    return ut_up, st_up, price, ut_stop, st_val

# ============================================================
# ORDER - UIT JE OUDE CODE
# Market order via /v5/order/create
# ============================================================
def place_order(side, qty):
    global total_buys, total_sells
    try:
        qty_int = int(qty)
        if qty_int <= 0:
            print(f"Order skip: qty 0", flush=True)
            return {}
        params = {"category": "spot", "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty_int)}
        headers = sign(params)
        r = requests.post(f"{BASE_URL}/v5/order/create", json=params, headers=headers, timeout=10)
        print(f"Order {side} {qty_int}: {r.text}", flush=True)
        res = r.json()
        if res.get("retCode") == 0:
            if side == "Buy":
                total_buys += 1
            else:
                total_sells += 1
        return res
    except Exception as e:
        print(f"Order fout: {e}", flush=True)
        traceback.print_exc()
        return {"error": str(e)}

# ============================================================
# BOT LOOP - UIT JE OUDE CODE + GAT INGEVULD
# CHECK_RUNNING, nooit stoppen, 60 sec
# ============================================================
def bot_loop():
    global CHECK_RUNNING, total_checks, last_error
    if CHECK_RUNNING:
        print("bot_loop al gestart, skip dubbele start", flush=True)
        return
    CHECK_RUNNING = True
    print("Bot loop gestart - PEPEUSDC - VOLLEDIG PERFECT", flush=True)
    send_msg("Pepebot-3 GESTART - PERFECTE VERSIE - USDC - UT + Supertrend actief", force=True)
    while True:
        try:
            total_checks += 1
            usdc, pepe = get_balance()
            if usdc is None:
                print("Geen saldo, probeer over 60 sec opnieuw - geen stop", flush=True)
                time.sleep(60)
                continue
            ut_up, st_up, price, ut_stop, st_val = check_signal()
            print(f"Check #{total_checks}: USDC {usdc} PEPE {pepe} UT {ut_up} ST {st_up} prijs {price}", flush=True)
            send_uur_update(usdc, pepe, price, ut_up, st_up, ut_stop, st_val)

            # KOPEN - HET GAT UIT JE SCREENSHOTS, NU INGEVULD:
            # UT + ST allebei UP + genoeg USDC + nog geen PEPE
            # Koop met 95% van je USDC
            if ut_up and st_up and usdc > 1 and pepe == 0:
                qty = int((usdc * USE_PERCENT) / price) if price > 0 else 0
                if qty > 0:
                    print(f"KOOP: {qty} PEPE voor {usdc} USDC @ {price}", flush=True)
                    send_msg(f"KOOP PEPE: {qty} stuks @ {price} USDC - UT UP + ST UP", force=True)
                    place_order("Buy", qty)
                else:
                    print(f"KOOP skip: qty 0 - usdc {usdc} prijs {price}", flush=True)

            # VERKOPEN - OOK UIT HET GAT:
            # UT of ST niet meer UP + wel PEPE in bezit -> verkoop ALLES
            if (not ut_up or not st_up) and pepe > 0:
                print(f"VERKOOP: {pepe} PEPE @ {price} - UT {ut_up} ST {st_up}", flush=True)
                send_msg(f"VERKOOP PEPE: {pepe} stuks @ {price} USDC - UT {ut_up} ST {st_up}", force=True)
                place_order("Sell", int(pepe))

            if pepe > 0 and usdc < 1:
                print(f"Holding PEPE {pepe}, wachten op verkoop-signaal", flush=True)
            if pepe == 0 and usdc < 1:
                print(f"Geen USDC en geen PEPE - wachten op storting", flush=True)

        except Exception as e:
            last_error = str(e)
            print(f"Loop fout, gaat door: {e}", flush=True)
            traceback.print_exc()
        time.sleep(CHECK_INTERVAL)

# ============================================================
# ROUTES - UIT JE OUDE CODE + EXTRA
# ============================================================
@app.route("/")
def home():
    usdc, pepe = get_balance()
    return f"Pepebot-3 live - USDC PERFECT - USDC {usdc} PEPE {pepe} - {SYMBOL} - Checks {total_checks}"

@app.route("/ping")
def ping():
    return "pong"

@app.route("/health")
def health():
    return jsonify({"status": "ok", "uptime_sec": int(time.time() - bot_started_at), "checks": total_checks, "symbol": SYMBOL})

@app.route("/test-key")
def test_key():
    usdc, pepe = get_balance()
    return jsonify({
        "api_key_set": bool(API_KEY),
        "accountType_used": "SPOT",
        "SYMBOL": SYMBOL,
        "CATEGORY": CATEGORY,
        "USDC": usdc,
        "PEPE": pepe,
        "telegram_set": bool(TELEGRAM_BOT_TOKEN),
        "telegram_chat_set": bool(TELEGRAM_CHAT_ID),
        "ut_factor": UT_FACTOR,
        "ut_period": UT_PERIOD,
        "st_period": ST_PERIOD,
        "st_mult": ST_MULT,
        "total_checks": total_checks,
        "total_buys": total_buys,
        "total_sells": total_sells,
        "last_signal": last_signal,
        "last_error": last_error,
        "balances_raw_spot": get_all_balances_debug()
    })

@app.route("/status")
def status():
    usdc, pepe = get_balance()
    ut_up, st_up, price, ut_stop, st_val = check_signal()
    return jsonify({"USDC": usdc, "PEPE": pepe, "prijs": price, "UT_UP": ut_up, "ST_UP": st_up, "UT_stop": ut_stop, "ST_value": st_val, "symbol": SYMBOL, "uptime_sec": int(time.time() - bot_started_at)})

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(silent=True) or {}
    print(f"Webhook ontvangen: {data}", flush=True)
    return jsonify({"ok": True, "received": data})

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
