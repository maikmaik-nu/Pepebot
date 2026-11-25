import os, time, hmac, hashlib, json, threading, requests, math
from flask import Flask, jsonify
app = Flask(__name__)

BASE_URL = "https://api.bybit.eu"
API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

SYMBOL = "PEPEUSDC"
BUY_AMOUNT = 100.0
USE_ALL_BALANCE = True
CHECK_SECONDS = 60
TAKE_PROFIT_PCT = 5.0
STOP_LOSS_PCT = -3.0

running = False
bought_price = 0.0
bought_qty = 0
last_signal = "NONE"
trade_count = 0

def log(msg):
    print(msg, flush=True)

def send_tg(msg):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
    except Exception as e:
        log(f"Telegram fout: {e}")

def make_sign(timestamp, recv_window, payload_str):
    msg = timestamp + API_KEY + recv_window + payload_str
    return hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

def headers_for(ts, recv, sign):
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "X-BAPI-SIGN-TYPE": "2",
        "Content-Type": "application/json"
    }

def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {}

def bybit_get(path, params):
    if not API_KEY or not API_SECRET:
        log("Balance fout: geen API key in Render")
        return {"retCode": -1, "retMsg": "no api key"}
    try:
        ts = str(int(time.time() * 1000)); recv = "20000"
        query = "&".join(f"{k}={params[k]}" for k in sorted(params))
        sign = make_sign(ts, recv, query)
        r = safe_json(requests.get(BASE_URL + path, params=params, headers=headers_for(ts, recv, sign), timeout=10))
        return r
    except Exception as e:
        log(f"【entity-BYBIT¦canonical_name=BYBIT】 GET FAIL {path}: {e}")
        return {"retCode": -1, "retMsg": str(e)}

def bybit_post(path, body):
    if not API_KEY or not API_SECRET:
        return {"retCode": -1, "retMsg": "no api key"}
    try:
        ts = str(int(time.time() * 1000)); recv = "20000"
        payload = json.dumps(body, separators=(",", ":"))
        sign = make_sign(ts, recv, payload)
        r = safe_json(requests.post(BASE_URL + path, headers=headers_for(ts, recv, sign), data=payload, timeout=10))
        print(f"【entity-BYBIT¦canonical_name=BYBIT】 POST {path} body={payload} -> {r.get('retCode')} {str(r)[:300]}", flush=True)
        return r
    except Exception as e:
        log(f"【entity-BYBIT¦canonical_name=BYBIT】 POST FAIL {path}: {e}")
        return {"retCode": -1, "retMsg": str(e)}

def get_balance_unified(coin):
    try:
        query = "accountType=UNIFIED&coin=" + coin
        ts = str(int(time.time() * 1000)); recv = "20000"
        sign = make_sign(ts, recv, query)
        r = safe_json(requests.get(BASE_URL + "/v5/account/wallet-balance?" + query, headers=headers_for(ts, recv, sign), timeout=10))
        if r.get("retCode") != 0:
            log(f"Balance UNIFIED fail {coin}: {r.get('retCode')} {r.get('retMsg')}")
            return 0.0
        lst = r.get("result", {}).get("list", []) or []
        if not lst:
            return 0.0
        for c in lst[0].get("coin", []) or []:
            if c.get("coin") == coin:
                try:
                    return float(c.get("walletBalance") or 0)
                except Exception:
                    return 0.0
        return 0.0
    except Exception as e:
        log(f"Balance UNIFIED exception {coin}: {e}")
        return 0.0

def get_balance_funding(coin):
    # Alleen als UNIFIED 0 is, via asset-endpoint, NIET via wallet-balance
    try:
        query = "accountType=FUND&coin=" + coin
        ts = str(int(time.time() * 1000)); recv = "20000"
        sign = make_sign(ts, recv, query)
        r = safe_json(requests.get(BASE_URL + "/v5/asset/transfer/query-account-coins-balance?" + query, headers=headers_for(ts, recv, sign), timeout=10))
        if r.get("retCode") != 0:
            return 0.0
        res = r.get("result", {})
        lst = res.get("list", []) if isinstance(res, dict) else []
        if isinstance(res, dict) and res.get("coin"):
            lst = [res]
        for c in lst:
            if isinstance(c, dict) and c.get("coin") == coin:
                try:
                    return float(c.get("balance") or c.get("walletBalance") or 0)
                except Exception:
                    return 0.0
        return 0.0
    except Exception as e:
        log(f"Balance FUND exception {coin}: {e}")
        return 0.0

def get_balance():
    if not API_KEY or not API_SECRET:
        log("Balance fout: geen API key in Render")
        return 0.0, 0.0, 0.0
    usdc = get_balance_unified("USDC")
    usdt = get_balance_unified("USDT")
    pepe = get_balance_unified("PEPE")
    if usdc == 0:
        usdc = get_balance_funding("USDC")
    if pepe == 0:
        pepe = get_balance_funding("PEPE")
    return usdt, usdc, pepe

def get_klines(symbol):
    try:
        r = safe_json(requests.get(BASE_URL + "/v5/market/kline", params={"category": "spot", "symbol": symbol, "interval": "15", "limit": 200}, timeout=10))
        if r.get("retCode") != 0:
            return []
        lst = r.get("result", {}).get("list", []) or []
        lst = list(reversed(lst))
        out = []
        for k in lst:
            try:
                out.append({"open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])})
            except Exception:
                continue
        return out
    except Exception as e:
        log(f"Kline fout: {e}")
        return []

def calc_atr(klines, period):
    try:
        trs = []
        for i in range(1, len(klines)):
            trs.append(max(klines[i]["high"] - klines[i]["low"], abs(klines[i]["high"] - klines[i-1]["close"]), abs(klines[i]["low"] - klines[i-1]["close"])))
        if not trs:
            return 0
        return sum(trs[-period:]) / min(len(trs), period)
    except Exception:
        return 0

def calc_supertrend(klines):
    try:
        atr = calc_atr(klines, 10)
        closes = [k["close"] for k in klines]
        hl2 = [(k["high"] + k["low"]) / 2 for k in klines]
        upper = [hl2[i] + 3 * atr for i in range(len(klines))]
        lower = [hl2[i] - 3 * atr for i in range(len(klines))]
        st_up = True
        for i in range(1, len(klines)):
            if closes[i] > upper[i-1]:
                st_up = True
            elif closes[i] < lower[i-1]:
                st_up = False
        return st_up
    except Exception:
        return False

def calc_utbot(klines):
    try:
        atr14 = calc_atr(klines, 14)
        x_atr = 4 * atr14
        closes = [k["close"] for k in klines]
        hl2 = [(k["high"] + k["low"]) / 2 for k in klines]
        return closes[-1] > closes[-2] and closes[-1] > (hl2[-1] - x_atr)
    except Exception:
        return False

def calc_signals(klines):
    if len(klines) < 50:
        return False, False, 0.0
    price = klines[-1]["close"]
    st_up = calc_supertrend(klines)
    ut_up = calc_utbot(klines)
    log(f"Signals: ST={st_up} UT={ut_up} prijs {price}")
    return st_up, ut_up, price

def place_buy(symbol, usdc_amount, price):
    global bought_price, bought_qty
    if price <= 0:
        return {}
    qty_raw = math.floor(usdc_amount / price)
    qty = math.floor(qty_raw / 1000) * 1000
    if qty <= 0:
        log(f"Buy afgebroken: qty 0, bedrag {usdc_amount} prijs {price}")
        return {}
    body = {"category": "spot", "symbol": symbol, "side": "Buy", "orderType": "Market", "qty": str(int(qty))}
    log(f"ORDER Buy {symbol} qty {qty} voor {usdc_amount} USDC @ {price}")
    r = bybit_post("/v5/order/create", body)
    log(f"ORDER Buy resultaat: {r}")
    if r.get("retCode") == 0:
        bought_price = price
        bought_qty = qty
    return r

def place_sell(symbol, qty, price, reason):
    if qty <= 0:
        return {}
    qty = math.floor(qty / 1000) * 1000
    if qty <= 0:
        return {}
    body = {"category": "spot", "symbol": symbol, "side": "Sell", "orderType": "Market", "qty": str(int(qty))}
    log(f"ORDER Sell {symbol} qty {qty} reden {reason} @ {price}")
    r = bybit_post("/v5/order/create", body)
    log(f"ORDER Sell resultaat: {r}")
    return r

def bot_loop():
    global running, last_signal, trade_count, bought_price, bought_qty
    if running:
        return
    running = True
    log("Bot loop gestart - Pepebot-3 FULL - alles gecheckt - 100 USDC + rente-op-rente")
    send_tg("Pepebot-3 gestart - 100 USDC + rente-op-rente live")
    while True:
        try:
            usdt, usdc, pepe = get_balance()
            klines = get_klines(SYMBOL)
            st_up, ut_up, price = calc_signals(klines)
            up = st_up and ut_up
            down = (not st_up) and (not ut_up)
            sig = "UP" if up else "DOWN" if down else "NEUTRAAL"
            if sig != last_signal:
                log(f"Signaal veranderd: {last_signal} -> {sig}")
                last_signal = sig
            log(f"Check: USDT {usdt:.2f} USDC {usdc:.2f} PEPE {pepe:.0f} SYMBOL {SYMBOL} UP={up} prijs {price} bought@{bought_price} qty {bought_qty}")

            if pepe > 0:
                pnl_pct = ((price - bought_price) / bought_price * 100) if bought_price > 0 else 0
                log(f"Holding PEPE: winst {pnl_pct:.2f}% TP {TAKE_PROFIT_PCT}% SL {STOP_LOSS_PCT}%")
                should_sell = False
                reason = ""
                if bought_price > 0 and pnl_pct >= TAKE_PROFIT_PCT:
                    should_sell = True; reason = f"TAKE PROFIT {pnl_pct:.2f}%"
                elif bought_price > 0 and pnl_pct <= STOP_LOSS_PCT:
                    should_sell = True; reason = f"STOP LOSS {pnl_pct:.2f}%"
                elif down:
                    should_sell = True; reason = "DOWN signaal ST+UT"
                if should_sell:
                    res = place_sell(SYMBOL, pepe, price, reason)
                    if res.get("retCode") == 0:
                        send_tg(f"PEPE VERKOCHT {reason} @ {price} winst {pnl_pct:.2f}% - winst wordt weer geinvesteerd")
                        bought_price = 0.0; bought_qty = 0; trade_count += 1
                    else:
                        send_tg(f"SELL afgewezen: {res.get('retMsg')}")
            elif up and usdc >= 5 and pepe == 0:
                buy_amount = usdc * 0.99 if USE_ALL_BALANCE else BUY_AMOUNT
                log(f"Rente-op-rente: koop voor {buy_amount:.2f} van {usdc:.2f} USDC")
                res = place_buy(SYMBOL, buy_amount, price)
                if res.get("retCode") == 0:
                    send_tg(f"PEPE GEKOCHT {SYMBOL} voor {buy_amount:.2f} USDC @ {price} - rente op rente")
                    trade_count += 1
                else:
                    send_tg(f"BUY afgewezen: {res.get('retMsg')}")
        except Exception as e:
            log(f"Loop fout: {e}")
        time.sleep(CHECK_SECONDS)

@app.route("/")
def home():
    return "Pepebot-3 live - 100 + rente-op-rente"

@app.route("/status")
def status():
    usdt, usdc, pepe = get_balance()
    return jsonify({"symbol": SYMBOL, "usdt": usdt, "usdc": usdc, "pepe": pepe, "bought_price": bought_price, "bought_qty": bought_qty, "last_signal": last_signal, "trades": trade_count})

@app.route("/test-key")
def test_key():
    usdt, usdc, pepe = get_balance()
    return jsonify({"retCode": 0, "usdt": usdt, "usdc": usdc, "pepe": pepe})

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
