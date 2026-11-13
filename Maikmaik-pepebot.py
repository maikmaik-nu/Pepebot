import os, time, math, threading, requests
from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP

app = Flask(__name__)

# --- ENV VARS (in Render > Environment) ---
BYBIT_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
TRADE_USDT = float(os.environ.get("TRADE_USDT", "50"))  # hoeveel USDT per trade
SYMBOL = "PEPEUSDT"
INTERVAL = "15"  # 15m zoals in TradingView
KEY_VALUE = 1.0  # UT Bot sensitivity - 1.0 = standaard, hoger = rustiger
ATR_PERIOD = 14

session = HTTP(testnet=False, api_key=BYBIT_KEY, api_secret=BYBIT_SECRET)
last_signal = None  # "buy" of "sell" van laatste gesloten candle
last_candle_time = None
bot_state = "Wachten op eerste candle..."

# --- TELEGRAM ---
def send_telegram(msg):
    if not TG_TOKEN or not TG_CHAT:
        print(f"Telegram niet ingesteld: {msg}")
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

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
                # FIX: walletBalance is leidend, niet availableToWithdraw
                pepe = float(c.get("walletBalance") or c.get("availableToWithdraw") or 0)
        return usdt, pepe
    except Exception as e:
        print(f"Balance error: {e}")
        return 0, 0

def do_buy(reason="UT Bot Buy"):
    usdt, pepe = get_balances()
    if usdt < 10:
        send_telegram(f"❌ BUY genegeerd: te weinig USDT ({usdt})")
        return
    use = min(TRADE_USDT, usdt * 0.98)
    # Prijs ophalen voor qty berekening
    try:
        tick = session.get_tickers(category="spot", symbol=SYMBOL)
        price = float(tick["result"]["list"][0]["lastPrice"])
    except:
        price = 0
    if price <= 0:
        send_telegram("❌ BUY mislukt: geen prijs")
        return
    qty = int(math.floor((use / price) / 1000) * 1000)  # FIX: veelvoud van 1000
    if qty <= 0:
        send_telegram("❌ BUY genegeerd: qty te klein")
        return
    try:
        session.place_order(category="spot", symbol=SYMBOL, side="Buy",
                            orderType="Market", qty=str(qty))
        send_telegram(f"✅ BUY uitgevoerd ({reason})\n{qty} PEPE voor ~{use:.1f} USDT\nPrijs: {price}")
    except Exception as e:
        send_telegram(f"❌ Error bij buy: {e}")

def do_sell(reason="UT Bot Sell"):
    usdt, pepe = get_balances()
    print(f"SELL signaal - PEPE walletBalance: {pepe}")
    if pepe <= 0:
        send_telegram(f"SELL genegeerd: geen PEPE ({pepe})")
        return
    qty = int(math.floor(pepe / 1000) * 1000)  # FIX: veelvoud van 1000
    if qty <= 0:
        send_telegram("SELL genegeerd: qty te klein na afronding")
        return
    try:
        session.place_order(category="spot", symbol=SYMBOL, side="Sell",
                            orderType="Market", qty=str(qty))
        send_telegram(f"✅ SELL uitgevoerd ({reason})\n{qty} PEPE verkocht")
    except Exception as e:
        send_telegram(f"❌ Error bij sell: {e}")

# --- UT BOT BEREKENING (zelfde als TradingView UT Bot Alerts 4,14) ---
def get_candles():
    r = session.get_kline(category="spot", symbol=SYMBOL, interval=INTERVAL, limit=100)
    # [start, open, high, low, close, volume, turnover]
    out = []
    for k in reversed(r["result"]["list"]):
        out.append({"time": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4])})
    return out

def calc_ut_signal(candles):
    # ATR
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    # Simpele ATR (SMA van TR)
    def atr_at(idx):
        if idx < ATR_PERIOD: return sum(trs[:idx]) / max(1, idx) if idx>0 else trs[0]
        return sum(trs[idx-ATR_PERIOD:idx]) / ATR_PERIOD
    # Trailing stop zoals UT Bot
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
            # trailing updaten
            if signal == "buy":
                trailing = max(trailing, c["close"] - xatr)
            else:
                trailing = min(trailing, c["close"] + xatr)
        prev_close = c["close"]
    return signal

def bot_loop():
    global last_signal, last_candle_time, bot_state
    send_telegram("🤖 Bot gestart - 100% gratis modus\nGeen TradingView nodig, ik reken UT Bot zelf uit.\nStuur /status voor je positie.")
    while True:
        try:
            candles = get_candles()
            # Alleen gesloten candles: laatste candle is nog open, dus -2 is laatste gesloten
            closed = candles[:-1]
            sig = calc_ut_signal(closed)
            ctime = closed[-1]["time"]
            bot_state = f"Laatste gesloten candle: {sig.upper()} | Prijs: {closed[-1]['close']}"
            if ctime != last_candle_time:
                # Nieuwe gesloten candle
                if last_signal is not None and sig != last_signal:
                    if sig == "buy":
                        send_telegram("📈 UT Bot flip naar BUY - ik koop nu")
                        do_buy("UT Bot flip")
                    else:
                        send_telegram("📉 UT Bot flip naar SELL - ik verkoop nu")
                        do_sell("UT Bot flip")
                last_signal = sig
                last_candle_time = ctime
                print(f"Nieuwe candle: {sig} @ {closed[-1]['close']}")
            time.sleep(60)
        except Exception as e:
            print(f"Loop error: {e}")
            time.sleep(60)

# --- TELEGRAM COMMANDS - DIT MISTE EERDER, DAAROM ANTWOORDDE HIJ NOOIT ---
def telegram_polling():
    offset = None
    while True:
        try:
            url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
            params = {"timeout": 30}
            if offset: params["offset"] = offset
            r = requests.get(url, params=params, timeout=35).json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                msg = u.get("message", {})
                text = (msg.get("text") or "").strip().lower()
                chat = str(msg.get("chat", {}).get("id", ""))
                if chat != str(TG_CHAT):
                    continue
                if text == "/start":
                    send_telegram("👋 Hey! Ik ben je PEPE bot.\n\nCommando's:\n/status - zie je positie\n/buy - koop nu handmatig\n/sell - verkoop nu handmatig\n/help - dit menu\n\nIk trade automatisch op UT Bot 15m, 100% gratis zonder TradingView.")
                elif text == "/status":
                    usdt, pepe = get_balances()
                    send_telegram(f"📊 STATUS\nUSDT: {usdt:.2f}\nPEPE: {pepe:.0f}\nLaatste signaal: {last_signal}\n{bot_state}")
                elif text == "/buy":
                    send_telegram("🛒 Handmatige BUY gestart...")
                    do_buy("Handmatig via /buy")
                elif text == "/sell":
                    send_telegram("💰 Handmatige SELL gestart...")
                    do_sell("Handmatig via /sell")
                elif text == "/help":
                    send_telegram("Commando's: /start /status /buy /sell")
                elif text.startswith("/setwebhook"):
                    # Dit is GEEN Telegram commando, mensen sturen dit per ongeluk
                    send_telegram("⚠️ /setwebhook hoef je NIET naar mij te sturen.\nDat was voor TradingView. Ik werk nu zonder TradingView.\nGebruik /status om te checken of ik draai.")
                elif text:
                    send_telegram("Ik snap alleen: /start /status /buy /sell")
        except Exception as e:
            print(f"Telegram polling error: {e}")
            time.sleep(5)

# --- FLASK ROUTES ---
@app.route("/")
def home():
    return "PEPE Bot Live - Gratis modus (geen TradingView nodig)", 200

@app.route("/ping")
def ping():
    return "pong", 200

# Webhook blijft bestaan voor als je later toch TradingView Premium neemt
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json(force=True, silent=True) or {}
    action = str(data.get("action", "")).lower()
    if action == "buy":
        threading.Thread(target=do_buy, args=("Webhook Buy",)).start()
        return jsonify({"status": "buy gestart"}), 200
    if action == "sell":
        threading.Thread(target=do_sell, args=("Webhook Sell",)).start()
        return jsonify({"status": "sell gestart"}), 200
    return jsonify({"status": "unknown action"}), 200

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    threading.Thread(target=telegram_polling, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=
