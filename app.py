import os, time, requests, hmac, hashlib, json, threading
from flask import Flask, request, jsonify

app = Flask(__name__)

BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
INTERVAL = "15"
KEY_VALUE = 4
ATR_PERIOD = 14

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def _sign(ts, recv, payload=""):
    msg = ts + BYBIT_API_KEY + recv + payload
    return hmac.new(BYBIT_API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

def _headers(ts, recv, sign):
    return {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json"
    }

def get_price():
    try:
        r = requests.get("https://api.bybit.com/v5/market/tickers",
                          params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
        return float(r["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print(f"Prijs error: {e}")
        return 0

def get_balance(coin):
    try:
        ts = str(int(time.time()*1000))
        recv = "5000"
        query = f"accountType=UNIFIED&coin={coin}"
        sign = _sign(ts, recv, query)
        url = f"https://api.bybit.com/v5/account/wallet-balance?{query}"
        r = requests.get(url, headers=_headers(ts, recv, sign), timeout=10).json()
        coins = r["result"]["list"][0]["coin"]
        for c in coins:
            if c["coin"] == coin:
                return float(c.get("walletBalance") or 0)
        return 0.0
    except Exception as e:
        print(f"Saldo error {coin}: {e}")
        return 0.0

def get_free_usdt():
    return get_balance("USDT")

def get_pepe_balance():
    return get_balance("PEPE")

def is_in_positie():
    return get_pepe_balance() >= 1000

def place_order(side, qty):
    ts = str(int(time.time()*1000))
    recv = "5000"
    body = {"category": "spot", "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty)}
    body_str = json.dumps(body)
    sign = _sign(ts, recv, body_str)
    r = requests.post("https://api.bybit.com/v5/order/create",
                      headers=_headers(ts, recv, sign), data=body_str, timeout=10)
    print(f"ORDER {side} qty {qty}: {r.text}")
    try:
        return r.json()
    except:
        return {"retCode": -1, "retMsg": r.text}

def do_buy():
    usdt = get_free_usdt()
    print(f"UT BOT BUY - USDT: {usdt}")
    if usdt < 10:
        send_telegram(f"BUY: te weinig USDT ({usdt:.2f})")
        return
    bedrag = usdt * 0.98
    prijs = get_price()
    if prijs <= 0:
        return
    qty = int((bedrag / prijs) // 1000 * 1000)
    if qty < 1000:
        send_telegram("BUY: qty te klein")
        return
    res = place_order("Buy", qty)
    if res.get("retCode") == 0:
        send_telegram(f"ECHT GEKOCHT {SYMBOL} Bedrag: {bedrag:.2f} USDT Qty: {qty} Prijs: {prijs}")
    else:
        send_telegram(f"BUY mislukt: {res}")

def do_sell():
    pepe = get_pepe_balance()
    print(f"UT BOT SELL - PEPE: {pepe}")
    if pepe < 1000:
        send_telegram(f"SELL: te weinig PEPE ({pepe})")
        return
    qty = int(pepe // 1000 * 1000)
    res = place_order("Sell", qty)
    if res.get("retCode") == 0:
        send_telegram(f"ECHT VERKOCHT {SYMBOL} Qty: {qty}")
    else:
        send_telegram(f"SELL mislukt: {res}")

def get_klines():
    try:
        r = requests.get("https://api.bybit.com/v5/market/kline",
                         params={"category": CATEGORY, "symbol": SYMBOL, "interval": INTERVAL, "limit": 200},
                         timeout=10).json()
        klines = r["result"]["list"][::-1]
        closes = [float(k[4]) for k in klines]
        highs = [float(k[2]) for k in klines]
        lows = [float(k[3]) for k in klines]
        return closes, highs, lows
    except Exception as e:
        print(f"Kline error: {e}")
        return [], [], []

def utbot_signal():
    closes, highs, lows = get_klines()
    if len(closes) < 30:
        return "NONE"
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    atr = sum(trs[-ATR_PERIOD:]) / ATR_PERIOD
    nloss = KEY_VALUE * atr
    trailing = 0
    last_signal = "NONE"
    for i in range(1, len(closes)):
        close = closes[i]
        prev_close = closes[i-1]
        if close > trailing and prev_close > trailing:
            trailing = max(trailing, close - nloss)
        elif close < trailing and prev_close < trailing:
            trailing = min(trailing, close + nloss)
        else:
            trailing = close - nloss if close > trailing else close + nloss
        if close > trailing:
            last_signal = "BUY"
        elif close < trailing:
            last_signal = "SELL"
    return last_signal

last_handled_signal = None

def bot_loop():
    global last_handled_signal
    print("Bot loop gestart - UT Bot zelf berekenen")
    while True:
        try:
            signaal = utbot_signal()
            in_pos = is_in_positie()
            print(f"Check - UT Bot: {signaal} - In positie: {in_pos} - Prijs: {get_price()}")
            if signaal == "BUY" and not in_pos and last_handled_signal != "BUY":
                do_buy()
                last_handled_signal = "BUY"
            elif signaal == "SELL" and in_pos and last_handled_signal != "SELL":
                do_sell()
                last_handled_signal = "SELL"
        except Exception as e:
            print(f"Loop error: {e}")
        time.sleep(60)

@app.route("/")
def home():
    return "Pepebot FINAL - UT Bot zelf - GEEN stop-loss - Rente-op-rente AAN - GEEN Supertrend - GEEN TradingView"

@app.route("/ping")
def ping():
    return jsonify({"status": "Pong", "in_positie": is_in_positie(), "utbot": "4,14"})

@app.route("/status")
def status():
    return jsonify({"usdt": get_free_usdt(), "pepe": get_pepe_balance(), "prijs": get_price(), "in_positie": is_in_positie(), "utbot_signaal": utbot_signal()})

@app.route("/webhook", methods=["POST"])
def webhook():
    return jsonify({"ok": True, "msg": "Bot berekent UT Bot nu zelf, geen webhook meer nodig"})

@app.route("/telegram", methods=["POST"])
def telegram():
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("message", {}).get("text", "")
    if text == "/ping":
        send_telegram("PONG - Ik ben LIVE - UT Bot zelf - GEEN stop-loss - GEEN Supertrend - GEEN TradingView")
    elif text == "/status":
        send_telegram(f"Status: USDT {get_free_usdt():.2f} - PEPE {get_pepe_balance():.0f} - In positie: {is_in_positie()} - UT Bot: {utbot_signal()}")
    return jsonify({"ok": True})

if __name__ == "__main__":
    send_telegram("Bot gestart - UT Bot zelf berekenen - Rente-op-rente AAN - GEEN stop-loss - GEEN Supertrend - GEEN TradingView - Start met 100")
    t = threading.Thread(target=bot_loop, daemon=True)
    t.start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
