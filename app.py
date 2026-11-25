import os
import time
import hmac
import hashlib
import json
import threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# ---------- CONFIG ----------
BASE_URL = os.getenv("BASE_URL", "https://api.bybit.eu")
API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# UT Bot settings - klassiek: ATR 10, factor 3, op 15 min
UT_ATR_PERIOD = 10
UT_FACTOR = 3.0
INTERVAL = "15"
CHECK_SECONDS = 60

running = False

# ---------- HELPERS ----------
def log(msg):
    print(msg, flush=True)

def send_tg(msg):
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": msg},
            timeout=10,
        )
    except Exception as e:
        log(f"Telegram fout: {e}")

def make_sign(timestamp, recv_window, payload_str):
    msg = timestamp + API_KEY + recv_window + payload_str
    return hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()

def get_balance():
    """Geeft (usdt, usdc, pepe) apart terug."""
    try:
        if not API_KEY or not API_SECRET:
            log("Balance fout: geen API key in Render")
            return 0.0, 0.0, 0.0

        ts = str(int(time.time() * 1000))
        recv = "5000"
        query = "accountType=SPOT"
        sign = make_sign(ts, recv, query)

        headers = {
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
            "X-BAPI-SIGN": sign,
        }

        r = requests.get(
            f"{BASE_URL}/v5/account/wallet-balance",
            headers=headers,
            params={"accountType": "SPOT"},
            timeout=10,
        )
        d = r.json()

        if d.get("retCode") != 0:
            log(f"Balance fail: {d}")
            return 0.0, 0.0, 0.0

        coin_list = d.get("result", {}).get("list", [])
        if not coin_list:
            return 0.0, 0.0, 0.0

        coins = coin_list[0].get("coin", [])
        usdt = usdc = pepe = 0.0
        for c in coins:
            if c.get("coin") == "USDT":
                usdt = float(c.get("walletBalance") or 0)
            elif c.get("coin") == "USDC":
                usdc = float(c.get("walletBalance") or 0)
            elif c.get("coin") == "PEPE":
                pepe = float(c.get("walletBalance") or 0)

        return usdt, usdc, pepe

    except Exception as e:
        log(f"Balance fout: {e}")
        return 0.0, 0.0, 0.0

def get_klines(symbol):
    """Haalt high, low, close op voor UT Bot."""
    try:
        r = requests.get(
            f"{BASE_URL}/v5/market/kline",
            params={"category": "spot", "symbol": symbol, "interval": INTERVAL, "limit": 200},
            timeout=10,
        )
        d = r.json()
        if d.get("retCode") != 0:
            log(f"Klines fail {symbol}: {d}")
            return None

        raw = d.get("result", {}).get("list", [])
        if len(raw) < 50:
            return None

        # 【entity-Bybit¦canonical_name=Bybit】 geeft nieuw -> oud, wij draaien om naar oud -> nieuw
        # Elke kline: [start, open, high, low, close, volume, turnover]
        klines = []
        for x in reversed(raw):
            klines.append({
                "high": float(x[2]),
                "low": float(x[3]),
                "close": float(x[4]),
            })
        return klines[:-1]  # laatste open candle eruit
    except Exception as e:
        log(f"Klines fout: {e}")
        return None

def utbot_signal(klines):
    """
    Echte UT Bot logica:
    - ATR over laatste 10 candles
    - Trailing stop = close +/- factor * ATR
    - BUY als prijs boven trailing stop kruist
    - SELL als prijs onder trailing stop kruist
    Geeft terug: "BUY", "SELL" of "HOLD" + huidige trailing stop + prijs
    """
    try:
        if len(klines) < UT_ATR_PERIOD + 5:
            return "HOLD", 0.0, 0.0

        # 1. ATR berekenen
        trs = []
        for i in range(1, len(klines)):
            high = klines[i]["high"]
            low = klines[i]["low"]
            prev_close = klines[i - 1]["close"]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)

        # ATR = gemiddelde van laatste UT_ATR_PERIOD TR's
        atr = sum(trs[-UT_ATR_PERIOD:]) / UT_ATR_PERIOD
        n_loss = UT_FACTOR * atr

        # 2. Trailing stop opbouwen
        trailing = 0.0
        pos = 0  # 0 = geen positie / neutraal, 1 = long, -1 = short
        signal = "HOLD"

        for i in range(1, len(klines)):
            close = klines[i]["close"]
            prev_close = klines[i - 1]["close"]

            if i == 1:
                trailing = close - n_loss if close > prev_close else close + n_loss
                continue

            # Trailing stop bijwerken
            if close > trailing and prev_close > trailing:
                trailing = max(trailing, close - n_loss)
            elif close < trailing and prev_close < trailing:
                trailing = min(trailing, close + n_loss)
            elif close > trailing:
                trailing = close - n_loss
            else:
                trailing = close + n_loss

            # Kruising checken - alleen op de laatste 2 candles telt het signaal
            if i >= len(klines) - 2:
                if prev_close <= trailing and close > trailing:
                    signal = "BUY"
                elif prev_close >= trailing and close < trailing:
                    signal = "SELL"

        price = klines[-1]["close"]
        return signal, trailing, price

    except Exception as e:
        log(f"UT Bot fout: {e}")
        return "HOLD", 0.0, 0.0

def place_order(symbol, side, qty):
    try:
        ts = str(int(time.time() * 1000))
        recv = "5000"
        body = {
            "category": "spot",
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": str(int(qty)),
        }
        body_str = json.dumps(body)
        sign = make_sign(ts, recv, body_str)
        headers = {
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv,
            "X-BAPI-SIGN": sign,
            "Content-Type": "application/json",
        }
        r = requests.post(
            f"{BASE_URL}/v5/order/create",
            headers=headers,
            data=body_str,
            timeout=10,
        )
        log(f"ORDER {side} {symbol} {qty}: {r.text[:400]}")
        return r.json() if r.text.startswith("{") else None
    except Exception as e:
        log(f"Order fout: {e}")
        return None

# ---------- BOT ----------
def bot_loop():
    global running
    if running:
        return
    running = True
    log("Bot loop gestart - UT Bot + Rente-op-rente")
    send_tg("Pepebot-3 gestart - UT Bot + Rente-op-rente AAN")

    while True:
        try:
            usdt, usdc, pepe = get_balance()

            # Kies symbool op basis van waar je geld ECHT staat
            # Jij hebt nu USDC, dus hij pakt PEPEUSDC
            if usdt >= 1:
                symbol, money = "PEPEUSDT", usdt
            elif usdc >= 1:
                symbol, money = "PEPEUSDC", usdc
            else:
                symbol, money = "PEPEUSDT", 0.0

            klines = get_klines(symbol)
            if klines is None:
                log("Geen klines, over 60 sec opnieuw")
                time.sleep(CHECK_SECONDS)
                continue

            signal, trailing, price = utbot_signal(klines)
            in_positie = pepe > 0

            log(f"Check: USDT {usdt:.2f} USDC {usdc:.2f} PEPE {pepe:.0f} SYMBOL {symbol} UT={signal} trailing {trailing:.8f} prijs {price} in_positie {in_positie}")

            # KOPEN - RENTE-OP-RENTE: hele saldo, niet max 10
            if signal == "BUY" and not in_positie and money >= 1:
                # Hou 0.50 over voor fees / afronding, rest alles inzetten
                buy_usd = max(0.0, money - 0.50)
                qty = int(buy_usd / price) if price > 0 else 0
                if qty > 0:
                    log(f"UT BUY: {buy_usd:.2f} -> {qty} PEPE via {symbol}")
                    res = place_order(symbol, "Buy", qty)
                    if res and res.get("retCode") == 0:
                        send_tg(f"UT BUY: {qty} PEPE voor ~{buy_usd:.2f} @ {price} - Rente-op-rente")
                    else:
                        log(f"BUY afgewezen: {res}")

            # VERKOPEN - alles eruit bij UT SELL
            elif signal == "SELL" and in_positie:
                log(f"UT SELL: {int(pepe)} PEPE via {symbol}")
                res = place_order(symbol, "Sell", int(pepe))
                if res and res.get("retCode") == 0:
                    send_tg(f"UT SELL: {int(pepe)} PEPE @ {price} - Rente-op-rente")
                else:
                    log(f"SELL afgewezen: {res}")
            else:
                log(f"Wachten op UT signaal. In positie: {in_positie}")

        except Exception as e:
            log(f"Loop fout, gaat door: {e}")

        time.sleep(CHECK_SECONDS)

def keep_alive():
    """Gratis truc: ping jezelf elke 10 min zodat Render minder snel slaapt."""
    while True:
        try:
            url = os.getenv("RENDER_EXTERNAL_URL", "")
            if url:
                requests.get(f"{url}/ping", timeout=10)
        except Exception:
            pass
        time.sleep(600)

# ---------- WEB ----------
@app.route("/")
def home():
    return "Pepebot-3 live - UT Bot + Rente-op-rente"

@app.route("/ping")
def ping():
    return "pong"

@app.route("/test-key")
def test_key():
    usdt, usdc, pepe = get_balance()
    # Kies symbool voor test
    symbol = "PEPEUSDT" if usdt >= 1 else "PEPEUSDC"
    klines = get_klines(symbol)
    signal, trailing, price = utbot_signal(klines) if klines else ("HOLD", 0.0, 0.0)
    return jsonify({
        "ok": True,
        "USDT": usdt,
        "USDC": usdc,
        "PEPE": pepe,
        "symbol": symbol,
        "ut_signal": signal,
        "trailing": trailing,
        "prijs": price,
        "base": BASE_URL,
    })

threading.Thread(target=bot_loop, daemon=True).start()
threading.Thread(target=keep_alive, daemon=True).start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
