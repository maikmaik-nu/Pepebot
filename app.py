import os
import time
import hmac
import hashlib
import json
import threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = os.getenv("BASE_URL", "https://api.bybit.eu").strip()
API_KEY = os.getenv("【entity-BYBIT_API_KEY¦canonical_name=Bybit】", "").strip()
API_SECRET = os.getenv("【entity-BYBIT_API_SECRET¦canonical_name=Bybit】", "").strip()
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "").strip()

BUY_AMOUNT = 10.0
CHECK_SECONDS = 60
running = False

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

def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        log(f"Geen JSON terug, status {resp.status_code}, body: {resp.text[:300]}")
        return {}

def get_balance_unified(coin):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    query = "accountType=UNIFIED&coin=" + coin
    sign = make_sign(ts, recv, query)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
    }
    r = safe_json(requests.get(BASE_URL + "/v5/account/wallet-balance?" + query, headers=headers, timeout=10))
    if r.get("retCode") != 0:
        log(f"Balance UNIFIED fail {coin}: {r}")
        return 0.0
    lst = r.get("result", {}).get("list", []) or []
    if not lst:
        return 0.0
    coins = lst[0].get("coin", []) or []
    for c in coins:
        if c.get("coin") == coin:
            return float(c.get("walletBalance") or 0)
    return 0.0

def get_balance_funding(coin):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    query = "accountType=FUND&coin=" + coin
    sign = make_sign(ts, recv, query)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
    }
    r = safe_json(requests.get(BASE_URL + "/v5/asset/transfer/query-account-coins-balance?" + query, headers=headers, timeout=10))
    if r.get("retCode") != 0:
        return 0.0
    res = r.get("result", {})
    lst = []
    if isinstance(res, dict):
        lst = res.get("list", []) or []
        if not lst and res.get("coin"):
            lst = [res]
    elif isinstance(res, list):
        lst = res
    for c in lst:
        if isinstance(c, dict) and c.get("coin") == coin:
            return float(c.get("balance") or c.get("walletBalance") or 0)
    return 0.0

def get_balance():
    try:
        if not API_KEY or not API_SECRET:
            log("Balance fout: geen API key in Render")
            return 0.0, 0.0, 0.0
        usdc = get_balance_unified("USDC")
        usdt = get_balance_unified("USDT")
        pepe = get_balance_unified("PEPE")
        if usdc == 0:
            usdc = get_balance_funding("USDC")
        if usdt == 0:
            usdt = get_balance_funding("USDT")
        if pepe == 0:
            pepe = get_balance_funding("PEPE")
        return usdt, usdc, pepe
    except Exception as e:
        log(f"Balance fout: {e}")
        return 0.0, 0.0, 0.0

def get_closes(symbol):
    try:
        r = safe_json(requests.get(
            BASE_URL + "/v5/market/kline",
            params={"category": "spot", "symbol": symbol, "interval": "15", "limit": 200},
            timeout=10,
        ))
        if r.get("retCode") != 0:
            log(f"Klines fail {symbol}: {r}")
            return None
        lst = r.get("result", {}).get("list", []) or []
        closes = [float(x[4]) for x in lst]
        if len(closes) < 60:
            return None
        closes.reverse()
        return closes[:-1]
    except Exception as e:
        log(f"Klines fout: {e}")
        return None

def trend_up(closes):
    try:
        if len(closes) < 50:
            return False
        sma50 = sum(closes[-50:]) / 50
        return closes[-1] > sma50
    except Exception:
        return False

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
        r = safe_json(requests.post(
            BASE_URL + "/v5/order/create",
            headers=headers,
            data=body_str,
            timeout=10,
        ))
        log(f"ORDER {side} {symbol} {qty}: {r}")
        return r
    except Exception as e:
        log(f"Order fout: {e}")
        return {}

def bot_loop():
    global running
    if running:
        return
    running = True
    log("Bot loop gestart - definitieve versie - PEPEUSDC / UNIFIED+FUNDING")
    send_tg("Pepebot-3 gestart - definitieve versie AAN")
    while True:
        try:
            usdt, usdc, pepe = get_balance()
            if usdc >= 1:
                symbol, money = "PEPEUSDC", usdc
            elif usdt >= 1:
                symbol, money = "PEPEUSDT", usdt
                log("WAARSCHUWING: geld staat in USDT, Bybit EU heeft geen USDT-paren. Zet om naar USDC.")
            else:
                symbol, money = "PEPEUSDC", 0.0
            closes = get_closes(symbol)
            if closes is None:
                log("Geen klines, over 60 sec opnieuw")
                time.sleep(CHECK_SECONDS)
                continue
            price = closes[-1]
            up = trend_up(closes)
            log(f"Check: USDT {usdt:.2f} USDC {usdc:.2f} PEPE {pepe:.0f} SYMBOL {symbol} UP={up} prijs {price}")
            if up and money >= 1 and pepe == 0:
                buy_usd = min(money, BUY_AMOUNT)
                qty = int(buy_usd / price)
                if qty > 0:
                    res = place_order(symbol, "Buy", qty)
                    if res and res.get("retCode") == 0:
                        send_tg(f"PEPE GEKOCHT: {qty} voor ~{buy_usd:.2f} prijs {price}")
                    else:
                        log(f"BUY afgewezen: {res}")
            elif not up and pepe > 0:
                res = place_order(symbol, "Sell", int(pepe))
                if res and res.get("retCode") == 0:
                    send_tg(f"PEPE VERKOCHT: {pepe:.0f} prijs {price}")
                else:
                    log(f"SELL afgewezen: {res}")
        except Exception as e:
            log(f"Loop fout, gaat door: {e}")
        time.sleep(CHECK_SECONDS)

def start_loop():
    threading.Thread(target=bot_loop, daemon=True).start()

@app.route("/")
def home():
    start_loop()
    return "Pepebot-3 live - definitieve versie"

@app.route("/ping")
def ping():
    return "pong"

@app.route("/test-key")
def test_key():
    usdt, usdc, pepe = get_balance()
    return jsonify({
        "ok": True,
        "USDT": usdt,
        "USDC": usdc,
        "PEPE": pepe,
        "base": BASE_URL,
        "keys_set": bool(API_KEY and API_SECRET),
    })

start_loop()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
