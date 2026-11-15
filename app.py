import os
import math
import time
import threading
import requests
from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP

app = Flask(__name__)

# --- Instellingen ---
SYMBOOL = "PEPEUSDT"
INTERVAL = "15"
ATR_PERIOD_UT = 14
UT_MULT = 4.0
ST_ATR = 10
ST_MULT = 3.0

BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

session = HTTP(
    testnet=False,
    api_key=BYBIT_API_KEY,
    api_secret=BYBIT_API_SECRET
)

bot_state = {
    "in_positie": False,
    "signaal": "geen",
    "laatste_candle": 0,
    "entry_prijs": 0.0,
    "actief": True,
    "laatste_ut": None,
    "laatste_st": None,
}

def send_telegram(msg):
    try:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            print("Telegram niet ingesteld")
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def get_free_balance(coin):
    for acc in ["UNIFIED", "SPOT", "FUND", "CONTRACT"]:
        try:
            resp = session.get_wallet_balance(accountType=acc, coin=coin)
            coins = resp["result"]["list"][0]["coin"]
            for c in coins:
                if c["coin"] == coin:
                    val = float(c.get("walletBalance") or 0)
                    if val > 0:
                        return val
        except Exception as e:
            print(f"Balance error {acc}: {e}")
    return 0.0

def get_prijs():
    resp = session.get_tickers(category="spot", symbol=SYMBOOL)
    return float(resp["result"]["list"][0]["lastPrice"])

def get_klines():
    resp = session.get_kline(category="spot", symbol=SYMBOOL, interval=INTERVAL, limit=100)
    # Bybit geeft nieuwste eerst, wij willen oudste eerst
    return list(reversed(resp["result"]["list"]))

def calc_atr(highs, lows, closes, period):
    tr_list = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        tr_list.append(tr)
    # Simpele ATR: gemiddelde van laatste 'period' TR's
    return sum(tr_list[-period:]) / period, tr_list

# --- 1. SUPERTREND 10, 3 - GECORRIGEERD ---
def supertrend_richting(klines):
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]
    atr, _ = calc_atr(highs, lows, closes, ST_ATR)

    # We berekenen alleen laatste candle, met juiste logica
    i = len(klines) - 1
    hl2 = (highs[i] + lows[i]) / 2
    upper = hl2 + ST_MULT * atr
    lower = hl2 - ST_MULT * atr

    # CORRECT: prijs boven upper = UP, onder lower = DOWN
    if closes[i] > upper:
        return "up", upper, lower
    if closes[i] < lower:
        return "down", upper, lower
    # Tussenin: hou vorige richting
    return bot_state["laatste_st"] or "down", upper, lower

# --- 2. UT BOT ALERTS 4, 14 ---
def utbot_richting(klines):
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    closes = [float(k[4]) for k in klines]
    atr, _ = calc_atr(highs, lows, closes, ATR_PERIOD_UT)

    # UT Bot trailing stop logica
    trailing = None
    richting = "down"
    for i in range(1, len(klines)):
        src = closes[i]
        if src > (trailing if trailing is not None else src - UT_MULT * atr):
            # Boven trailing -> up
            new_trailing = max(trailing if trailing is not None else src - UT_MULT * atr, src - UT_MULT * atr)
            if richting == "down":
                richting = "up"
            trailing = new_trailing
        else:
            new_trailing = min(trailing if trailing is not None else src + UT_MULT * atr, src + UT_MULT * atr)
            if richting == "up":
                richting = "down"
            trailing = new_trailing

    return richting

# --- 3. TRENDLINE ---
def trendline_richting(klines):
    closes = [float(k[4]) for k in klines]
    # Simpele trendline: linear regression over laatste 30 candles
    data = closes[-30:]
    n = len(data)
    x_mean = sum(range(n)) / n
    y_mean = sum(data) / n
    num = sum((i - x_mean) * (data[i] - y_mean) for i in range(n))
    den = sum((i - x_mean) ** 2 for i in range(n))
    slope = num / den if den != 0 else 0
    # Trendline waarde op laatste candle
    trend_value = y_mean + slope * (n - 1 - x_mean)
    prijs = closes[-1]
    # Prijs boven trendline + stijgende lijn = up
    if prijs > trend_value and slope > 0:
        return "up"
    if prijs < trend_value and slope < 0:
        return "down"
    return "up" if prijs > trend_value else "down"

def do_buy():
    try:
        usdt_free = get_free_balance("USDT")
        print(f"BUY signaal - USDT free: {usdt_free}")
        if usdt_free < 5:
            msg = f"BUY genegeerd: te weinig USDT ({usdt_free})"
            send_telegram(msg)
            return msg

        prijs = get_prijs()
        bedrag = usdt_free * 0.99
        qty = math.floor((bedrag / prijs) / 1000) * 1000
        if qty <= 0:
            msg = "BUY genegeerd: Qty te klein"
            send_telegram(msg)
            return msg

        resp = session.place_order(
            category="spot",
            symbol=SYMBOOL,
            side="Buy",
            orderType="Market",
            qty=str(qty)
        )
        print(f"BUY order: {resp}")
        bot_state["in_positie"] = True
        bot_state["entry_prijs"] = prijs
        send_telegram(f"✅ BUY {SYMBOOL} | qty {qty} | prijs {prijs}")
        return "BUY uitgevoerd"
    except Exception as e:
        print(f"BUY error: {e}")
        send_telegram(f"❌ BUY error: {e}")
        return f"BUY error: {e}"

def do_sell(reden="signaal"):
    try:
        pepe_free = get_free_balance("PEPE")
        print(f"SELL signaal ({reden}) - PEPE free: {pepe_free}")
        if pepe_free <= 0:
            bot_state["in_positie"] = False
            return "SELL genegeerd: geen PEPE"

        qty = math.floor(pepe_free / 1000) * 1000
        if qty <= 0:
            return "SELL genegeerd: Qty te klein"

        prijs = get_prijs()
        resp = session.place_order(
            category="spot",
            symbol=SYMBOOL,
            side="Sell",
            orderType="Market",
            qty=str(qty)
        )
        print(f"SELL order: {resp}")
        pnl_pct = ((prijs - bot_state["entry_prijs"]) / bot_state["entry_prijs"] * 100) if bot_state["entry_prijs"] else 0
        bot_state["in_positie"] = False
        bot_state["entry_prijs"] = 0.0
        send_telegram(f"🔴 SELL {SYMBOOL} | qty {qty} | prijs {prijs} | PnL {pnl_pct:.2f}% | reden: {reden}")
        return "SELL uitgevoerd"
    except Exception as e:
        print(f"SELL error: {e}")
        send_telegram(f"❌ SELL error: {e}")
        return f"SELL error: {e}"

def check_signalen():
    try:
        klines = get_klines()
        if not klines:
            return

        candle_time = int(klines[-1][0])
        # Alleen 1x per nieuwe candle handelen
        if candle_time == bot_state["laatste_candle"]:
            return
        bot_state["laatste_candle"] = candle_time

        st, _, _ = supertrend_richting(klines)
        ut = utbot_richting(klines)
        trend = trendline_richting(klines)

        print(f"Candle {candle_time} | ST: {st} | UT: {ut} | Trendline: {trend} | In positie: {bot_state['in_positie']}")
        bot_state["laatste_st"] = st
        bot_state["laatste_ut"] = ut

        # KOOP: alles moet UP zijn
        if not bot_state["in_positie"] and st == "up" and ut == "up" and trend == "up":
            bot_state["signaal"] = "buy"
            do_buy()
        # VERKOOP: UT of Supertrend draait naar DOWN
        elif bot_state["in_positie"] and (ut == "down" or st == "down"):
            bot_state["signaal"] = "sell"
            do_sell(reden=f"UT={ut} ST={st}")
        else:
            bot_state["signaal"] = "geen"
    except Exception as e:
        print(f"check_signalen error: {e}")

def bot_loop():
    while True:
        if bot_state["actief"]:
            check_signalen()
        time.sleep(60)

# --- Flask routes ---
@app.route("/", methods=["GET"])
def home():
    return jsonify({"status": "live", "state": bot_state})

@app.route("/webhook", methods=["POST"])
def webhook():
    # Voor als je later TradingView alerts wil gebruiken
    data = request.json or {}
    print(f"Webhook: {data}")
    return jsonify({"ok": True})

@app.route("/status", methods=["GET"])
def status():
    return jsonify(bot_state)

# Start loop bij opstarten
threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
