import os
import time
import hmac
import hashlib
import requests
import threading
from flask import Flask, jsonify, request

app = Flask(__name__)

BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"

【entity-BYBIT_API_KEY¦canonical_name=Bybit】 = os.getenv("【entity-BYBIT_API_KEY¦canonical_name=Bybit】", "").strip()
【entity-BYBIT_API_SECRET¦canonical_name=Bybit】 = os.getenv("【entity-BYBIT_API_SECRET¦canonical_name=Bybit】", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

ENTRY_PRICE = 0.0

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=10,
        )
    except Exception as e:
        log(f"Telegram fout: {e}")

def sign_get(timestamp, recv_window, query):
    s = timestamp + 【entity-BYBIT_API_KEY¦canonical_name=Bybit】 + recv_window + query
    return hmac.new(【entity-BYBIT_API_SECRET¦canonical_name=Bybit】.encode(), s.encode(), hashlib.sha256).hexdigest()

def sign_post(timestamp, recv_window, body_str):
    s = timestamp + 【entity-BYBIT_API_KEY¦canonical_name=Bybit】 + recv_window + body_str
    return hmac.new(【entity-BYBIT_API_SECRET¦canonical_name=Bybit】.encode(), s.encode(), hashlib.sha256).hexdigest()

def bybit_get(path, params=None):
    if params is None:
        params = {}
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    query = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    headers = {
        "X-BAPI-API-KEY": 【entity-BYBIT_API_KEY¦canonical_name=Bybit】,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": sign_get(timestamp, recv_window, query),
        "Content-Type": "application/json",
    }
    try:
        r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
        return r.status_code, r.json()
    except Exception as e:
        return -1, {"retCode": -1, "retMsg": str(e)}

def bybit_post(path, body):
    import json
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    body_str = json.dumps(body)
    headers = {
        "X-BAPI-API-KEY": 【entity-BYBIT_API_KEY¦canonical_name=Bybit】,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": sign_post(timestamp, recv_window, body_str),
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(BASE_URL + path, data=body_str, headers=headers, timeout=10)
        return r.status_code, r.json()
    except Exception as e:
        return -1, {"retCode": -1, "retMsg": str(e)}

def parse_balance(data):
    usdt = 0.0
    pepe = 0.0
    try:
        lst = data.get("result", {}).get("list", [])
        if lst:
            for c in lst[0].get("coin", []):
                if c.get("coin") == "USDT":
                    usdt = float(c.get("walletBalance", 0) or 0)
                if c.get("coin") == "PEPE":
                    pepe = float(c.get("walletBalance", 0) or 0)
    except Exception:
        pass
    return usdt, pepe

def get_balance():
    # 1. UNIFIED eerst - daar staat je geld op 【entity-Bybit EU¦canonical_name=Bybit】
    _, data = bybit_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
    if data.get("retCode") == 0:
        usdt, pepe = parse_balance(data)
        return usdt, pepe, 0, "OK-UNIFIED"
    # 2. SPOT als fallback
    _, data2 = bybit_get("/v5/account/wallet-balance", {"accountType": "SPOT"})
    if data2.get("retCode") == 0:
        usdt, pepe = parse_balance(data2)
        return usdt, pepe, 0, "OK-SPOT"
    return 0.0, 0.0, data2.get("retCode", -1), data2.get("retMsg", "")

def get_price():
    try:
        r = requests.get(BASE_URL + "/v5/market/tickers", params={"category": "spot", "symbol": SYMBOL}, timeout=10)
        lst = r.json().get("result", {}).get("list", [])
        if lst:
            return float(lst[0].get("lastPrice", 0) or 0)
    except Exception:
        pass
    return 0.0

def place_order(side, qty):
    # qty afronden voor PEPE - 【entity-Bybit¦canonical_name=Bybit】 wil hele getallen
    body = {
        "category": "spot",
        "symbol": SYMBOL,
        "side": side,
        "orderType": "Market",
        "qty": str(int(qty)),
    }
    status, data = bybit_post("/v5/order/create", body)
    log(f"ORDER {side} qty={qty} -> {data}")
    return data

def bot_loop():
    global ENTRY_PRICE
    log("Bot loop gestart")
    send_telegram("Pepebot-3 gestart ✅")
    while True:
        try:
            usdt, pepe, retcode, msg = get_balance()
            price = get_price()
            log(f"CHECK: USDT={usdt} PEPE={pepe} price={price} retCode={retcode} {msg}")

            if retcode != 0:
                log(f"Saldo fout, wacht 60s: {msg}")
                time.sleep(60)
                continue

            # NIET in positie + geld vrij = KOOP met alles (rente-op-rente)
            if pepe <= 0 and usdt >= 5 and price > 0:
                qty = usdt / price
                log(f"KOOP {qty} PEPE voor {usdt} USDT")
                res = place_order("Buy", qty)
                if res.get("retCode") == 0:
                    ENTRY_PRICE = price
                    send_telegram(f"🟢 KOOP PEPE: {qty:.0f} voor {usdt:.2f} USDT @ {price}")
                else:
                    send_telegram(f"KOOP mislukt: {res}")

            # WEL in positie = VERKOOP alleen bij +2% winst, geen stop-loss
            elif pepe > 0 and price > 0 and ENTRY_PRICE > 0:
                winst_pct = (price - ENTRY_PRICE) / ENTRY_PRICE * 100
                log(f"In positie, winst: {winst_pct:.2f}%")
                if winst_pct >= 2:
                    log(f"VERKOOP {pepe} PEPE, winst {winst_pct:.2f}%")
                    res = place_order("Sell", pepe)
                    if res.get("retCode") == 0:
                        send_telegram(f"🔴 VERKOOP PEPE: {pepe:.0f} winst {winst_pct:.2f}% @ {price}")
                        ENTRY_PRICE = 0.0

            time.sleep(60)
        except Exception as e:
            log(f"Loop fout: {e}")
            time.sleep(60)

@app.route("/")
def home():
    usdt, pepe, retcode, msg = get_balance()
    price = get_price()
    return jsonify({"status": "live", "symbol": SYMBOL, "USDT": usdt, "PEPE": pepe, "price": price, "retCode": retcode, "msg": msg})

@app.route("/test-key")
def test_key():
    usdt, pepe, retcode, msg = get_balance()
    return jsonify({"api_key_set": bool(BYBIT_API_KEY), "balances": {"USDT": usdt, "PEPE": pepe}, "retCode": retcode, "retMsg": msg})

@app.route("/check")
def check():
    usdt, pepe, retcode, msg = get_balance()
    price = get_price()
    return jsonify({"CHECK": f"USDT={usdt} PEPE={pepe} price={price} retCode={retcode}", "msg": msg})

@app.route("/telegram", methods=["POST"])
def telegram():
    return jsonify({"ok": True}), 200

# Start loop 1x
threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
