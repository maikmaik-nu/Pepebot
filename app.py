import os, time, math, threading, json, requests
from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP

app = Flask(__name__)

# --- ENV VARS (Render > Environment) ---
BYBIT_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
TRADE_USDT = float(os.environ.get("TRADE_USDT", "50"))
SYMBOL = "PEPEUSDT"
INTERVAL = "15"
KEY_VALUE = 1.0
ATR_PERIOD = 14
STOP_LOSS_PCT = float(os.environ.get("STOP_LOSS_PCT", "0.03"))
COOLDOWN_SEC = 900
STATE_FILE = "state.json"

session = HTTP(testnet=False, api_key=BYBIT_KEY, api_secret=BYBIT_SECRET)
trade_lock = threading.Lock()
bot_state = "Wachten op eerste candle..."

# --- STATE ---
def load_state():
    default = {"enabled": True, "in_position": False, "entry_price": 0.0,
               "last_signal": None, "last_candle_time": None, "last_sell_time": 0}
    try:
        with open(STATE_FILE) as f:
            default.update(json.load(f))
    except Exception:
        pass
    return default

def save_state(s):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(s, f)
    except Exception as e:
        print(f"State save error: {e}")

state = load_state()

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
        r = session.get_wallet_balance(accountType="UNIFIED")
        usdt = pepe = 0
        for c in r["result"]["list"][0]["coin"]:
            if c["coin"] == "USDT":
                usdt = float(c.get("walletBalance") or 0)
            if c["coin"] == "PEPE":
                pepe = float(c.get("walletBalance") or 0)
        return usdt, pepe
    except Exception as e:
        print(f"Balance error: {e}")
        return 0, 0

def get_price():
    try:
        tick = session.get_tickers(category="spot", symbol=SYMBOL)
        return float(tick["result"]["list"][0]["lastPrice"])
    except Exception:
        return 0

def do_buy(reason="UT Bot Buy"):
    global state
    with trade_lock:
        if state["in_position"]:
            return
        usdt, _ = get_balances()
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
            session.place_order(category="spot", symbol=SYMBOL, side="Buy",
                                orderType="Market", qty=str(qty))
            state["in_position"] = True
            state["entry_price"] = price
            save_state(state)
            send_telegram(f"✅ BUY ({reason})\n{qty} PEPE voor ~{use:.1f} USDT\nPrijs: {price}")
        except Exception as e:
            send_telegram(f"❌ Error bij buy: {e}")

def do_sell(reason="UT Bot Sell"):
    global state
    with trade_lock:
        _, pepe = get_balances()
        if pepe <= 0:
            state["in_position"] = False
            save_state(state)
            send_telegram(f"SELL genegeerd: geen PEPE ({pepe})")
            return
        qty = int(math.floor(pepe / 1000) * 1000)
        if qty <= 0:
            send_telegram("SELL genegeerd: qty te klein na afronding")
            return
        try:
            session.place_order(category="spot", symbol=SYMBOL, side="Sell",
                                orderType="Market", qty=str(qty))
            state["in_position"] = False
            state["entry_price"] = 0
            state["last_sell_time"] = time.time()
            save_state(state)
            send_telegram(f"✅ SELL ({reason})\n{qty} PEPE verkocht")
        except Exception as e:
            send_telegram(f"❌ Error bij sell: {e}")

# --- UT BOT ---
def get_candles():
    r = session.get_kline(category="spot", symbol=SYMBOL, interval=INTERVAL, limit=100)
    out = []
    for k in reversed(r["result"]["list"]):
        out.append({"time": int(k[0]), "open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]), "close": float(k[4])})
    return out

def calc_ut_signal(candles):
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    def atr_at(idx):
        if idx <= 0: return trs[0] if trs else 0
        if idx < ATR_PERIOD: return sum(trs[:idx]) / idx
        return sum(trs[idx-ATR_PERIOD:idx]) / ATR_PERIOD
    trailing = None; prev_close = None; signal = None
    for i in range(1, len(candles)):
        c = candles[i]
        xatr = atr_at(i) * KEY_VALUE
        if trailing is None:
            trailing = c["close"] - xatr; signal = "buy"
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

def check_stop_loss():
    if not state["in_position"] or not state["entry_price"]:
        return
    price = get_price()
    if price <= 0: return
    if price <= state["entry_price"] * (1 - STOP_LOSS_PCT):
        send_telegram(f"🛑 STOP-LOSS -{STOP_LOSS_PCT*100:.0f}% geraakt\nEntry: {state['entry_price']} | Nu: {price}")
        do_sell("Stop-loss")

def bot_loop():
    global bot_state
    send_telegram("🤖 Bot gestart - NIEUWE VERSIE\n✅ Stop-loss AAN (-3%)\n✅ Herstel na herstart AAN\n✅ /stop en /start werken echt\nStuur /ping om te testen")
    while True:
        try:
            if state["enabled"]:
                candles = get_candles()
                closed = candles[:-1]
                sig = calc_ut_signal(closed)
                ctime = closed[-1]["time"]
                bot_state = f"Laatste gesloten candle: {ctime} | Signaal: {sig} | In positie: {state['in_position']}"
                check_stop_loss()
                if ctime != state["last_candle_time"]:
                    if sig == "buy" and state["last_signal"] != "buy":
                        if time.time() - state["last_sell_time"] < COOLDOWN_SEC:
                            print("BUY geblokkeerd: cooldown")
                        else:
                            do_buy()
                    elif sig == "sell" and state["last_signal"] != "sell":
                        do_sell()
                    state["last_signal"] = sig
                    state["last_candle_time"] = ctime
                    save_state(state)
            else:
                bot_state = "Bot is GESTOPT via /stop"
            time.sleep(60)
        except Exception as e:
            print(f"Loop fout: {e}")
            time.sleep(60)

# --- TELEGRAM WEBHOOK ---
@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    global state
    data = request.get_json(force=True, silent=True) or {}
    msg = data.get("message", {})
    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = (msg.get("text") or "").strip().lower()
    if TG_CHAT and chat_id != str(TG_CHAT):
        print(f"Geblokkeerde Telegram poging van chat_id: {chat_id}")
        return jsonify({"ok": True})
    if text == "/start":
        state["enabled"] = True; save_state(state)
        send_telegram("▶️ Bot GESTART - ik koop weer")
    elif text == "/stop":
        state["enabled"] = False; save_state(state)
        send_telegram("⏸️ Bot GESTOPT - ik koop niks meer (posities blijven open)")
    elif text == "/ping":
        send_telegram(f"🏓 Pong! Bot leeft.\n{bot_state}")
    elif text == "/status":
        usdt, pepe = get_balances()
        send_telegram(f"📊 Status\nAan: {state['enabled']}\nIn positie: {state['in_position']}\nEntry: {state['entry_price']}\nUSDT: {usdt:.2f}\nPEPE: {pepe}\n{bot_state}")
    return jsonify({"ok": True})

@app.route("/")
@app.route("/health")
def health():
    return jsonify({"ok": True, "bot": bot_state})

# Start loop 1x
threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
