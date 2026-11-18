import os, requests
from flask import Flask, request, jsonify

app = Flask(__name__)

KEY = os.getenv("BYBIT_API_KEY","").strip().strip('"').strip("'")
SECRET = os.getenv("BYBIT_API_SECRET","").strip().strip('"').strip("'")
TG_TOKEN = os.getenv("TELEGRAM_TOKEN","").strip()
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID","").strip()

print(f"KEY LOADED: {len(KEY)} chars, start={KEY[:4]}****", flush=True)

def tg(msg):
    if not TG_TOKEN or not TG_CHAT: return
    try: requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json={"chat_id": TG_CHAT, "text": msg}, timeout=10)
    except Exception as e: print("TG fail", e, flush=True)

def get_balance_pybit():
    # Officiële lib = juiste signature, juiste domein
    try:
        from 【entity-pybit¦canonical_name=Bybit】.unified_trading import HTTP
        for domain in ["【entity-bybit¦canonical_name=Bybit】.eu", "bytick.com"]:
            try:
                s = HTTP(testnet=False, api_key=KEY, api_secret=SECRET, domain=domain)
                # 1. UNIFIED wallet-balance
                r = s.get_wallet_balance(accountType="UNIFIED", coin="USDT")
                print(f"【entity-PYBIT¦canonical_name=Bybit】 {domain} UNIFIED: {r}", flush=True)
                if r.get("retCode") == 0:
                    c = r["result"]["list"][0]["coin"][0]
                    return float(c["walletBalance"]), float(c.get("availableToWithdraw", 0)), f"UNIFIED-{domain}"
                # 2. SPOT
                r2 = s.get_wallet_balance(accountType="SPOT", coin="USDT")
                print(f"【entity-PYBIT¦canonical_name=Bybit】 {domain} SPOT: {r2}", flush=True)
                if r2.get("retCode") == 0:
                    c = r2["result"]["list"][0]["coin"][0]
                    return float(c["walletBalance"]), 0, f"SPOT-{domain}"
                # 3. Coins balance (Asset endpoint, andere permissie)
                r3 = s.get_coins_balance(accountType="SPOT", coin="USDT")
                print(f"PYBIT {domain} COINS: {r3}", flush=True)
                if r3.get("retCode") == 0:
                    b = r3["result"]["balance"][0]
                    return float(b["walletBalance"]), float(b.get("availableToWithdraw", 0)), f"COINS-{domain}"
            except Exception as e:
                print(f"PYBIT {domain} fail: {e}", flush=True)
                continue
    except Exception as e:
        print("pybit import fail", e, flush=True)
    return 0, 0, None

def get_price():
    for domain in ["api.bybit.eu", "api.bytick.com"]:
        try:
            r = requests.get(f"https://{domain}/v5/market/tickers", params={"category": "spot", "symbol": "PEPEUSDT"}, timeout=10)
            return float(r.json()["result"]["list"][0]["lastPrice"])
        except: continue
    return 0

def get_klines():
    try:
        r = requests.get("https://api.bybit.eu/v5/market/kline", params={"category": "spot", "symbol": "PEPEUSDT", "interval": "15", "limit": 100}, timeout=15)
        kl = r.json()["result"]["list"][::-1]
        return [float(x[4]) for x in kl], [float(x[2]) for x in kl], [float(x[3]) for x in kl]
    except: return [], [], []

def utbot(closes, highs, lows):
    if len(closes) < 30: return None
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(1, len(closes))]
    atr = sum(trs[-14:]) / 14
    hl2 = (highs[-1]+lows[-1])/2
    upper, lower = hl2 + 4.0*atr, hl2 - 4.0*atr
    if closes[-2] < lower and closes[-1] >= lower: return "BUY"
    if closes[-2] > upper and closes[-1] <= upper: return "SELL"
    return None

def place_order(side, usdt_val):
    try:
        from pybit.unified_trading import HTTP
        price = get_price()
        if price == 0: return {"retCode": -1, "retMsg": "no price"}
        qty = str(int(usdt_val / price))
        s = HTTP(testnet=False, api_key=KEY, api_secret=SECRET, domain="bybit.eu")
        r = s.place_order(category="spot", symbol="PEPEUSDT", side=side, orderType="Market", qty=qty)
        print(f"ORDER {side} {qty} -> {r}", flush=True)
        return r
    except Exception as e:
        return {"retCode": -1, "retMsg": str(e)}

@app.route("/")
def home(): return "Pepebot-3 live"

@app.route("/test-key")
def test_key():
    usdt, avail, acct = get_balance_pybit()
    return jsonify({"status": "OK" if acct else "FOUT", "account": acct, "usdt": usdt, "available": avail, "key_start": KEY[:4]})

@app.route("/check")
def check():
    print("=== Bot check gestart ===", flush=True)
    usdt, avail, acct = get_balance_pybit()
    if usdt < 5:
        tg(f"Bot stopt: geen saldo. USDT={usdt} acct={acct}")
        return jsonify({"status": "geen saldo", "usdt": usdt, "acct": acct})
    closes, highs, lows = get_klines()
    sig = utbot(closes, highs, lows)
    print(f"UTBOT sig={sig} last={closes[-1] if closes else None} usdt={usdt} acct={acct}", flush=True)
    if sig == "BUY" and usdt >= 20:
        res = place_order("Buy", 20)
        tg(f"BUY PEPE 20 USDT -> {res.get('retMsg')} {res.get('retCode')}")
        return jsonify({"signal": sig, "order": res})
    if sig == "SELL":
        # verkoop 20 USDT waarde
        res = place_order("Sell", 20)
        tg(f"SELL PEPE -> {res.get('retMsg')} {res.get('retCode')}")
        return jsonify({"signal": sig, "order": res})
    return jsonify({"status": "geen signaal", "signal": sig, "usdt": usdt, "acct": acct})

@app.route("/webhook", methods=["POST"])
def webhook():
    print("Webhook:", request.get_json(force=True, silent=True), flush=True)
    return check()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
