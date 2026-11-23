import os, time, math, threading, requests
from flask import Flask, jsonify
from pybit.unified_trading import HTTP

app = Flask(__name__)

# --- ENV VARS ---
BYBIT_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
TRADE_USDT = float(os.environ.get("TRADE_USDT", "50"))
SYMBOL = "PEPEUSDT"
INTERVAL = "15"
KEY_VALUE = 1.0
ATR_PERIOD = 14

# FIX: forceer globale endpoint, niet eu.com die DNS-fout geeft
session = HTTP(testnet=False, api_key=BYBIT_KEY, api_secret=BYBIT_SECRET, endpoint="https://api.bybit.com")

last_signal = None
last_candle_time = None
bot_state = "Wachten op eerste candle..."

# --- TELEGRAM ---
def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT:
        print(f"Telegram niet ingesteld: {msg}", flush=True)
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}", flush=True)

def get_balances():
    try:
        r = session.get_wallet_balance(accountType="UNIFIED", coin="USDT")
        usdt = 0
        for c in r["result"]["list"][0]["coin"]:
            if c["coin"] == "USDT":
                usdt = float(c.get("walletBalance") or 0)
        r2 = session.get_wallet_balance(accountType="UNIFIED", coin="PEPE")
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

def do_buy(reason="UT Bot Buy"):
    usdt, pepe = get_balances()
    if usdt < 10:
        send_telegram(f"❌ BUY genegeerd: te weinig USDT ({usdt})")
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
        send_telegram(f"✅ BUY uitgevoerd ({reason})\n{qty} PEPE voor ~{use:.1f} USDT\nPrijs: {price}")
    except Exception as e:
        send_telegram(f"❌ Error bij buy: {e}")

def do_sell(reason="UT Bot Sell"):
    usdt, pepe = get_balances()
    print(f"SELL signaal - PEPE walletBalance: {pepe}", flush=True)
    if pepe <= 0:
        send_telegram(f"SELL genegeerd: geen PEPE ({pepe})")
        return
    qty = int(math.floor(pepe / 1000) * 1000)
    if qty <= 0:
        send_telegram("SELL genegeerd: qty te klein na afronding")
        return
    try:
        session.place_order(category="spot", symbol=SYMBOL, side="Sell", orderType="Market", qty=str(qty))
        send_telegram(f"✅ SELL uitgevoerd ({reason})\n{qty} PEPE verkocht")
    except Exception as e:
        send_telegram(f"❌ Error bij sell: {e}")

# --- UT BOT ---
def get_candles():
    try:
        r = session.get_kline(category="spot", symbol=SYMBOL, interval=INTERVAL, limit=100)
        out = []
        for k in reversed(r["result"]["list"]):
            out.append({"time": int(k[0]), "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4])})
        return out
    except Exception as e:
        print(f"Kline error: {e}", flush=True)
        return []

def calc_ut_signal(candles):
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    def atr_at(idx):
        if idx < ATR_PERIOD:
            return sum(trs[:idx]) / max(1, idx) if idx > 0 else trs[0]
        return sum(trs[idx-ATR_PERIOD:idx]) / ATR_PERIOD
    trailing = None
    prev_close = None
    signal = None
    for i in range(1, len(candles)):
        c = candles[i]
        atr = atr_at(i)
        xatr = atr * KEY_VALUE
        if trailing is None:
            trailing = c["close"] - xatr
            signal = "buy"
        else:
            if prev_close is not None and prev_close > trailing and c["close"] < trailing:
                signal = "sell"
            elif prev_close is not None and prev_close < trailing and c["close"] > trailing:
                signal = "buy"
            if signal == "buy":
                trailing = max(trailing, c["close"] - xatr)
            else:
                trailing = min(trailing, c["close"] + xatr)
        prev_close = c["close"]
    return signal

def bot_loop():
    global last_signal, last_candle_time, bot_state
    send_telegram("🤖 Bot gestart\nUT Bot zelf berekend, 15m PEPEUSDT.")
    while True:
        try:
            candles = get_candles()
            if not candles or len(candles) < 20:
                print("Skipping cycle: geen candles", flush=True)
                time.sleep(60)
                continue
            closed = candles[:-1]
            sig = calc_ut_signal(closed)
            ctime = closed[-1]["time"]
            usdt, pepe = get_balances()
            price = get_price()
            print(f"USDT={usdt:.2f} PEPE={pepe} PRICE={price} SIGNAL={sig}", flush=True)
            bot_state = f"Laatste gesloten candle: {ctime} | Signaal: {sig} | Laatste trade: {last_signal}"
            if ctime != last_candle_time and sig != last_signal and sig is not None:
                last_candle_time = ctime
                last_signal = sig
                if sig == "buy":
                    do_buy("UT Bot Buy - nieuwe candle")
                elif sig == "sell":
                    do_sell("UT Bot Sell - nieuwe candle")
            else:
                if last_candle_time is None:
                    last_candle_time = ctime
                    last_signal = sig
        except Exception as e:
            print(f"Loop error: {e}", flush=True)
        time.sleep(60)

# --- FLASK ---
@app.route("/")
def home():
    return jsonify({"status": "running", "bot_state": bot_state, "last_signal": last_signal})

@app.route("/status")
def status():
    usdt, pepe = get_balances()
    return jsonify({"usdt": usdt, "pepe": pepe, "bot_state": bot_state, "last_signal": last_signal})

@app.route("/health")
def health():
    return "OK", 200

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
