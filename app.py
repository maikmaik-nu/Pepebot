import os, time, hmac, hashlib, requests, threading, json
from flask import Flask, jsonify

app = Flask(__name__)

BASE = "https://api.bybit.eu"
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
CATEGORY = "spot"
SYMBOL = "PEPEUSDC"
MUNT = "USDC"

def telegram(msg):
    try:
        if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
            print(f"Telegram uit: {msg}", flush=True)
            return
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT, "text": msg}, timeout=10)
    except Exception as e:
        print(f"Telegram fout: {e}", flush=True)

def sign_get(params):
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    query = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    payload = timestamp + API_KEY + recv_window + query
    sig = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": timestamp, "X-BAPI-RECV-WINDOW": recv_window, "X-BAPI-SIGN": sig, "Content-Type": "application/json"}

def sign_post(body):
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    body_str = json.dumps(body, separators=(",", ":"))
    payload = timestamp + API_KEY + recv_window + body_str
    sig = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": timestamp, "X-BAPI-RECV-WINDOW": recv_window, "X-BAPI-SIGN": sig, "Content-Type": "application/json"}, body_str

def get_balance(coin):
    try:
        params = {"accountType": "SPOT", "coin": coin}
        r = requests.get(f"{BASE}/v5/account/wallet-balance", params=params, headers=sign_get(params), timeout=10)
        data = r.json()
        if data.get("retCode") != 0:
            print(f"Balance fail {coin}: {data}", flush=True)
            return 0.0
        lst = data.get("result", {}).get("list", [])
        if not lst:
            return 0.0
        for c in lst[0].get("coin", []):
            if c.get("coin") == coin:
                return float(c.get("walletBalance", 0) or 0)
        return 0.0
    except Exception as e:
        print(f"Balance fail {coin}: {e}", flush=True)
        return 0.0

def get_price(symbol):
    try:
        r = requests.get(f"{BASE}/v5/market/tickers", params={"category": CATEGORY, "symbol": symbol}, timeout=10)
        data = r.json()
        lst = data.get("result", {}).get("list", [])
        if not lst:
            return 0.0
        return float(lst[0].get("lastPrice", 0) or 0)
    except Exception as e:
        print(f"Prijs fout: {e}", flush=True)
        return 0.0

def get_signal(symbol):
    try:
        r = requests.get(f"{BASE}/v5/market/kline", params={"category": CATEGORY, "symbol": symbol, "interval": "15", "limit": "3"}, timeout=10)
        data = r.json()
        lst = data.get("result", {}).get("list", [])
        if len(lst) < 3:
            return "GEEN"
        c1 = lst[1]
        c2 = lst[2]
        o1, cl1 = float(c1[1]), float(c1[4])
        o2, cl2 = float(c2[1]), float(c2[4])
        if cl1 > o1 and cl2 > o2:
            return "BUY"
        if cl1 < o1 and cl2 < o2:
            return "SELL"
        return "GEEN"
    except Exception as e:
        print(f"Signaal fout: {e}", flush=True)
        return "GEEN"

def place_order(symbol, side, qty):
    try:
        body = {"category": CATEGORY, "symbol": symbol, "side": side, "orderType": "Market", "qty": str(int(qty)), "timeInForce": "GTC"}
        headers, body_str = sign_post(body)
        r = requests.post(f"{BASE}/v5/order/create", headers=headers, data=body_str, timeout=10)
        data = r.json()
        print(f"{side} {symbol} qty {int(qty)} -> {data}", flush=True)
        return data
    except Exception as e:
        print(f"Order fout: {e}", flush=True)
        return {}

def bot_loop():
    telegram("Bot gestart - Rente-op-rente AAN - USDC only")
    while True:
        try:
            geld = get_balance(MUNT)
            pepe = get_balance("PEPE")
            price = get_price(SYMBOL) if geld >= 5 or pepe > 0 else 0.0
            pepe_waarde = pepe * price if price > 0 else 0.0
            in_positie = pepe_waarde >= 5
            signaal = get_signal(SYMBOL) if price > 0 else "GEEN"
            print(f"Check: {MUNT} {geld:.2f} PEPE {pepe} waarde {pepe_waarde:.2f} prijs {price} signaal {signaal} in_positie {in_positie}", flush=True)
            if signaal == "BUY" and not in_positie and geld >= 5:
                qty = geld / price
                print(f"KOPEN rente-op-rente: {geld:.2f} {MUNT} -> {int(qty)} PEPE", flush=True)
                res = place_order(SYMBOL, "Buy", qty)
                if res.get("retCode") == 0:
                    telegram(f"GEKOCHT: {geld:.2f} {MUNT} -> PEPE @ {price}")
            elif signaal == "SELL" and in_positie:
                print(f"VERKOPEN: {int(pepe)} PEPE", flush=True)
                res = place_order(SYMBOL, "Sell", pepe)
                if res.get("retCode") == 0:
                    telegram(f"VERKOCHT: {int(pepe)} PEPE @ {price}")
            else:
                print(f"Geen signaal. In positie: {in_positie}", flush=True)
        except Exception as e:
            print(f"Loop fout: {e}", flush=True)
        time.sleep(60)

@app.route("/")
def home():
    return "Pepebot-3 live - USDC"

@app.route("/test-key")
def test_key():
    return jsonify({"api_key_set": bool(API_KEY), "base": BASE, "usdc": get_balance("USDC"), "pepe": get_balance("PEPE"), "prijs_usdc": get_price("PEPEUSDC"), "signaal_usdc": get_signal("PEPEUSDC")})

threading.Thread(target=bot_loop, daemon=True).start()
if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
