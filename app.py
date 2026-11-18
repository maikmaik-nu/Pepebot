import os, time, threading, json
from decimal import Decimal
from flask import Flask, jsonify
from pybit.unified_trading import HTTP

app = Flask(__name__)

SYMBOL = "PEPEUSDT"
INTERVAL = "15"
UTBOT_FACTOR = 4.0
ATR_PERIOD = 14

API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

def get_client():
    return HTTP(
        testnet=False,
        api_key=API_KEY,
        api_secret=API_SECRET,
        domain="bybit.eu",
        recv_window=20000
    )

def send_tg(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    try:
        import urllib.request
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = json.dumps({"chat_id": TELEGRAM_CHAT, "text": msg}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print("TG ERR", e, flush=True)

def get_balance():
    client = get_client()
    for acct in ["SPOT", "UNIFIED"]:
        try:
            r = client.get_wallet_balance(accountType=acct, coin="USDT")
            print(f"BALANCE TRY {acct} -> {r}", flush=True)
            if r.get("retCode") == 0:
                for w in r.get("result", {}).get("list", []):
                    for c in w.get("coin", []):
                        if c.get("coin") == "USDT":
                            return Decimal(str(c.get("walletBalance", "0")))
                return Decimal("0")
        except Exception as e:
            print(f"BALANCE EXC {acct} {e}", flush=True)
    return None

def get_klines():
    client = get_client()
    r = client.get_kline(category="spot", symbol=SYMBOL, interval=INTERVAL, limit=200)
    if r.get("retCode") != 0:
        raise Exception(f"Kline {r}")
    return r["result"]["list"][::-1]

def calc_utbot(klines):
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    trs = []
    for i in range(1, len(klines)):
        trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
    atr = sum(trs[:ATR_PERIOD]) / ATR_PERIOD
    for i in range(ATR_PERIOD, len(trs)):
        atr = (atr * (ATR_PERIOD-1) + trs[i]) / ATR_PERIOD
    last_close = closes[-1]
    prev_close = closes[-2]
    trailing_prev = prev_close - UTBOT_FACTOR * atr
    is_buy = last_close > trailing_prev
    is_sell = last_close < trailing_prev
    return is_buy, is_sell, atr

state = {"running": False, "last": "gestart", "balance": "?"}

def bot_loop():
    time.sleep(5)
    send_tg("🤖 Pepebot-3 gestart - pybit / bybit.eu")
    in_position = False
    while True:
        try:
            bal = get_balance()
            if bal is None:
                state["last"] = "Kan saldo niet lezen"
                send_tg("⚠️ Kan saldo niet lezen")
                time.sleep(60)
                continue
            state["balance"] = str(bal)
            klines = get_klines()
            is_buy, is_sell, atr = calc_utbot(klines)
            client = get_client()
            r = client.get_wallet_balance(accountType="SPOT", coin="PEPE")
            pepe_bal = Decimal("0")
            if r.get("retCode") == 0:
                for w in r["result"]["list"]:
                    for c in w.get("coin", []):
                        if c.get("coin") == "PEPE":
                            pepe_bal = Decimal(str(c.get("walletBalance", "0")))
            state["last"] = f"bal={bal} PEPE={pepe_bal} buy={is_buy} sell={is_sell}"
            print(state["last"], flush=True)
            if is_buy and not in_position and pepe_bal == 0 and bal > 5:
                price = float(klines[-1][4])
                qty = int(float(bal / Decimal(str(price))))
                if qty > 0:
                    o = client.place_order(category="spot", symbol=SYMBOL, side="Buy", orderType="Market", qty=str(qty))
                    print("BUY", o, flush=True)
                    send_tg(f"🟢 BUY PEPE {qty} @ {price} bal {bal}")
                    in_position = True
            elif is_sell and (in_position or pepe_bal > 0):
                qty = int(pepe_bal)
                if qty > 0:
                    o = client.place_order(category="spot", symbol=SYMBOL, side="Sell", orderType="Market", qty=str(qty))
                    print("SELL", o, flush=True)
                    send_tg(f"🔴 SELL PEPE {qty}")
                    in_position = False
            time.sleep(60)
        except Exception as e:
            print("LOOP ERR", e, flush=True)
            state["last"] = f"ERR {e}"
            time.sleep(60)

@app.route("/")
def home():
    return jsonify({"status": "Pepebot-3 live", "state": state, "key_start": API_KEY[:4] if API_KEY else "LEEG"})

@app.route("/test-key")
def test_key():
    try:
        client = get_client()
        r = client.get_wallet_balance(accountType="SPOT", coin="USDT")
        return jsonify({"key_start": API_KEY[:4] if API_KEY else "LEEG", "key_len": len(API_KEY), "secret_len": len(API_SECRET), "bybit_raw": r})
    except Exception as e:
        return jsonify({"key_start": API_KEY[:4] if API_KEY else "LEEG", "error": str(e)})

# FIX 2: start direct bij import, niet alleen in __main__ (gunicorn)
if not state["running"]:
    state["running"] = True
    threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
