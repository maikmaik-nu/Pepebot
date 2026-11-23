import os
import time
import hmac
import hashlib
import threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# --- INSTELLINGEN ---
BASE_URL = "https://api.bybit.eu"
CATEGORY = "spot"
SYMBOL = "PEPEUSDC"
BASE = "USDC"
QUOTE = "PEPE"

API_KEY = os.environ.get("BYBIT_API_KEY", "")
API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

TRADE_AMOUNT_USDC = float(os.environ.get("TRADE_AMOUNT_USDC", "10"))
CHECK_EVERY_SEC = 60

state = {"usdc": 0.0, "pepe": 0.0, "prijs": 0.0, "in_position": False, "last": "gestart"}

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception:
        pass

def bybit_get(path, params):
    ts = str(int(time.time() * 1000))
    query = "&".join(f"{k}={v}" for k, v in params.items())
    sign_str = ts + API_KEY + "5000" + query
    sign = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sign,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000",
    }
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
    j = r.json()
    return j.get("retCode"), j.get("result") or {}

def bybit_post(path, params):
    ts = str(int(time.time() * 1000))
    body = "&".join(f"{k}={v}" for k, v in params.items())
    sign_str = ts + API_KEY + "5000" + body
    sign = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sign,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    r = requests.post(BASE_URL + path, data=params, headers=headers, timeout=10)
    return r.json()

def get_balance():
    usdc = 0.0
    pepe = 0.0
    for acct in ["UNIFIED", "SPOT", "FUNDING"]:
        try:
            _, data = bybit_get("/v5/account/wallet-balance", {"accountType": acct, "coin": "USDC,PEPE"})
            for acc in (data.get("list") or []):
                for c in (acc.get("coin") or []):
                    if c.get("coin") == "USDC":
                        usdc += float(c.get("walletBalance") or 0)
                    if c.get("coin") == "PEPE":
                        pepe += float(c.get("walletBalance") or 0)
        except Exception:
            pass
    return usdc, pepe

def get_prijs():
    try:
        r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
        lst = (r.get("result") or {}).get("list") or []
        if lst:
            return float(lst[0].get("lastPrice") or 0)
    except Exception:
        pass
    return 0.0

def plaats_order(side, qty):
    return bybit_post("/v5/order/create", {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": side,
        "orderType": "Market",
        "qty": str(qty),
    })

def bot_loop():
    send_telegram(f"Bot gestart: {SYMBOL} - SPOT - base {BASE}")
    while True:
        try:
            usdc, pepe = get_balance()
            prijs = get_prijs()
            state["usdc"] = usdc
            state["pepe"] = pepe
            state["prijs"] = prijs
            state["in_position"] = pepe > 0 and prijs > 0

            # Simpele regel: heb je geen PEPE en heb je USDC? Koop voor TRADE_AMOUNT_USDC
            # Heb je wel PEPE? Dan nu niks doen (verkopen doe je later handmatig / met eigen regel)
            if not state["in_position"] and usdc >= TRADE_AMOUNT_USDC and prijs > 0:
                qty = int(TRADE_AMOUNT_USDC / prijs)
                if qty > 0:
                    res = plaats_order("Buy", qty)
                    state["last"] = f"BUY {qty} -> {res}"
                    send_telegram(f"BUY {qty} PEPE voor {TRADE_AMOUNT_USDC} USDC\n{res}")
                else:
                    state["last"] = "te weinig voor 1 order"
            else:
                state["last"] = "check ok, niks gedaan"
        except Exception as e:
            state["last"] = f"fout: {e}"
        time.sleep(CHECK_EVERY_SEC)

@app.route("/")
def home():
    return jsonify({"status": "live", "symbol": SYMBOL, **state})

@app.route("/test-key")
def test_key():
    usdc, pepe = get_balance()
    prijs = get_prijs()
    return jsonify({
        "api_key_set": bool(API_KEY),
        "base": BASE,
        "pepe": pepe,
        "prijs": prijs,
        "usdc": usdc,
    })

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
