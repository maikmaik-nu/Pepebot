import os
import time
import hmac
import hashlib
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# --- Config uit Render Environment ---
API_KEY = os.environ.get("API_KEY", "")
API_SECRET = os.environ.get("API_SECRET", "")
BASE_URL = os.environ.get("BASE_URL", "https://api.bybit.eu").rstrip("/")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Hoeveel per trade? 10 = 10 USDT/USDC per Buy
TRADE_AMOUNT = float(os.environ.get("TRADE_AMOUNT", "10"))

def log(msg):
    print(msg, flush=True)

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        log(f"Telegram fout: {e}")

def bybit_headers(params):
    timestamp = str(int(time.time() * 1000))
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    sign = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": "5000",
        "X-BAPI-SIGN": sign,
    }
    return headers, timestamp

def bybit_get(path, params):
    try:
        p = dict(params)
        p["api_key"] = API_KEY
        p["timestamp"] = str(int(time.time() * 1000))
        p["recv_window"] = "5000"
        headers, _ = bybit_headers(p)
        r = requests.get(f"{BASE_URL}{path}", params=params, headers=headers, timeout=15)
        try:
            return r.json()
        except Exception:
            log(f"【entity-Bybit¦canonical_name=Bybit】 GET geen JSON: status={r.status_code} body={r.text[:200]}")
            return None
    except Exception as e:
        log(f"【entity-Bybit¦canonical_name=Bybit】 GET fout: {e}")
        return None

def bybit_post(path, params):
    try:
        p = dict(params)
        p["api_key"] = API_KEY
        p["timestamp"] = str(int(time.time() * 1000))
        p["recv_window"] = "5000"
        headers, _ = bybit_headers(p)
        r = requests.post(f"{BASE_URL}{path}", json=params, headers=headers, timeout=15)
        try:
            return r.json()
        except Exception:
            log(f"【entity-Bybit¦canonical_name=Bybit】 POST geen JSON: status={r.status_code} body={r.text[:200]}")
            return None
    except Exception as e:
        log(f"【entity-Bybit¦canonical_name=Bybit】 POST fout: {e}")
        return None

def get_saldo(coin):
    for account_type in ["SPOT", "UNIFIED"]:
        data = bybit_get("/v5/account/wallet-balance", {"accountType": account_type, "coin": coin})
        if not data:
            continue
        if data.get("retCode") != 0:
            code = data.get("retCode")
            msg = data.get("retMsg", "")
            log(f"Saldo fout {coin} {account_type}: {code} {msg}")
            if code == 10003:
                log("KEY FOUT 10003 - In 【entity-Bybit EU¦canonical_name=Bybit】: key Actief + Read aan + IP whitelist UIT")
            if code == 10005:
                log("KEY FOUT 10005 - key ongeldig/verlopen, maak nieuwe key in 【entity-Bybit EU¦canonical_name=Bybit】")
            continue
        try:
            result = data.get("result", {})
            coins = []
            if isinstance(result, dict):
                lst = result.get("list", [])
                if lst and isinstance(lst[0], dict):
                    coins = lst[0].get("coin", [])
            elif isinstance(result, list) and result:
                coins = result[0].get("coin", [])
            for c in coins:
                if c.get("coin") == coin:
                    saldo = float(c.get("walletBalance", 0) or 0)
                    return saldo
            return 0.0
        except Exception as e:
            log(f"Saldo parsen fout {coin}: {e}")
            continue
    return 0.0

def get_geld_en_pepe():
    usdt = get_saldo("USDT")
    usdc = get_saldo("USDC")
    pepe = get_saldo("PEPE")
    if usdt >= usdc:
        geld = usdt
        geld_coin = "USDT"
    else:
        geld = usdc
        geld_coin = "USDC"
    if geld == 0:
        geld_coin = "USDT"
    return geld, geld_coin, usdt, usdc, pepe

def get_prijs(symbool):
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={"category": "spot", "symbol": symbool}, timeout=10)
        try:
            data = r.json()
        except Exception:
            return 0.0
        if not data or data.get("retCode") != 0:
            log(f"Prijs {symbool} fout: {data}")
            return 0.0
        result = data.get("result", {})
        tickers = []
        if isinstance(result, dict):
            tickers = result.get("list", [])
        elif isinstance(result, list):
            tickers = result
        if not tickers:
            return 0.0
        prijs = float(tickers[0].get("lastPrice", 0) or 0)
        return prijs
    except Exception as e:
        log(f"Prijs {symbool} exception: {e}")
        return 0.0

def doe_buy():
    geld, geld_coin, usdt, usdc, pepe = get_geld_en_pepe()
    log(f"BUY signaal: Geld {geld:.2f} {geld_coin} (USDT={usdt:.2f} USDC={usdc:.2f}) PEPE={pepe}")

    if geld < TRADE_AMOUNT:
        log(f"Niet kopen: geld {geld} {geld_coin} is minder dan {TRADE_AMOUNT}")
        send_telegram(f"Niet gekocht: te weinig geld {geld} {geld_coin}")
        return {"ok": False, "reden": "te weinig geld"}

    if pepe > 0:
        log(f"Niet kopen: je hebt al {pepe} PEPE")
        return {"ok": False, "reden": "al PEPE in bezit"}

    if geld_coin == "USDC":
        symbool = "PEPEUSDC"
    else:
        symbool = "PEPEUSDT"

    prijs = get_prijs(symbool)
    if prijs <= 0:
        # Probeer andere symbool als eerste niet werkt
        ander = "PEPEUSDT" if symbool == "PEPEUSDC" else "PEPEUSDC"
        prijs = get_prijs(ander)
        if prijs > 0:
            symbool = ander

    if prijs <= 0:
        log("Niet kopen: prijs 0, Bybit geeft geen prijs")
        return {"ok": False, "reden": "prijs 0"}

    qty = int(TRADE_AMOUNT / prijs)
    if qty <= 0:
        log("Niet kopen: qty 0")
        return {"ok": False, "reden": "qty 0"}

    log(f"KOOP: {qty} PEPE via {symbool} voor {TRADE_AMOUNT} {geld_coin} @ {prijs}")
    data = bybit_post("/v5/order/create", {
        "category": "spot",
        "symbol": symbool,
        "side": "Buy",
        "orderType": "Market",
        "qty": str(qty),
    })
    log(f"Koop antwoord: {data}")
    if data and data.get("retCode") == 0:
        send_telegram(f"Gekocht: {qty} PEPE via {symbool}")
        return {"ok": True, "qty": qty, "symbool": symbool}
    else:
        return {"ok": False, "reden": str(data)}

def doe_sell():
    geld, geld_coin, usdt, usdc, pepe = get_geld_en_pepe()
    log(f"SELL signaal: PEPE={pepe} Geld {geld
