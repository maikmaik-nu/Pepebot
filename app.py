import os, time, hmac, hashlib, json, threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDC"
CATEGORY = "spot"
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

print(f"KEY LOADED: key_len={len(API_KEY)} secret_len={len(API_SECRET)} base={BASE_URL} symbol={SYMBOL}", flush=True)

_lock = threading.Lock()
_last_check = 0
_prev_signal = "NEUTRAAL"
_bought_at = 0.0
_bought_qty = 0.0

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"TELEGRAM SKIP: {msg}", flush=True)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print(f"TELEGRAM FAIL: {e}", flush=True)

def _sign(params):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    query = "&".join(f"{k}={params[k]}" for k in sorted(params))
    to_sign = ts + API_KEY + recv + query
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json"
    }
    return headers

def bybit_get(path, params):
    r = requests.get(BASE_URL + path, params=params, headers=_sign(params), timeout=15)
    print(f"【entity-BYBIT¦canonical_name=Bybit】 GET {path} -> {r.status_code} {r.text[:300]}", flush=True)
    return r.json()

def bybit_post(path, body):
    # POST: sign over body, geen query-params
    ts = str(int(time.time() * 1000))
    recv = "5000"
    to_sign = ts + API_KEY + recv + json.dumps(body)
    sig = hmac.new(API_SECRET.encode(), to_sign.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": sig,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "Content-Type": "application/json"
    }
    r = requests.post(BASE_URL + path, json=body, headers=headers, timeout=15)
    print(f"【entity-BYBIT¦canonical_name=Bybit】 POST {path} -> {r.status_code} {r.text[:500]}", flush=True)
    return r.json()

def get_balances():
    out = {}
    for acct in ["SPOT", "UNIFIED"]:
        try:
            d = bybit_get("/v5/account/wallet-balance", {"accountType": acct})
            if d.get("retCode") != 0:
                print(f"Saldo check {acct}: retCode={d.get('retCode')} retMsg={d.get('retMsg')}", flush=True)
                continue
            lst = d.get("result", {}).get("list", [])
            if not lst:
                continue
            for coin in lst[0].get("coin", []):
                out[coin["coin"]] = float(coin.get("walletBalance", 0))
        except Exception as e:
            print(f"Saldo check {acct} FAIL: {e}", flush=True)
    return out

def get_price():
    r = requests.get(BASE_URL + "/v5/market/tickers",
        params={"category": CATEGORY, "symbol": SYMBOL}, timeout=10).json()
    return float(r["result"]["list"][0]["lastPrice"])

def get_signal(price):
    # Simpel signaal: snelle vs langzame gemiddelde over klines
    try:
        r = requests.get(BASE_URL + "/v5/market/kline",
            params={"category": CATEGORY, "symbol": SYMBOL, "interval": "15", "limit": 50}, timeout=10).json()
        closes = [float(x[4]) for x in r["result"]["list"]]
        closes.reverse()
        fast = sum(closes[-5:]) / 5
        slow = sum(closes[-20:]) / 20
        return "UP" if fast > slow else "NEUTRAAL"
    except Exception as e:
        print(f"SIGNAL FAIL: {e}", flush=True)
        return "NEUTRAAL"

def koop_pepe(usdc_bedrag):
    # FIX 170381: Market Buy op Spot -> qty = USDC, NIET PEPE
    qty_usdc = round(float(usdc_bedrag), 2)
    print(f"ORDER Buy PEPEUSDC qty {qty_usdc} USDC (Market)", flush=True)
    return bybit_post("/v5/order/create", {
        "category": "spot",
        "symbol": SYMBOL,
        "side": "Buy",
        "orderType": "Market",
        "qty": str(qty_usdc)
    })

def verkoop_pepe(pepe_qty):
    # Market Sell -> qty = PEPE (andersom als kopen)
    qty_pepe = int(float(pepe_qty))
    print(f"ORDER Sell PEPEUSDC qty {qty_pepe} PEPE (Market)", flush=True)
    return bybit_post("/v5/order/create", {
        "category": "spot",
        "symbol": SYMBOL,
        "side": "Sell",
        "orderType": "Market",
        "qty": str(qty_pepe)
    })

def do_check():
    global _last_check, _prev_signal, _bought_at, _bought_qty
    now = time.time()
    if not _lock.acquire(blocking=False):
        return {"status": "already running"}
    try:
        if now - _last_check < 30:
            return {"status": "too soon, wait 30s"}
        _last_check = now

        bal = get_balances()
        usdt = bal.get("USDT", 0)
        usdc = bal.get("USDC", 0)
        pepe = bal.get("PEPE", 0)
        price = get_price()
        signal = get_signal(price)
        up = signal == "UP"

        if signal != _prev_signal:
            print(f"Signaal {_prev_signal} -> {signal}", flush=True)
            _prev_signal = signal

        print(f"Check: USDT {usdt:.2f} USDC {usdc:.2f} PEPE {pepe:.0f} SYMBOL {SYMBOL} UP={up} prijs {price:.3e} bought@{_bought_at:.3e} qty {_bought_qty:.0f}", flush=True)

        # KOPEN: signaal UP, nog geen positie, genoeg USDC
        if up and pepe < 1000 and usdc >= 5:
            inzet = round(usdc * 0.99, 2)  # 99% van je saldo
            print(f"Rente-op-rente: koop voor {inzet:.2f} van {usdc:.2f} USDC", flush=True)
            res = koop_pepe(inzet)
            if res.get("retCode") == 0:
                _bought_at = price
                _bought_qty = inzet / price
                send_telegram(f"Gekocht: {inzet:.2f} USDC PEPE @ {price}")
                return {"status": "bought", "usdc": usdc, "price": price}
            else:
                print(f"KOOP GEFAALD: {res}", flush=True)
                return {"status": "buy failed", "error": res, "usdc": usdc, "price": price}

        # VERKOPEN: signaal weer NEUTRAAL en we hebben PEPE
        if not up and pepe >= 1000:
            res = verkoop_pepe(pepe)
            if res.get("retCode") == 0:
                send_telegram(f"Verkocht: {pepe:.0f} PEPE @ {price}")
                _bought_at = 0.0
                _bought_qty = 0.0
                return {"status": "sold", "pepe": pepe, "price": price}
            else:
                print(f"VERKOOP GEFAALD: {res}", flush=True)
                return {"status": "sell failed", "error": res, "pepe": pepe, "price": price}

        return {"status": "ok", "signal": signal, "usdt": usdt, "usdc": usdc, "pepe": pepe, "price": price}
    except Exception as e:
        print(f"CHECK FAIL: {e}", flush=True)
        return {"status": "fail", "error": str(e)}
    finally:
        _lock.release()

def bot_loop():
    time.sleep(10)
    while True:
        try:
            print("AUTO CHECK gestart", flush=True)
            res = do_check()
            print(f"AUTO CHECK klaar: {res}", flush=True)
        except Exception as e:
            print(f"AUTO CHECK FAIL: {e}", flush=True)
        time
