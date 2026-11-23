import os, time, hmac, hashlib, json, threading, requests
from flask import Flask, jsonify

app = Flask(__name__)

# ===== CONFIG =====
API_KEY = os.environ.get("BYBIT_API_KEY", "")
API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
BASE_URL = "https://api.bybit.eu"  # NOOIT .com gebruiken
SYMBOL = "PEPEUSDT"
INTERVAL = "15"
FACTOR = 4.0
ATR_PERIOD = 14

print(f"Bot loop gestart - UTBOT {int(FACTOR)}-{ATR_PERIOD}, 15m, SPOT via bybit.eu", flush=True)
print(f"KEY LOADED: {'JA' if API_KEY else 'NEE - CHECK RENDER ENV!'} len={len(API_KEY)}", flush=True)

# ===== 【entity-BYBIT¦canonical_name=Bybit】 HELPERS =====
def sign(timestamp, query_string):
    payload = f"{timestamp}{API_KEY}5000{query_string}"
    return hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

def bybit_get(path, params):
    # Sorteer params, Bybit eist dit voor signature
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    ts = str(int(time.time() * 1000))
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": "5000",
        "X-BAPI-SIGN": sign(ts, query),
        "Content-Type": "application/json"
    }
    try:
        r = requests.get(BASE_URL + path + "?" + query, headers=headers, timeout=15)
        # FIX voor "Expecting value char 0": lege body niet parsen
        if not r.text or not r.text.strip():
            return {"retCode": -1, "retMsg": "Lege response van Bybit (UNIFIED leeg)", "result": {}}
        try:
            return r.json()
        except:
            return {"retCode": -1, "retMsg": f"Geen JSON, raw: {r.text[:200]}", "result": {}}
    except Exception as e:
        return {"retCode": -1, "retMsg": str(e), "result": {}}

def get_balance(coin_wanted):
    # Probeer SPOT, daarna UNIFIED. Bybit EU zet geld soms in UNIFIED.
    total = 0.0
    last_err = ""
    for account in ["SPOT", "UNIFIED"]:
        data = bybit_get("/v5/account/wallet-balance", {"accountType": account, "coin": coin_wanted})
        if data.get("retCode") != 0:
            last_err = f"{account}: {data.get('retCode')} {data.get('retMsg')}"
            print(f"Saldo fout {coin_wanted} {account}: {data}", flush=True)
            continue
        try:
            lst = data.get("result", {}).get("list", [])
            # FIX: 【entity-Bybit¦canonical_name=Bybit】 geeft soms list, soms dict
            if isinstance(lst, dict):
                lst = [lst]
            for acc in lst:
                coins = acc.get("coin", [])
                if isinstance(coins, dict):
                    coins = [coins]
                for c in coins:
                    if c.get("coin") == coin_wanted:
                        # walletBalance > availableToWithdraw, gebruik walletBalance
                        v = float(c.get("walletBalance") or c.get("availableToWithdraw") or 0)
                        total += v
        except Exception as e:
            print(f"Saldo parse fout {coin_wanted} {account}: {e}", flush=True)
    return total, last_err

def get_klines():
    try:
        # Klines is public, geen signature nodig
        r = requests.get(f"{BASE_URL}/v5/market/kline", params={"category": "spot", "symbol": SYMBOL, "interval": INTERVAL, "limit": 200}, timeout=15)
        data = r.json()
        kl = data.get("result", {}).get("list", [])
        # Bybit geeft nieuwste eerst, draai om naar oud -> nieuw
        kl.reverse()
        return kl
    except Exception as e:
        print(f"Kline fout: {e}", flush=True)
        return []

def calc_utbot(klines):
    if len(klines) < ATR_PERIOD + 5:
        return "NONE", 0, 0
    closes = [float(k[4]) for k in klines]
    highs = [float(k[2]) for k in klines]
    lows = [float(k[3]) for k in klines]
    trs = []
    for i in range(1, len(klines)):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        trs.append(tr)
    atr = sum(trs[:ATR_PERIOD]) / ATR_PERIOD
    atr_list = [atr]
    for i in range(ATR_PERIOD, len(trs)):
        atr = (atr * (ATR_PERIOD - 1) + trs[i]) / ATR_PERIOD
        atr_list.append(atr)
    trailings = []
    trailing = 0.0
    for idx in range(ATR_PERIOD, len(closes)):
        a = atr_list[idx - ATR_PERIOD]
        x_atr = a * FACTOR
        close = closes[idx]
        if idx == ATR_PERIOD:
            prev_c = closes[idx-1]
            trailing = close - x_atr if close > prev_c else close + x_atr
        else:
            if close > trailing:
                trailing = max(trailing, close - x_atr)
            else:
                trailing = min(trailing, close + x_atr)
        trailings.append(trailing)
    last_close = closes[-1]
    prev_close = closes[-2]
    last_trail = trailings[-1]
    prev_trail = trailings[-2]
    # ECHTE kruising, niet "prijs boven lijn"
    if last_close > last_trail and prev_close <= prev_trail:
        signal = "BUY"
    elif last_close < last_trail and prev_close >= prev_trail:
        signal = "SELL"
    else:
        signal = "NONE"
    return signal, last_trail, atr_list[-1]

def place_order(side, qty):
    ts = str(int(time.time() * 1000))
    body = {"category": "spot", "symbol": SYMBOL, "side": side, "orderType": "Market", "qty": str(qty)}
    body_str = json.dumps(body)
    payload = f"{ts}{API_KEY}5000{body_str}"
    sig = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    headers = {"X-BAPI-API-KEY": API_KEY, "X-BAPI-TIMESTAMP": ts, "X-BAPI-RECV-WINDOW": "5000", "X-BAPI-SIGN": sig, "Content-Type": "application/json"}
    try:
        r = requests.post(BASE_URL + "/v5/order/create", headers=headers, data=body_str, timeout=15)
        print(f"ORDER {side} {qty}: {r.text[:500]}", flush=True)
        return r.json()
    except Exception as e:
        print(f"ORDER fout: {e}", flush=True)
        return {}

# ===== BOT LOOP =====
def bot_loop():
    while True:
        try:
            klines = get_klines()
            if not klines:
                time.sleep(60)
                continue
            prijs = float(klines[-1][4])
            signal, trail, atr = calc_utbot(klines)
            usdt, err_usdt = get_balance("USDT")
            pepe, err_pepe = get_balance("PEPE")
            # Als beide 0 en er is een 10003 fout, niet traden
            if usdt == 0 and pepe == 0 and "10003" in (err_usdt + err_pepe):
                print(f"Check: Prijs {prijs} Geld 0.00 PEPE 0 Signaal NONE - OORZAAK: 10003 key/domein mismatch - key MOET van bybit.eu zijn", flush=True)
                time.sleep(60)
                continue
            print(f"Check: Prijs {prijs} Geld {usdt:.2f} PEPE {pepe:.0f} Signaal {signal}", flush=True)
            if signal == "BUY" and usdt > 5:
                qty = int((usdt / prijs) * 0.99)  # rente-op-rente: hele saldo, minus fee
                if qty > 0:
                    place_order("Buy", qty)
            elif signal == "SELL" and pepe > 0:
                place_order("Sell", int(pepe))
        except Exception as e:
            print(f"BOT ERROR: {e}", flush=True)
        time.sleep(60)

threading.Thread(target=bot_loop, daemon=True).start()

# ===== ROUTES =====
@app.route("/")
def home():
    return "Pepebot-3 live - UTBOT 4-14"

@app.route("/test-key")
def test_key():
    usdt, e1 = get_balance("USDT")
    pepe, e2 = get_balance("PEPE")
    klines = get_klines()
    prijs = float(klines[-1][4]) if klines else 0
    signal, _, _ = calc_utbot(klines) if klines else ("NONE", 0, 0)
    return jsonify({"base_url": BASE_URL, "key_loaded": bool(API_KEY), "prijs": prijs, "USDT": usdt, "PEPE": pepe, "signaal": signal, "error_usdt": e1, "error_pepe": e2})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
