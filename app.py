import os
import time
import hmac
import hashlib
import json
import threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

# ================= CONFIG - HARDCODED, NIET VIA ENV =================
# Dit staat expres vast, zodat hij nooit meer per ongeluk naar .com gaat
BASE_URL = "https://api.bybit.eu"
API_KEY = os.getenv("BYBIT_API_KEY", "").strip()
API_SECRET = os.getenv("BYBIT_API_SECRET", "").strip()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

CATEGORY = "spot"

STATUS = {
    "in_positie": False,
    "koop_prijs": 0.0,
    "laatste_signaal": "geen",
    "laatste_check": "",
    "laatste_fout": ""
}

# ================= TELEGRAM =================
def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"Telegram niet ingesteld: {msg}", flush=True)
        return
    try:
        url = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage"
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print(f"Telegram fout: {e}", flush=True)

# ================= BYBIT V5 AUTH =================
def signed_headers(query_string):
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    # V5 sign: timestamp + api_key + recv_window + query_string
    sign_str = timestamp + API_KEY + recv_window + query_string
    sig = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": sig
    }

def safe_json(r):
    # Dit is de fix voor je "Expecting value" fout:
    # nooit meer crashen op een leeg antwoord
    print(f"【entity-Bybit¦canonical_name=Bybit】 status={r.status_code} body={r.text[:500]}", flush=True)
    try:
        return r.json()
    except Exception as e:
        print(f"【entity-Bybit¦canonical_name=Bybit】 geen JSON: {e}", flush=True)
        return {}

# ================= PRIJS / KLINES =================
def get_price(symbol):
    try:
        r = requests.get(
            BASE_URL + "/v5/market/tickers",
            params={"category": CATEGORY, "symbol": symbol},
            timeout=10
        )
        data = safe_json(r)
        lst = data.get("result", {}).get("list", [])
        if not lst:
            print(f"Prijs fout: lege list, response={data}", flush=True)
            return 0.0
        return float(lst[0].get("lastPrice", 0) or 0)
    except Exception as e:
        print(f"Prijs fout: {e}", flush=True)
        return 0.0

def get_klines(symbol, limit=100):
    try:
        r = requests.get(
            BASE_URL + "/v5/market/kline",
            params={"category": CATEGORY, "symbol": symbol, "interval": "15", "limit": str(limit)},
            timeout=10
        )
        data = safe_json(r)
        lst = list(reversed(data.get("result", {}).get("list", [])))
        candles = []
        for c in lst:
            candles.append({
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4])
            })
        return candles
    except Exception as e:
        print(f"Klines fout: {e}", flush=True)
        return []

# ================= SALDO - SPOT + UNIFIED =================
def get_balance_one(coin, account_type):
    """Probeer 1 accountType, geef saldo + raw data terug"""
    try:
        params = {"accountType": account_type, "coin": coin}
        query = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        headers = signed_headers(query)
        r = requests.get(
            BASE_URL + "/v5/account/wallet-balance",
            params=params,
            headers=headers,
            timeout=10
        )
        data = safe_json(r)
        if data.get("retCode") != 0:
            print(f"Balance fail {coin} {account_type}: {data}", flush=True)
            return 0.0, data
        result = data.get("result", {})
        lst = result.get("list", []) if isinstance(result, dict) else []
        for acc in lst:
            for c in acc.get("coin", []):
                if c.get("coin") == coin:
                    return float(c.get("walletBalance", 0) or 0), data
        return 0.0, data
    except Exception as e:
        print(f"Balance fail {coin} {account_type}: {e}", flush=True)
        return 0.0, {"error": str(e)}

def get_balance(coin):
    # Meeste kans: eerst SPOT, als dat 0 is ook UNIFIED proberen
    # Op Bybit EU staat spot-geld vaak in UNIFIED
    bal_spot, data_spot = get_balance_one(coin, "SPOT")
    if bal_spot > 0:
        return bal_spot, data_spot
    bal_uni, data_uni = get_balance_one(coin, "UNIFIED")
    if bal_uni > 0:
        return bal_uni, data_uni
    # Als allebei 0 zijn, geef de SPOT response terug voor debug
    # Zo zie je in /test-key of het 10003 is of echt 0 saldo
    return 0.0, data_spot

# ================= ORDER =================
def place_order(symbol, side, qty):
    try:
        timestamp = str(int(time.time() * 1000))
        recv_window = "5000"
        body = {
            "category": CATEGORY,
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": str(int(qty))
        }
        body_str = json.dumps(body)
        sign_str = timestamp + API_KEY + recv_window + body_str
        sig = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
        headers = {
            "X-BAPI-API-KEY": API_KEY,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN": sig,
            "Content-Type": "application/json"
        }
        r = requests.post(BASE_URL + "/v5/order/create", headers=headers, data=body_str, timeout=10)
        print(f"Order {side} response: {r.text}", flush=True)
        return safe_json(r)
    except Exception as e:
        print(f"Order fail: {e}", flush=True)
        return {"error": str(e)}

# ================= UT BOT ALLEEN - GEEN SUPERTREND, GEEN STOP-LOSS =================
def calc_atr(candles, period):
    if len(candles) < period + 1:
        return 0.0
    tr_list = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)
    if len(tr_list) < period:
        return 0.0
    return sum(tr_list[-period:]) / period

def calc_ut_bot(candles):
    # UT Bot 4, 14 op 15min - key=1, atr_period=14
    if len(candles) < 20:
        return "neutraal"
    atr = calc_atr(candles, 14)
    if atr == 0:
        return "neutraal"
    last = candles[-1]
    prev = candles[-2]
    if last["close"] > prev["close"] + (1.0 * atr * 0.5):
        return "buy"
    if last["close"] < prev["close"] - (1.0 * atr * 0.5):
        return "sell"
    return "hold"

# ================= BOT LOOP =================
def bot_loop():
    print("Bot loop gestart - UT Bot ALLEEN - GEEN Supertrend - GEEN stop-loss", flush=True)
    send_telegram("Bot gestart - UT Bot ALLEEN - Rente-op-rente AAN")
    while True:
        try:
            usdt, _ = get_balance("USDT")
            usdc, _ = get_balance("USDC")
            pepe, _ = get_balance("PEPE")

            # Kies waar je geld staat: USDT eerst, anders USDC
            if usdt >= 5:
                symbol, geld, munt = "PEPEUSDT", usdt, "USDT"
            elif usdc >= 5:
                symbol, geld, munt = "PEPEUSDC", usdc, "USDC"
            else:
                # Geen geld, maar wel PEPE? Dan nog prijs ophalen om te kunnen verkopen
                symbol, geld, munt = ("PEPEUSDT", 0.0, "USDT") if pepe == 0 else ("PEPEUSDT", 0.0, "USDT")

            price = get_price(symbol) if (geld >= 5 or pepe > 0) else 0.0
            pepe_waarde = pepe * price if price > 0 else 0.0
            in_positie = pepe_waarde >= 5

            candles = get_klines(symbol, 100) if price > 0 else []
            ut = calc_ut_bot(candles) if candles else "neutraal"

            log = f"Check: {munt} {geld:.2f} USDT {usdt:.2f} USDC {usdc:.2f} PEPE {pepe} waarde {pepe_waarde:.2f} prijs {price} UT:{ut} in_positie {in_positie}"
            print(log, flush=True)
            STATUS["laatste_check"] = log
            STATUS["in_positie"] = in_positie

            if price == 0:
                print("Niet traden: prijs 0", flush=True)
            else:
                if pepe > 0 and not STATUS["in_positie"]:
                    STATUS["in_positie"] = True
                    if STATUS["koop_prijs"] == 0:
                        STATUS["koop_prijs"] = price

                # KOOP: UT = buy, nog geen positie
                if not in_positie and ut == "buy" and geld >= 5:
                    # Rente-op-rente: 98% van alles wat je hebt
                    bedrag = geld * 0.98
                    qty = bedrag / price
                    if qty > 0:
                        print(f"KOOP: {int(qty)} PEPE voor {round(bedrag, 2)} {munt}", flush=True)
                        res = place_order(symbol, "Buy", qty)
                        if res.get("retCode") == 0:
                            STATUS["in_positie"] = True
                            STATUS["koop_prijs"] = price
                            STATUS["laatste_signaal"] = "buy"
                            send_telegram(f"GEKOCHT: {int(qty)} PEPE voor {round(bedrag, 2)} {munt} op {price}")
                        else:
                            print(f"Koop mislukt: {res}", flush=True)

                # VERKOOP: UT = sell, wel in positie
                if in_positie and ut == "sell" and pepe > 0:
                    print(f"VERKOOP: {int(pepe)} PEPE", flush=True)
                    res = place_order(symbol, "Sell", pepe)
                    if res.get("retCode") == 0:
                        STATUS["in_positie"] = False
                        STATUS["koop_prijs"] = 0.0
                        STATUS["laatste_signaal"] = "sell"
                        send_telegram(f"VERKOCHT: {int(pepe)} PEPE op {price}")

                # GEEN stop-loss hier - expres weggelaten

        except Exception as e:
            print(f"Loop fout: {e}", flush=True)
            STATUS["laatste_fout"] = str(e)
        time.sleep(60)

# ================= ROUTES =================
@app.route("/")
def home():
    return "Pepebot-3 draait - UT Bot ALLEEN!"

@app.route("/test-key")
def test_key():
    try:
        usdt, bal_usdt = get_balance("USDT")
        usdc, bal_usdc = get_balance("USDC")
        pepe, bal_pepe = get_balance("PEPE")
        prijs_usdt = get_price("PEPEUSDT")
        prijs_usdc = get_price("PEPEUSDC")
        candles = get_klines("PEPEUSDT", 20)
        ut = calc_ut_bot(candles) if candles else "geen data"
        return jsonify({
            "BASE_URL": BASE_URL,
            "KEY_INGESTELD": bool(API_KEY),
            "SECRET_INGESTELD": bool(API_SECRET),
            "TELEGRAM_INGESTELD": bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID),
            "USDT_saldo": usdt,
            "USDC_saldo": usdc,
            "PEPE_saldo": pepe,
            "PEPEUSDT_prijs": prijs_usdt,
            "PEPEUSDC_prijs": prijs_usdc,
            "STATUS": STATUS,
            "UT_BOT": ut,
            "SUPERTREND": "UIT - expres verwijderd",
            "STOP_LOSS": "UIT - expres verwijderd",
            "saldo_raw_usdt": bal_usdt,
            "saldo_raw_usdc": bal_usdc
        })
    except Exception as e:
        return jsonify({"FOUT": str(e)})

@app.route("/status")
def status():
    return jsonify(STATUS)

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
