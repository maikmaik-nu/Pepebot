import os, time, hmac, hashlib, json, threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDC"
COIN_BUY = "USDC"
COIN_SELL = "PEPE"
VASTE_INLEG = 100.0
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10
        )
    except Exception as e:
        print("Telegram fail:", e)

def _sign(timestamp, recv_window, body_str):
    to_sign = timestamp + BYBIT_API_KEY + recv_window + body_str
    return hmac.new(BYBIT_API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()

def bybit_get(path, params):
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    query = "&".join([f"{k}={v}" for k, v in params.items()])
    sign = _sign(timestamp, recv_window, query)
    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-SIGN": sign,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
    }
    r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
    return r.json()

def bybit_post(path, payload):
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    body_str = json.dumps(payload)
    sign = _sign(timestamp, recv_window, body_str)
    headers = {
        "X-BAPI-API-KEY": BYBIT_API_KEY,
        "X-BAPI-SIGN": sign,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "Content-Type": "application/json"
    }
    r = requests.post(BASE_URL + path, json=payload, headers=headers, timeout=10)
    return r.json()

def get_balance():
    try:
        data = bybit_get("/v5/account/wallet-balance", {"accountType": "SPOT"})
        print("BALANCE RAW:", data)
        if data.get("retCode") != 0:
            print(f"Balance fail: retCode={data.get('retCode')} retMsg={data.get('retMsg')}")
            return 0.0, 0.0, data
        coins = data["result"]["list"][0]["coin"]
        usdc = 0.0
        pepe = 0.0
        for c in coins:
            if c["coin"] == "USDC":
                usdc = float(c.get("walletBalance") or 0)
            if c["coin"] == "PEPE":
                pepe = float(c.get("walletBalance") or 0)
        return usdc, pepe, data
    except Exception as e:
        print("Balance fail exception:", e)
        return 0.0, 0.0, {"retCode": -1, "retMsg": str(e)}

def get_price():
    try:
        data = bybit_get("/v5/market/tickers", {"category": "spot", "symbol": SYMBOL})
        price = float(data["result"]["list"][0]["lastPrice"])
        return price, data
    except Exception as e:
        print("Prijs fail:", e)
        return 0.0, {}

def do_check():
    usdc, pepe, bal_data = get_balance()
    prijs, _ = get_price()
    print(f"CHECK: USDC={usdc} PEPE={pepe} price={prijs} retCode={bal_data.get('retCode')}")
    if bal_data.get("retCode") != 0:
        print("Geen saldo, probeer over 60 sec opnieuw - geen stop")
        return {"usdc": usdc, "pepe": pepe, "prijs": prijs, "actie": "geen saldo - 10003 check key", "bal": bal_data}
    if prijs <= 0:
        return {"usdc": usdc, "pepe": pepe, "prijs": prijs, "actie": "geen prijs"}
    if usdc < VASTE_INLEG:
        print(f"Te weinig USDC om te kopen: {usdc}")
        return {"usdc": usdc, "pepe": pepe, "prijs": prijs, "actie": f"te weinig USDC: {usdc}"}
    # Hier staat nu alleen checken, geen auto-kooptje, zodat je eerst ziet dat saldo werkt
    print(f"Vaste inleg: {VASTE_INLEG} - Geen signaal - alleen check")
    return {"usdc": usdc, "pepe": pepe, "prijs": prijs, "actie": "check ok - klaar om te kopen"}

def loop():
    while True:
        try:
            do_check()
        except Exception as e:
            print("Loop fail:", e)
        time.sleep(60)

threading.Thread(target=loop, daemon=True).start()

@app.route("/")
def home():
    return "Pepebot-3 live"

@app.route("/test-key")
def test_key():
    usdc, pepe, bal_data = get_balance()
    prijs, _ = get_price()
    return jsonify({
        "api_key_set": bool(BYBIT_API_KEY),
        "base": BASE_URL,
        "retCode": bal_data.get("retCode"),
        "retMsg": bal_data.get("retMsg"),
        "full": bal_data,
        "pepe": pepe,
        "prijs": prijs,
        "usdc": usdc,
        "usdt": 0.0
    })

@app.route("/check")
def check():
    result = do_check()
    return jsonify({"ok": True, **result})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
