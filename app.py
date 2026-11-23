from flask import Flask, jsonify
import hmac, hashlib, time, requests, os, threading, math

app = Flask(__name__)

# --- ENV ---
BASE = "https://api.bybit.com"
API_KEY = os.environ.get("BYBIT_API_KEY", "")
API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "")
SYMBOL = "PEPEUSDT"
TRADE_USDT = float(os.environ.get("TRADE_USDT", "50"))
INTERVAL = "15"
ATR_PERIODE = 14
SLEUTEL_WAARDE = 1.0

bot_state = {"in_positie": False, "laatste_signaal": "Geen", "laatste_check": "Nog niet", "laatste_fout": ""}

# --- BYBIT V5 ---
def bybit_get(path, params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    query = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    to_sign = ts + API_KEY + recv + query
    sign = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sign,
        "X-BAPI-RECV-WINDOW": recv,
    }
    r = requests.get(f"{BASE}{path}?{query}", headers=headers, timeout=10)
    return r.json()

def bybit_post(path, body):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    import json
    payload = json.dumps(body)
    to_sign = ts + API_KEY + recv + payload
    sign = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sign,
        "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json"
    }
    r = requests.post(f"{BASE}{path}", headers=headers, data=payload, timeout=10)
    return r.json()

# --- TELEGRAM ---
def telegram(msg):
    if not TG_TOKEN or not TG_CHAT:
        print(f"Telegram niet ingesteld: {msg}")
        return
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
                      json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
    except Exception as e:
        print(f"Telegramfout: {e}")
    print(msg)

# --- DATA ---
def get_candles():
    r = requests.get(f"{BASE}/v5/market/kline",
                     params={"category": "spot", "symbol": SYMBOL, "interval": INTERVAL, "limit": 100},
                     timeout=10).json()
    candles = []
    for c in r.get("result", {}).get("list", []):
        # [start, open, high, low, close, volume, turnover]
        candles.append({"high": float(c[2]), "low": float(c[3]), "close": float(c[4])})
    return list(reversed(candles))

def atr(candles):
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    if len(trs) < ATR_PERIODE:
        return 0
    return sum(trs[-ATR_PERIODE:]) / ATR_PERIODE

def ut_signaal(candles):
    # Simpele UT Bot: close > vorige close + ATR*key = buy, anders sell
    if len(candles) < ATR_PERIODE + 2:
        return "Geen"
    a = atr(candles)
    if a == 0:
        return "Geen"
    if candles[-1]["close"] > candles[-2]["close"] + a * SLEUTEL_WAARDE * 0.1:
        return "BUY"
    if candles[-1]["close"] < candles[-2]["close"] - a * SLEUTEL_WAARDE * 0.1:
        return "SELL"
    return "Geen"

def get_balances():
    data = bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED", "coin": "USDT,PEPE"})
    usdt, pepe = 0, 0
    try:
        for coin in data["result"]["list"][0]["coin"]:
            if coin["coin"] == "USDT":
                usdt = float(coin.get("walletBalance", 0))
            if coin["coin"] == "PEPE":
                pepe = float(coin.get("walletBalance", 0))
    except Exception as e:
        bot_state["laatste_fout"] = str(e)
    return usdt, pepe

def do_buy():
    usdt, _ = get_balances()
    if usdt < 10:
        telegram(f"❌ KOOP afgewezen: te weinig USDT ({usdt})")
        return
    bedrag = min(TRADE_USDT, usdt * 0.98)
    # Prijs ophalen
    tick = requests.get(f"{BASE}/v5/market/tickers",
                        params={"category": "spot", "symbol": SYMBOL}, timeout=10).json()
    prijs = float(tick["result"]["list"][0]["lastPrice"])
    qty = int(math.floor((bedrag / prijs) / 1000) * 1000)
    if qty <= 0:
        telegram("❌ BUY mislukt: qty te klein")
        return
    resp = bybit_post("/v5/order/create", {
        "category": "spot", "symbol": SYMBOL, "side": "Buy",
        "orderType": "Market", "qty": str(qty)
    })
    print(f"BUY resp: {resp}")
    if resp.get("retCode") == 0:
        bot_state["in_positie"] = True
        telegram(f"✅ KOPEN uitgevoerd: {qty} PEPE voor ~{bedrag:.2f} USDT")
    else:
        telegram(f"❌ BUY fout: {resp}")

def do_sell():
    _, pepe = get_balances()
    if pepe <= 0:
        telegram("❌ VERKOOP afgewezen: geen PEPE")
        return
    qty = int(math.floor(pepe / 1000) * 1000)
    if qty <= 0:
        telegram("❌ SELL mislukt: qty te klein")
        return
    resp = bybit_post("/v5/order/create", {
        "category": "spot", "symbol": SYMBOL, "side": "Sell",
        "orderType": "Market", "qty": str(qty)
    })
    print(f"SELL resp: {resp}")
    if resp.get("retCode") == 0:
        bot_state["in_positie"] = False
        telegram(f"✅ VERKOPEN uitgevoerd: {qty} PEPE")
    else:
        telegram(f"❌ SELL fout: {resp}")

# --- LOOP ---
def bot_loop():
    time.sleep(5)
    telegram("🤖 Pepebot gestart op Render")
    laatste_candle = None
    while True:
        try:
            candles = get_candles()
            # Alleen traden op een nieuwe gesloten candle: gebruik 1-na-laatste als gesloten
            if len(candles) >= 2:
                # Simpele check: trade max 1x per 15 min via timestamp
                signaal = ut_signaal(candles[:-1])
                bot_state["laatste_signaal"] = signaal
                bot_state["laatste_check"] = time.strftime("%H:%M:%S")
                print(f"Check {bot_state['laatste_check']} signaal={signaal} in_positie={bot_state['in_positie']}")
                if signaal == "BUY" and not bot_state["in_positie"]:
                    do_buy()
                elif signaal == "SELL" and bot_state["in_positie"]:
                    do_sell()
        except Exception as e:
            bot_state["laatste_fout"] = str(e)
            print(f"Loop fout: {e}")
        time.sleep(60)

# Start ook onder Gunicorn
threading.Thread(target=bot_loop, daemon=True).start()

# --- ROUTES ---
@app.route("/")
def home():
    return "Pepebot draait"

@app.route("/status")
def status():
    usdt, pepe = get_balances()
    return jsonify({**bot_state, "usdt": usdt, "pepe": pepe, "trade_usdt": TRADE_USDT})

@app.route("/test-key")
def test_key():
    data = bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
    return jsonify({"bybit": data})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
