import os, math, time, threading, requests
from flask import Flask, jsonify, request
from pybit.unified_trading import HTTP

app = Flask(__name__)

# --- ENV VARS (in Render > Environment) ---
BYBIT_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
TRADE_USDT = float(os.environ.get("TRADE_USDT", "50"))
SYMBOL = os.environ.get("SYMBOL", "PEPEUSDT")
INTERVAL = os.environ.get("INTERVAL", "15")
KEY_VALUE = float(os.environ.get("KEY_VALUE", "1.0"))
ATR_PERIOD = int(os.environ.get("ATR_PERIOD", "14"))
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))

# FIX 1: domain bybit.eu, anders 10003
session = HTTP(testnet=False, api_key=BYBIT_KEY, api_secret=BYBIT_SECRET, domain="bybit.eu")

last_signal = None
last_candle_time = None
last_trade_info = "Nog geen trade"
bot_state = "Wachten op eerste candle..."
last_error = ""
telegram_offset = None

# --- TELEGRAM ---
def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT:
        print(f"Telegram niet ingesteld: {msg}", flush=True)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg}, timeout=10
        )
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)

# --- BALANCES: FIX 2: SPOT i.p.v. UNIFIED ---
def get_balances():
    try:
        r = session.get_wallet_balance(accountType="SPOT", coin="USDT")
        usdt = 0
        for c in r["result"]["list"][0]["coin"]:
            if c["coin"] == "USDT":
                usdt = float(c.get("walletBalance") or 0)
        r2 = session.get_wallet_balance(accountType="SPOT", coin="PEPE")
        pepe = 0
        for c in r2["result"]["list"][0]["coin"]:
            if c["coin"] == "PEPE":
                pepe = float(c.get("walletBalance") or c.get("availableToWithdraw") or 0)
        return usdt, pepe
    except Exception as e:
        print(f"Balance error: {e}", flush=True)
        return 0, 0

def get_price():
    try:
        tick = session.get_tickers(category="spot", symbol=SYMBOL)
        return float(tick["result"]["list"][0]["lastPrice"])
    except Exception as e:
        print(f"Price error: {e}", flush=True)
        return 0

def get_klines():
    try:
        k = session.get_kline(category="spot", symbol=SYMBOL, interval=INTERVAL, limit=200)
        lst = list(reversed(k["result"]["list"]))
        closes = [float(x[4]) for x in lst]
        highs = [float(x[2]) for x in lst]
        lows = [float(x[3]) for x in lst]
        return closes, highs, lows
    except Exception as e:
        print(f"Kline error: {e}", flush=True)
        return [], [], []

def calc_signal():
    closes, highs, lows = get_klines()
    if len(closes) < ATR_PERIOD + 2:
        return None, closes, highs, lows
    # ATR
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr = sum(trs[-ATR_PERIOD:]) / ATR_PERIOD
    # Supertrend simpele versie
    hl2 = (highs[-1] + lows[-1]) / 2
    lower = hl2 - 3 * atr
    st = "green" if closes[-1] > lower else "red"
    # UT Bot simpele versie
    ut = "buy" if closes[-1] > closes[-2] else "sell"
    if st == "green" and ut == "buy":
        return "buy", closes, highs, lows
    if st == "red":
        return "sell", closes, highs, lows
    return None, closes, highs, lows

def do_buy(reason="UT Bot Buy"):
    global last_trade_info
    usdt, pepe = get_balances()
    if usdt < 10:
        send_telegram(f"❌ BUY genegeerd: te weinig USDT ({usdt:.2f})")
        return
    use = min(TRADE_USDT, usdt * 0.98)
    price = get_price()
    if price <= 0:
        send_telegram("❌ BUY mislukt: geen prijs")
        return
    qty = int(math.floor((use / price) / 1000) * 1000)
    if qty <= 0:
        send_telegram("❌ BUY genegeerd: qty te klein")
        return
    try:
        session.place_order(category="spot", symbol=SYMBOL, side="Buy", orderType="Market", qty=str(qty))
        last_trade_info = f"BUY {qty} PEPE @ {price} - {reason}"
        send_telegram(f"✅ {last_trade_info}")
    except Exception as e:
        send_telegram(f"❌ BUY mislukt: {e}")

def do_sell(reason="UT Bot Sell"):
    global last_trade_info
    usdt, pepe = get_balances()
    if pepe <= 0:
        send_telegram("❌ SELL genegeerd: geen PEPE")
        return
    qty = int(math.floor(pepe / 1000) * 1000)
    if qty <= 0:
        send_telegram("❌ SELL genegeerd: qty te klein")
        return
    try:
        session.place_order(category="spot", symbol=SYMBOL, side="Sell", orderType="Market", qty=str(qty))
        last_trade_info = f"SELL {qty} PEPE - {reason}"
        send_telegram(f"✅ {last_trade_info}")
    except Exception as e:
        send_telegram(f"❌ SELL mislukt: {e}")

def auto_loop():
    global last_signal, last_candle_time, bot_state, last_error
    while True:
        try:
            signal, closes, highs, lows = calc_signal()
            usdt, pepe = get_balances()
            price = get_price()
            bot_state = f"USDT={usdt:.2f} PEPE={pepe} PRICE={price} SIGNAL={signal}"
            print(bot_state, flush=True)
            if signal == "buy" and last_signal != "buy":
                do_buy()
                last_signal = "buy"
            elif signal == "sell" and last_signal != "sell":
                if pepe > 0:
                    do_sell()
                last_signal = "sell"
            last_error = ""
        except Exception as e:
            last_error = str(e)
            print(f"LOOP FAIL: {e}", flush=True)
        time.sleep(POLL_SECONDS)

@app.route("/")
def home():
    return "Pepebot-3 draait"

@app.route("/test-key")
def test_key():
    usdt, pepe = get_balances()
    price = get_price()
    return jsonify({
        "api_key_set": bool(BYBIT_KEY),
        "domain": "bybit.eu",
        "accountType": "SPOT",
        "symbol": SYMBOL,
        "usdt": usdt,
        "pepe": pepe,
        "price": price,
        "last_trade": last_trade_info,
        "state": bot_state,
        "last_error": last_error
    })

@app.route("/check")
def check():
    usdt, pepe = get_balances()
    price = get_price()
    signal, _, _, _ = calc_signal()
    return jsonify({"USDT": usdt, "PEPE": pepe, "price": price, "signal": signal, "state": bot_state})

@app.route("/buy")
def buy_route():
    do_buy("Manual Buy")
    return jsonify({"status": "BUY SENT", "last_trade": last_trade_info})

@app.route("/sell")
def sell_route():
    do_sell("Manual Sell")
    return jsonify({"status": "SELL SENT", "last_trade": last_trade_info})

threading.Thread(target=auto_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
