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
    telegram("Bot gestart - USDC, zonder trend, rente-op-rente AAN")
    while True:
        try:
            usdc = get_balance("USDC")
            pepe = get_balance("PEPE")
            price = get_price(SYMBOL) if usdc >= 5 or pepe > 0 else 0.0
            pepe_waarde = pepe * price if price > 0 else 0.0
            in_positie = pepe_waarde >= 5
            print(f"Check: USDC {usdc:.2f} PEPE {pepe} waarde {pepe_waarde:.2f} prijs {price} in_positie {in_positie}", flush=True)
            if not in_positie and usdc >= 5 and price > 0:
                qty = usdc / price
                print(f"KOPEN: {usdc:.2f} USDC -> {int(qty)} PEPE", flush=True)
                res = place_order(SYMBOL, "Buy", qty)
                if res.get("retCode") == 0:
                    telegram(f"GEKOCHT: {usdc:.2f} USDC -> PEPE @ {price}")
            elif in_positie:
                print("Al PEPE in bezit, vasthouden", flush=True)
            else:
                print("Te weinig USDC, wachten", flush=True)
        except Exception as e:
            print(f"Loop fout: {e}", flush=True)
        time.sleep(60)

@app.route("/")
def home():
    return "Pepebot-3 live - USDC zonder trend"

@app.route("/test-key")
def test_key():
    return jsonify({"api_key_set": bool(API_KEY), "base": BASE, "usdc": get_balance("USDC"), "pepe": get_balance("PEPE"), "prijs_usdc": get_price("PEPEUSDC")})

threading.Thread(target=bot_loop, daemon=True).start()
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
