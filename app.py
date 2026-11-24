import os
import time
import hmac
import hashlib
import json
import requests
import logging
from datetime import datetime
from flask import Flask, jsonify, request

# ================= LOGGING =================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("Pepebot")

app = Flask(__name__)

# ================= CONFIG =================
# BELANGRIJK: Jouw key woont op .com, NIET op .eu
BASE = "https://api.bybit.com"

API_KEY = os.environ.get("BYBIT_API_KEY", "")
API_SECRET = os.environ.get("BYBIT_API_SECRET", "")

# Trading instellingen
DEFAULT_SYMBOL = os.environ.get("SYMBOL", "BTCUSDT")
DEFAULT_CATEGORY = "linear"  # linear = USDT perpetual
DEFAULT_LEVERAGE = int(os.environ.get("LEVERAGE", "10"))
DEFAULT_QTY_USD = float(os.environ.get("QTY_USD", "20"))  # hoeveel USDT per trade
DEFAULT_STOPLOSS_PCT = float(os.environ.get("STOPLOSS_PCT", "2.0"))
DEFAULT_TAKEPROFIT_PCT = float(os.environ.get("TAKEPROFIT_PCT", "4.0"))
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "pepe123")

# Bot status in geheugen
BOT_STATE = {
    "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "total_trades": 0,
    "wins": 0,
    "losses": 0,
    "last_signal": None,
    "last_trade": None,
    "last_error": None,
    "is_active": True
}

# ================= HELPERS =================

def get_timestamp():
    return str(int(time.time() * 1000))

def sign_request(params, timestamp, recv_window="5000"):
    """Maak Bybit V5 signature"""
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    payload = timestamp + API_KEY + recv_window + query
    signature = hmac.new(
        API_SECRET.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()
    return signature

def bybit_request(method, endpoint, params=None, body=None):
    """Algemene Bybit request"""
    if params is None:
        params = {}
    if body is None:
        body = {}

    timestamp = get_timestamp()
    recv_window = "5000"

    url = BASE + endpoint

    if method == "GET":
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        payload = timestamp + API_KEY + recv_window + query
        signature = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
            "Content-Type": "application/json"
        }
        if query:
            url = url + "?" + query
        r = requests.get(url, headers=headers, timeout=15)
    else:
        body_str = json.dumps(body) if body else ""
        # Voor POST met query params
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        payload = timestamp + API_KEY + recv_window + query + body_str
        signature = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
            "Content-Type": "application/json"
        }
        if query:
            url = url + "?" + query
        r = requests.post(url, headers=headers, data=body_str, timeout=15)

    try:
        return r.json()
    except Exception as e:
        return {"retCode": -1, "retMsg": f"JSON error: {str(e)} - raw: {r.text[:500]}"}

def get_price(symbol):
    """Haal huidige prijs op"""
    try:
        url = f"{BASE}/v5/market/tickers?category={DEFAULT_CATEGORY}&symbol={symbol}"
        r = requests.get(url, timeout=10)
        data = r.json()
        if data.get("retCode") == 0:
            lst = data["result"]["list"]
            if lst:
                return float(lst[0]["lastPrice"])
    except Exception as e:
        logger.error(f"Price error: {e}")
    return None

def get_balance():
    """Haal USDT balance op"""
    params = {"accountType": "UNIFIED", "coin": "USDT"}
    return bybit_request("GET", "/v5/account/wallet-balance", params=params)

def get_positions(symbol=None):
    """Haal open posities op"""
    params = {"category": DEFAULT_CATEGORY}
    if symbol:
        params["symbol"] = symbol
    return bybit_request("GET", "/v5/position/list", params=params)

def set_leverage(symbol, leverage):
    """Zet leverage"""
    body = {
        "category": DEFAULT_CATEGORY,
        "symbol": symbol,
        "buyLeverage": str(leverage),
        "sellLeverage": str(leverage)
    }
    return bybit_request("POST", "/v5/position/set-leverage", body=body)

def place_order(symbol, side, qty, tp_price=None, sl_price=None):
    """Plaats market order met TP/SL"""
    body = {
        "category": DEFAULT_CATEGORY,
        "symbol": symbol,
        "side": side,  # Buy of Sell
        "orderType": "Market",
        "qty": str(qty),
        "timeInForce": "GTC",
    }
    if tp_price:
        body["takeProfit"] = str(tp_price)
    if sl_price:
        body["stopLoss"] = str(sl_price)
    if tp_price or sl_price:
        body["tpslMode"] = "Full"

    result = bybit_request("POST", "/v5/order/create", body=body)
    logger.info(f"Order {side} {symbol} qty={qty} -> {result}")
    return result

def close_position(symbol, side):
    """Sluit positie - side is de kant die je nu hebt, dus omgekeerd sluiten"""
    # Als je Long hebt (Buy), moet je Sell om te sluiten
    close_side = "Sell" if side == "Buy" else "Buy"
    # Haal positie qty op
    pos_data = get_positions(symbol)
    qty = "0"
    if pos_data.get("retCode") == 0:
        for p in pos_data["result"]["list"]:
            if p["symbol"] == symbol and float(p["size"]) > 0:
                qty = p["size"]
                break
    if qty == "0" or float(qty) == 0:
        return {"retCode": -1, "retMsg": "Geen open positie om te sluiten"}
    return place_order(symbol, close_side, qty)

def calculate_qty(symbol, usd_amount):
    """Bereken hoeveel coins je krijgt voor X USDT"""
    price = get_price(symbol)
    if not price or price == 0:
        return None, None
    qty = usd_amount / price
    # Bybit wil max 3 decimalen voor BTC, voor andere coins anders - we ronden af
    # Voor BTC: 0.001, voor ETH: 0.01 etc. Simpel: rond af op 3 decimalen
    qty = round(qty, 3)
    if qty <= 0:
        qty = 0.001
    return qty, price

def do_trade(symbol, action, usd_amount=None, leverage=None):
    """Voer een trade uit: buy / sell / close"""
    if usd_amount is None:
        usd_amount = DEFAULT_QTY_USD
    if leverage is None:
        leverage = DEFAULT_LEVERAGE

    symbol = symbol.upper()
    action = action.lower()

    # Zet leverage eerst
    set_leverage(symbol, leverage)

    qty, price = calculate_qty(symbol, usd_amount)
    if qty is None:
        return {"ok": False, "error": "Kon prijs niet ophalen"}

    # TP / SL berekenen
    if action == "buy":
        side = "Buy"
        tp = round(price * (1 + DEFAULT_TAKEPROFIT_PCT / 100), 2)
        sl = round(price * (1 - DEFAULT_STOPLOSS_PCT / 100), 2)
    elif action == "sell":
        side = "Sell"
        tp = round(price * (1 - DEFAULT_TAKEPROFIT_PCT / 100), 2)
        sl = round(price * (1 + DEFAULT_STOPLOSS_PCT / 100), 2)
    elif action in ["close", "flat", "exit"]:
        res = close_position(symbol, "Buy")  # probeer Buy kant
        if res.get("retCode") != 0:
            res = close_position(symbol, "Sell")
        return {"ok": res.get("retCode") == 0, "result": res}
    else:
        return {"ok": False, "error": f"Onbekende actie: {action}. Gebruik buy, sell of close"}

    result = place_order(symbol, side, qty, tp_price=tp, sl_price=sl)

    if result.get("retCode") == 0:
        BOT_STATE["total_trades"] += 1
        BOT_STATE["last_trade"] = {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": price,
            "tp": tp,
            "sl": sl,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        return {"ok": True, "order": result, "trade": BOT_STATE["last_trade"]}
    else:
        BOT_STATE["last_error"] = result.get("retMsg")
        return {"ok": False, "error": result.get("retMsg"), "full": result}

# ================= ROUTES =================

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "bot": "Pepebot",
        "base": BASE,
        "symbol": DEFAULT_SYMBOL,
        "leverage": DEFAULT_LEVERAGE,
        "qty_usd": DEFAULT_QTY_USD,
        "started_at": BOT_STATE["started_at"],
        "endpoints": [
            "/test-key",
            "/balance",
            "/price?symbol=BTCUSDT",
            "/positions",
            "/status",
            "/buy?symbol=BTCUSDT&usd=20",
            "/sell?symbol=BTCUSDT&usd=20",
            "/close?symbol=BTCUSDT",
            "/webhook (POST van TradingView)"
        ]
    })

@app.route("/test-key")
def test_key():
    if not API_KEY or not API_SECRET:
        return jsonify({
            "ok": False,
            "error": "API key of secret ontbreekt in Render Environment Variables"
        }), 500

    data = get_balance()

    if data.get("retCode") == 0:
        coins = data["result"]["list"][0]["coin"]
        usdt = "0"
        for c in coins:
            if c["coin"] == "
