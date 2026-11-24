import os, time, hmac, hashlib, threading, requests, json, traceback
from flask import Flask, jsonify

app = Flask(__name__)

BASE_URL = "https://api.bybit.eu"
API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()

SYMBOL = "PEPEUSDT"
BASE_COIN = "PEPE"
QUOTE_COIN = "USDT"

TRADE_USDT = 20.0
MIN_USDT = 10.0
TAKE_PROFIT_PCT = 5.0
STOP_LOSS_PCT = -3.0
CHECK_EVERY_SEC = 60

RECV_WINDOW = "5000"

STATUS = {
    "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    "last_check": "nog niet",
    "usdt": 0,
    "usdc": 0,
    "pepe": 0,
    "pepe_price": 0,
    "in_position": False,
    "entry_price": 0,
    "pnl_pct": 0,
    "last_action": "opstarten...",
    "last_error": None,
    "total_trades": 0,
    "total_buys": 0,
    "total_sells": 0,
}

_bot_started = False
_bot_lock = threading.Lock()

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def sign(timestamp, api_key, recv_window, payload_str):
    param_str = str(timestamp) + api_key + str(recv_window) + payload_str
    return hmac.new(API_SECRET.encode("utf-8"), param_str.encode("utf-8"), hashlib.sha256).hexdigest()

def bybit_get(path, params):
    timestamp = int(time.time() * 1000)
    query = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
    signature = sign(timestamp, API_KEY, RECV_WINDOW, query)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": str(timestamp),
        "X-BAPI-RECV-WINDOW": str(RECV_WINDOW),
        "Content-Type": "application/json"
    }
    r = requests.get(f"{BASE_URL}{path}", params=params, headers=headers, timeout=15)
    log(f"GET {path} {params} -> {r.status_code} {r.text[:600]}")
    return r.json()

def bybit_post(path, body_dict):
    timestamp = int(time.time() * 1000)
    body_str = json.dumps(body_dict, separators=(',', ':'))
    signature = sign(timestamp, API_KEY, RECV_WINDOW, body_str)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-SIGN": signature,
        "X-BAPI-TIMESTAMP": str(timestamp),
        "X-BAPI-RECV-WINDOW": str(RECV_WINDOW),
        "Content-Type": "application/json"
    }
    r = requests.post(f"{BASE_URL}{path}", data=body_str, headers=headers, timeout=15)
    log(f"POST {path} {body_str} -> {r.status_code} {r.text[:600]}")
    return r.json()

def get_balances():
    usdt = 0.0
    usdc = 0.0
    pepe = 0.0
    for coin_name in ["USDT", "USDC", "PEPE"]:
        try:
            data = bybit_get("/v5/account/wallet-balance", {"accountType": "SPOT", "coin": coin_name})
            if data.get("retCode") != 0:
                log(f"SALDO FOUT {coin_name}: {data}")
                if coin_name == "USDT":
                    return 0, 0, 0, data
                continue
            coins = data["result"]["list"][0]["coin"]
            for c in coins:
                val = float(c.get("walletBalance", 0) or 0)
                if c["coin"] == "USDT":
                    usdt = val
                elif c["coin"] == "USDC":
                    usdc = val
                elif c["coin"] == "PEPE":
                    pepe = val
        except Exception as e:
            log(f"SALDO EXCEPTION {coin_name}: {e}")
            if coin_name == "USDT":
                return 0, 0, 0, {"error": str(e)}
   
