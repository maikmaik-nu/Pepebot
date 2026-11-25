import os
import time
import hmac
import hashlib
import requests

# Vul in via Render > Environment
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE = "https://api.bybit.com"

SYMBOL = "PEPEUSDT"
CATEGORY = "spot"

# Alleen met deze coins traden
CASH_COINS = ["USDC", "USDT"]

def public_kline(interval, limit=200):
    """Publieke kline, GEEN start/end -> dit was je bug."""
    url = f"{BASE}/v5/market/kline"
    params = {
        "category": CATEGORY,
        "symbol": SYMBOL,
        "interval": str(interval),  # 15, 60, D
        "limit": limit,
    }
    r = requests.get(url, params=params, timeout=10)
    data = r.json()
    print(f"KLINE {interval} -> {r.status_code} :: {data.get('retMsg')}")
    lst = data.get("result", {}).get("list", [])
    return lst

def signed_get(path, params=None):
    """Voor saldo, wel signed."""
    params = params or {}
    params["api_key"] = API_KEY
    params["timestamp"] = str(int(time.time() * 1000))
    params["recv_window"] = "5000"
    query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sign = hmac.new(API_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
    url = f"{BASE}{path}?{query}&sign={sign}"
    r = requests.get(url, timeout=10)
    return r.json()

def get_balances():
    data = signed_get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
    print(f"BALANCE RAW :: {data}")
    coins = {}
    try:
        lst = data["result"]["list"][0]["coin"]
        for c in lst:
            coins[c["coin"]] = float(c.get("walletBalance", 0))
    except Exception as e:
        print(f"Saldo parse error: {e}")
    return coins

def utbot_signal(candles):
    """Simpele UTBot: alleen als we genoeg candles hebben."""
    if len(candles) < 50:
        return "HOLD", "te weinig candles"
    # candles zijn [timestamp, open, high, low, close, volume, ...]
    # Bybit geeft ze nieuw -> oud, draai om naar oud -> nieuw
    closes = [float(c[4]) for c in reversed(candles)]
    # Simpele trend: laatste close vs 20 candles terug
    if closes[-1] > closes[-20]:
        return "BUY", "uptrend"
    else:
        return "SELL", "downtrend"

def auto_check():
    print("=== AUTO CHECK ===")
    balances = get_balances()
    print(f"Saldo UNIFIED :: {balances}")

    cash = sum(balances.get(c, 0) for c in CASH_COINS)
    pepe = balances.get("PEPE", 0)
    print(f"CHECK :: cash={cash} USDC={balances.get('USDC',0)} USDT={balances.get('USDT',0)} PEPE={pepe}")

    # Klines ophalen
    k15 = public_kline("15")
    k60 = public_kline("60")
    kD = public_kline("D")

    if not k15 or not k60 or not kD:
        print("KLINE LEEG voor alle intervals")
        print("UTBOT: te weinig candles (0) -> HOLD")
        print(f"AUTO CHECK klaar: {{'status': 'hold', 'reason': 'te weinig candles'}}")
        return

    signal, reason = utbot_signal(k15)
    print(f"UTBOT: {signal} - {reason} | candles 15m: {len(k15)}")
    print(f"AUTO CHECK klaar: {{'status': '{signal.lower()}', 'reason': '{reason}', 'cash': {cash}, 'pepe': {pepe}}}")

    # Hier komt pas je order-logica, alleen als signal != HOLD
    # Nu nog geen echte order, eerst kijken of candles binnenkomen

if __name__ == "__main__":
    auto_check()
