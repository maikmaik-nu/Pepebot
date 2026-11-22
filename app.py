import os
import time
import hmac
import hashlib
import json
import threading
import requests
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = "https://api.bybit.eu"
SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")

TRADE_USDT = 10
CHECK_EVERY_SEC = 60
RECV_WINDOW = "5000"

# Strategie
TAKE_PROFIT_PCT = 0.02  # +2%
STOP_LOSS_PCT = 0.02    # -2%
STATE_FILE = "bot_state.json"

# ===== SIGNING =====
def _headers(timestamp, payload_str=""):
    msg = timestamp + API_KEY + RECV_WINDOW + payload_str
    sign = hmac.new(API_SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": RECV_WINDOW,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json"
    }

def bybit_get(path, params=None):
    if params is None:
        params = {}
    timestamp = str(int(time.time() * 1000))
    query = "&".join([f"{k}={params[k]}" for k in sorted(params)])
    headers = _headers(timestamp, query)
    try:
        r = requests.get(BASE_URL + path, params=params, headers=headers, timeout=10)
        return r.status_code, r.json()
    except Exception as e:
        return 0, {"error": str(e)}

def bybit_post(path, body):
    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(body, separators=(',', ':'))
    headers = _headers(timestamp, body_str)
    try:
        r = requests.post(BASE_URL + path, data=body_str, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}

# ===== STATE - overleeft een restart =====
def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except:
        return {"buy_price": 0.0}

def save_state(state):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print(f"STATE SAVE ERROR: {e}", flush=True)

# ===== BALANCE - FIX: alleen UNIFIED, geen dubbeltelling =====
def get_balance():
    status, data = bybit_get("/v5/account/wallet-balance", {
        "accountType": "UNIFIED",
        "coin": "USDT,PEPE"
    })
    usdt = 0.0
    pepe = 0.0
    retcode = data.get("retCode") if isinstance(data, dict) else None
    try:
        for item in data.get("result", {}).get("list", []):
            for coin in item.get("coin", []):
                # Let op: Bybit V5 gebruikt "coin" list, niet "balance"
                if coin.get("coin") == "USDT":
                    usdt = float(coin.get("walletBalance") or 0)
                if coin.get("coin") == "PEPE":
                    pepe = float(coin.get("walletBalance") or 0)
    except Exception as e:
        print(f"BALANCE PARSE ERROR: {e} data={data}", flush=True)
    return usdt, pepe, retcode

def get_price():
    try:
        r = requests.get(f"{BASE_URL}/v5/market/tickers", params={
            "category": CATEGORY,
            "symbol": SYMBOL
        }, timeout=10)
        data = r.json()
        return float(data["result"]["list"][0]["lastPrice"])
    except:
        return 0

def get_qty_step():
    """Haalt de juiste qty-afronding op voor PEPE, zodat 【entity-Bybit¦canonical_name=Bybit】 je order niet weigert."""
    try:
        r = requests.get(f"{BASE_URL}/v5/market/instruments-info", params={
            "category": CATEGORY,
            "symbol": SYMBOL
        }, timeout=10)
        data = r.json()
        info = data["result"]["list"][0]
        lot_filter = info["lotSizeFilter"]
        return float(lot_filter["qtyStep"]), float(lot_filter["minOrderQty"])
    except:
        return 1.0, 1.0

def round_down_qty(qty, step):
    if step <= 0:
        return int(qty)
    return int(qty / step) * step

def place_order(side, qty):
    body = {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "side": side,
        "orderType": "Market",
        "qty": str(qty)
    }
    return bybit_post("/v5/order/create", body)

# ===== BOT LOOP - FIX: echte strategie =====
def bot_loop():
    qty_step, min_qty = get_qty_step()
    print(f"START: qtyStep={qty_step} minQty={min_qty}", flush=True)

    while True:
        try:
            usdt, pepe, retcode = get_balance()
            price = get_price()
            state = load_state()
            buy_price = state.get("buy_price", 0.0)

            # FIX: positie uit echte balance, niet uit geheugen
            pepe_value = pepe * price
            in_position = pepe_value > 1.0  # meer dan $1 aan PEPE = in positie

            print(f"CHECK: USDT={usdt:.2f} PEPE={pepe} value=${pepe_value:.2f} price={price} buy_price={buy_price} in_position={in_position} retCode={retcode}", flush=True)

            if retcode != 0:
                print("Bot pauzeert: geen saldo / geen rechten", flush=True)
                time.sleep(CHECK_EVERY_SEC)
                continue

            if price <= 0:
                time.sleep(CHECK_EVERY_SEC)
                continue

            # --- KOPEN ---
            if not in_position and usdt >= TRADE_USDT:
                raw_qty = TRADE_USDT / price
                qty = round_down_qty(raw_qty, qty_step)
                if qty >= min_qty:
                    res = place_order("Buy", qty)
                    print(f"BUY: qty={qty} price={price} res={res}", flush=True)
                    # Alleen opslaan als order echt gelukt is
                    if res.get("retCode") == 0:
                        save_state({"buy_price": price})
                    else:
                        print(f"BUY FAILED: {res}", flush=True)
                else:
                    print(f"BUY SKIPPED: qty {qty} < min {min_qty}", flush=True)

            # --- VERKOPEN: alleen bij TP of SL ---
            elif in_position and pepe > 0 and buy_price > 0:
                pnl_pct = (price - buy_price) / buy_price
                tp_price = buy_price * (1 + TAKE_PROFIT_PCT)
                sl_price = buy_price * (1 - STOP_LOSS_PCT)

                print(f"HOLD: pnl={pnl_pct*100:.2f}% TP bij {tp_price} SL bij {sl_price}", flush=True)

                if price >= tp_price or price <= sl_price:
                    qty = round_down_qty(pepe, qty_step)
                    if qty >= min_qty:
                        reason = "TAKE-PROFIT" if price >= tp_price else "STOP-LOSS"
                        res = place_order("Sell", qty)
                        print(f"SELL {reason}: qty={qty} price={price} pnl={pnl_pct*100:.2f}% res={res}", flush=True)
                        if res.get("retCode") == 0:
                            save_state({"buy_price": 0.0})
                    else:
                        print(f"SELL SKIPPED: qty {qty} < min {min_qty} - dust blijft staan", flush=True)
                else:
                    print("HOLD: wachten op TP/SL", flush=True)

            # Edge case: we hebben PEPE maar geen buy_price (bijv. handmatig gekocht)
            elif in_position and pepe > 0 and buy_price == 0:
                print("HOLD: PEPE gevonden maar geen buy_price, stel buy_price = huidige prijs", flush=True)
                save_state({"buy_price": price})

        except Exception as e:
            print(f"LOOP ERROR: {e}", flush=True)
        time.sleep(CHECK_EVERY_SEC)

# ===== ROUTES =====
@app.route("/")
def home():
    usdt, pepe, retcode = get_balance()
    state = load_state()
    price = get_price()
    return jsonify({
        "status": "live",
        "symbol": SYMBOL,
        "USDT": usdt,
        "PEPE": pepe,
        "PEPE_value_usd": pepe * price,
        "price": price,
        "buy_price": state.get("buy_price", 0.0),
        "in_position": (pepe * price) > 1.0,
        "bybit_retCode": retcode
    })

@app.route("/test-key")
def test_key():
    usdt, pepe, retcode = get_balance()
    return jsonify({
        "accountType_used": "UNIFIED",
        "api_key_set": bool(API_KEY),
        "balances": {"PEPE": pepe, "USDT": usdt},
        "base_url": BASE_URL,
        "bybit_retCode": retcode,
        "bybit_retMsg": "success" if retcode == 0 else "check",
        "http_status": 200
        # FIX: geen "raw" meer, lekt geen data
    })

@app.route("/balance")
def balance():
    usdt, pepe, retcode = get_balance()
    return jsonify({"USDT": usdt, "PEPE": pepe, "retCode": retcode})

threading.Thread(target=bot_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
