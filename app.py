import os, time, hmac, hashlib, json, threading, requests
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = "https://api.bybit.eu"
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
INTERVAL = "15"

def log(msg):
    print(msg, flush=True)

def send_tg(msg):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
    except Exception as e:
        log(f"Telegram fout: {e}")

def _sign(ts, recv, payload=""):
    msg = ts + API_KEY + recv + payload
    return hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

def _headers(ts, recv, sign):
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json"
    }

def _extract_list(result):
    if isinstance(result, dict):
        return result.get("list", []) or []
    if isinstance(result, list):
        return result
    return []

def get_balance(coin):
    for acct in ["SPOT", "UNIFIED"]:
        try:
            if not API_KEY or not API_SECRET:
                return 0.0
            ts = str(int(time.time() * 1000))
            recv = "5000"
            query = f"accountType={acct}&coin={coin}"
            sign = _sign(ts, recv, query)
            url = f"{BASE_URL}/v5/account/wallet-balance?{query}"
            r = requests.get(url, headers=_headers(ts, recv, sign), timeout=10).json()
            if r.get("retCode") != 0:
                log(f"Saldo fout {coin} {acct}: {r}")
                continue
            lst = _extract_list(r.get("result", {}))
            if not lst:
                continue
            coins = lst[0].get("coin", []) or []
            for c in coins:
                if c.get("coin") == coin:
                    bal = float(c.get("walletBalance") or 0)
                    if bal > 0:
                        return bal
        except Exception as e:
            log(f"Saldo fout {coin} {acct}: {e}")
            continue
    return 0.0

def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers",
            params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
        lst = _extract_list(r.get("result", {}))
        if not lst:
            return 0.0
        return float(lst[0].get("lastPrice") or 0)
    except Exception as e:
        log(f"Prijs fout: {e}")
        return 0.0

def get_klines():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/kline",
            params={"category": CATEGORY, "symbol": SYMBOL, "interval": INTERVAL, "limit": 200}, timeout=10).json()
        return _extract_list(r.get("result", {}))
    except Exception as e:
        log(f"Klines fout: {e}")
        return []

def calc_utbot(klines, atr_period=14, factor=4.0):
    try:
        if len(klines) < atr_period + 5:
            return "NONE"
        klines = list(reversed(klines))
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        trs = []
        for i in range(1, len(klines)):
            trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
        atr = sum(trs[:atr_period]) / atr_period
        atr_list = [atr]
        for i in range(atr_period, len(trs)):
            atr = (atr * (atr_period - 1) + trs[i]) / atr_period
            atr_list.append(atr)
        trailings = []
        trailing = 0.0
        for idx in range(atr_period, len(closes)):
            a = atr_list[idx - atr_period]
            x_atr = a * factor
            close = closes[idx]
            if idx == atr_period:
                prev_c = closes[idx-1]
                trailing = close - x_atr if close > prev_c else close + x_atr
            else:
                if close > trailing:
                    trailing = max(trailing, close - x_atr)
                else:
                    trailing = min(trailing, close + x_atr)
            trailings.append(trailing)
        last_close = closes[-1]
        prev_close = closes[-2]
        last_trail = trailings[-1]
        prev_trail = trailings[-2]
        if last_close > last_trail and prev_close <= prev_trail:
            return "BUY"
        if last_close < last_trail and prev_close >= prev_trail:
            return "SELL"
        return "NONE"
    except Exception as e:
        log(f"UTBOT fout: {e}")
        return "NONE"

def place_order(side, qty):
    try:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        body = {"category": CATEGORY, "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty)}
        payload = json.dumps(body)
        sign = _sign(ts, recv, payload)
        r = requests.post(f"{BASE_URL}/v5/order/create", headers=_headers(ts, recv, sign), data=payload, timeout=10).json()
        log(f"Order {side} {qty}: {r}")
        return r
    except Exception as e:
        log(f"Order fout: {e}")
        return {}

def do_buy():
    try:
        money = get_balance("USDT") + get_balance("USDC")
        prijs = get_price()
        if money < 5 or prijs <= 0:
            log(f"BUY skip: geld {money} prijs {prijs}")
            return
        qty = int(money / prijs)
        if qty <= 0:
            return
        log(f"BUY: {qty} PEPE voor {money} @ {prijs}")
        place_order("Buy", qty)
        send_tg(f"PEPE GEKOCHT: {qty} @ {prijs}")
    except Exception as e:
        log(f"do_buy fout: {e}")

def do_sell():
    try:
        pepe = get_balance("PEPE")
        prijs = get_price()
        if pepe <= 0 or prijs <= 0:
            log(f"SELL skip: PEPE {pepe} prijs {prijs}")
            return
        qty = int(pepe)
        log(f"SELL: {qty} PEPE @ {prijs}")
        place_order("Sell", qty)
        send_tg(f"PEPE VERKOCHT: {qty} @ {prijs}")
    except Exception as e:
        log(f"do_sell fout: {e}")

def bot_loop():
    log("Bot loop gestart - UTBOT 4-14, 15m, SPOT via bybit.eu")
    send_tg("Bot gestart: UTBOT 4-14, 15m, SPOT via bybit.eu")
    while True:
        try:
            prijs = get_price()
            money = get_balance("USDT") + get_balance("USDC")
            pepe = get_balance("PEPE")
            sig = calc_utbot(get_klines())
            log(f"Check: Prijs {prijs} Geld {money:.2f} PEPE {pepe:.0f} Signaal {sig}")
            if sig == "BUY" and pepe * prijs < 1:
                do_buy()
            elif sig == "SELL" and pepe * prijs > 1:
                do_sell()
        except Exception as e:
            log(f"Loop fout, gaat door: {e}")
        time.sleep(60)

@app.route("/")
def home():
    return "Pepebot-3 live"

@app.route("/test-key")
def test_key():
    try:
        return jsonify({"ok": True, "USDT": get_balance("USDT"), "USDC": get_balance("USDC"), "PEPE": get_balance("PEPE"), "prijs": get_price(), "base": BASE_URL})
    except Exception as e:
        return jsonify({"ok": False, "fout": str(e)})

@app.route("/status")
def status():
    try:
        return jsonify({"prijs": get_price(), "USDT": get_balance("USDT"), "USDC": get_balance("USDC"), "PEPE": get_balance("PEPE")})
    except Exception as e:
        return jsonify({"ok": False, "fout": str(e)})

_started = False
_lock = threading.Lock()
def start_once():
    global _started
    with _lock:
        if not _started:
            _started = True
            threading.Thread(target=bot_loop, daemon=True).start()
start_once()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
