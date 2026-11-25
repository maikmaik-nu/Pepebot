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

def bybit_post(path, body):
    if not API_KEY or not API_SECRET:
        return {"retCode": -1, "retMsg": "no api key"}
    try:
        ts = str(int(time.time() * 1000)); recv = "20000"
        payload = json.dumps(body, separators=(",", ":"))
        sign = make_sign(ts, recv, payload)
        r = safe_json(requests.post(BASE_URL + path, headers=headers_for(ts, recv, sign), data=payload, timeout=10))
        print(f"【entity-BYBIT¦canonical_name=BYBIT】 POST {path} -> {r.get('retCode')} {str(r)[:300]}", flush=True)
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

def get_balance():
    if not API_KEY or not API_SECRET:
        log("Balance fout: geen API key in Render")
        return 0.0, 0.0, 0.0
    usdc = get_balance_unified("USDC")
    usdt = get_balance_unified("USDT")
    pepe = get_balance_unified("PEPE")
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

# ===== BUY MET OPKNIP-FIX VOOR 170381 =====
def place_buy(symbol, usdc_amount, price):
    global bought_price, bought_qty
    if price <= 0:
        return {}
    qty_total = math.floor((usdc_amount / price) / 1000) * 1000
    if qty_total <= 0:
        log(f"Buy afgebroken: qty 0, bedrag {usdc_amount} prijs {price}")
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
        if
