import os, json, time, threading, hmac, hashlib, urllib.parse
from decimal import Decimal, ROUND_DOWN
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY", "").strip()
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "").strip()
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

SYMBOL = "PEPEUSDT"
CATEGORY = "spot"
INTERVAL = "15"
BASE_URL = "https://api.bybit.eu"
STATE_FILE = "state.json"

# Alleen UT Bot 4 / 14 - GEEN Supertrend
UT_KEY_VALUE = 4.0
UT_ATR_PERIOD = 14

STATE = {"in_position": False, "entry_price": 0.0, "qty": 0.0, "last_check": 0, "last_start_msg": 0, "last_error_msg": 0}
STATE_LOCK = threading.Lock()
_LOOP_STARTED = False
_CHECK_RUNNING = False

def log(m): print(m, flush=True)
log(f"KEY LOADED: {len(BYBIT_API_KEY)} chars, start={BYBIT_API_KEY[:4] if BYBIT_API_KEY else 'LEEG'}****")

def send_telegram(msg):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID: return
    try: requests.post("https://api.telegram.org/bot"+TELEGRAM_BOT_TOKEN+"/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e: log("Telegram error: "+str(e))

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE) as f: STATE.update(json.load(f))
    except: pass
def save_state():
    try:
        with STATE_LOCK: data=dict(STATE)
        with open(STATE_FILE,"w") as f: json.dump(data,f)
    except Exception as e: log("save_state: "+str(e))

def _sign(p, ts):
    rw="5000"; payload=ts+BYBIT_API_KEY+rw+p
    return hmac.new(BYBIT_API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest(), rw
def _headers(s,rw,ts):
    return {"X-BAPI-API-KEY":BYBIT_API_KEY,"X-BAPI-SIGN":s,"X-BAPI-SIGN-TYPE":"2","X-BAPI-TIMESTAMP":ts,"X-BAPI-RECV-WINDOW":rw,"Content-Type":"application/json"}

def bybit_get(path, params):
    ts=str(int(time.time()*1000)); q=urllib.parse.urlencode(sorted(params.items())); s,rw=_sign(q,ts)
    try:
        r=requests.get(BASE_URL+path+"?"+q, headers=_headers(s,rw,ts), timeout=15); return r.json()
    except Exception as e: return {"retCode":-1,"retMsg":str(e)}
def bybit_post(path, body):
    ts=str(int(time.time()*1000)); bs=json.dumps(body,separators=(",",":")); s,rw=_sign(bs,ts)
    try:
        r=requests.post(BASE_URL+path, headers=_headers(s,rw,ts), data=bs, timeout=15); return r.json()
    except Exception as e: return {"retCode":-1,"retMsg":str(e)}

def get_balances():
    usdt=0.0; pepe=0.0; ok=False; last="ERROR"
    for at in ["UNIFIED"]:
        d=bybit_get("/v5/account/wallet-balance", {"accountType": at})
        log(f"Saldo {at}: retCode={d.get('retCode')} retMsg={d.get('retMsg')}")
        if d.get("retCode")==10005: last="10005"; continue
        if d.get("retCode")==10003: last="10003"; continue
        if d.get("retCode")!=0: last="ERROR"; continue
        lst=d.get("result",{}).get("list",[])
        if not lst: continue
        ok=True
        for c in lst[0].get("coin",[]):
            if c.get("coin")=="USDT": usdt+=float(c.get("walletBalance",0) or 0)
            if c.get("coin")=="PEPE": pepe+=float(c.get("walletBalance",0) or 0)
    if not ok: return None,None,last
    return usdt,pepe,"UNIFIED"

def get_klines():
    try:
        r=requests.get(BASE_URL+"/v5/market/kline", params={"category":CATEGORY,"symbol":SYMBOL,"interval":INTERVAL,"limit":200}, timeout=15)
        return list(reversed(r.json().get("result",{}).get("list",[])))
    except Exception as e: log("Klines: "+str(e)); return []
def get_price():
    try:
        r=requests.get(BASE_URL+"/v5/market/tickers", params={"category":CATEGORY,"symbol":SYMBOL}, timeout=10)
        lst=r.json().get("result",{}).get("list",[])
        if lst: return float(lst[0].get("lastPrice",0))
    except: pass
    return 0
def get_qty_step():
    try:
        r=requests.get(BASE_URL+"/v5/market/instruments-info", params={"category":CATEGORY,"symbol":SYMBOL}, timeout=10)
        lst=r.json().get("result",{}).get("list",[])
        if lst:
            f=lst[0].get("lotSizeFilter",{}); return float(f.get("qtyStep",1)), float(f.get("minOrderQty",1))
    except: pass
    return 1.0,1.0
def round_qty(qty, step):
    return float((Decimal(str(qty))/Decimal(str(step))).to_integral_value(rounding=ROUND_DOWN)*Decimal(str(step)))

def calc_atr(klines, period):
    if len(klines)<period+1: return 0
    trs=[]
    for i in range(1,len(klines)):
        h=float(klines[i][2]); l=float(klines[i][3]); pc=float(klines[i-1][4])
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs[-period:])/period

def ut_bot_signal(klines):
    if len(klines)<50: return None
    atr=calc_atr(klines, UT_ATR_PERIOD)
    if atr==0: return None
    trail=None; pos=0; last_signal=None
    for k in klines:
        close=float(k[4])
        if trail is None: trail=close
        if close>trail and pos<=0:
            trail=close-UT_KEY_VALUE*atr
            if pos!=1: last_signal="buy"; pos=1
        elif close<trail and pos>=0:
            trail=close+UT_KEY_VALUE*atr
            if pos!=-1: last_signal="sell"; pos=-1
        else:
            if pos==1: trail=max(trail, close-UT_KEY_VALUE*atr)
            elif pos==-1: trail=min(trail, close+UT_KEY_VALUE*atr)
    return last_signal, atr, trail

def place_order(side, qty):
    step,minq=get_qty_step(); qty=round_qty(qty,step)
    if qty<minq: log(f"Order te klein: {qty}"); return None
    body={"category":CATEGORY,"symbol":SYMBOL,"side":side,"orderType":"Market","qty":str(qty)}
    log("Plaats order: "+str(body)); d=bybit_post("/v5/order/create", body); log("Order result: "+str(d))
    return d if d.get("retCode")==0 else None

def do_buy(usdt, price):
    if usdt<5: log("Te weinig USDT"); return False
    qty=usdt/price
    if place_order("Buy", qty):
        with STATE_LOCK: STATE.update({"in_position":True,"entry_price":price,"qty":qty})
        save_state(); send_telegram(f"GEKOCHT PEPEUSDT\nPrijs: {price}\nInleg: {usdt:.2f} USDT (alles)\nRente-op-rente AAN"); log("KOOP uitgevoerd"); return True
    return False

def do_sell(pepe_qty, price, usdt):
    if pepe_qty<=0: log("Niks te verkopen"); return False
    if place_order("Sell", pepe_qty):
        with STATE_LOCK: STATE.update({"in_position":False,"qty":0,"entry_price":0})
        save_state(); send_telegram(f"VERKOCHT PEPEUSDT\nPrijs: {price}\nSaldo nu: {usdt:.2f} USDT"); log("VERKOOP uitgevoerd"); return True
    return False

def check_bot():
    global _CHECK_RUNNING
    if _CHECK_RUNNING: return
    _CHECK_RUNNING=True
    try:
        with STATE_LOCK:
            if time.time()-STATE["last_check"]<60: return
            STATE["last_check"]=time.time()
        log("=== Bot check ===")
        usdt,pepe,acc=get_balances()
        if usdt is None:
            with STATE_LOCK: STATE["last_check"]=0
            if acc=="10003":
                with STATE_LOCK:
                    if time.time()-STATE["last_error_msg"]>600:
                        STATE["last_error_msg"]=time.time(); send_telegram("Pepebot: saldo niet leesbaar (10003). Bybit.eu: Read + Spot Trade + Wallet AAN, IP UIT.")
            if acc=="10005":
                with STATE_LOCK:
                    if time.time()-STATE["last_error_msg"]>600:
                        STATE["last_error_msg"]=time.time(); send_telegram("Pepebot: saldo niet leesbaar (10005). Key heeft geen toestemming voor UNIFIED wallet-balance.")
            return
        log(f"Saldo: {usdt} USDT | PEPE: {pepe}")
        klines=get_klines()
        if len(klines)<50: log("Te weinig klines"); return
        price=get_price() or float(klines[-1][4])
        sig,atr,trail=ut_bot_signal(klines)
        log(f"Prijs {price} | UT signaal {sig} | ATR {atr}")
        with STATE_LOCK:
            in_pos=STATE["in_position"]
            if pepe>0 and not in_pos: STATE["in_position"]=True; STATE["qty"]=pepe; in_pos=True
            if pepe==0 and in_pos: STATE["in_position"]=False; STATE["qty"]=0; in_pos=False
        if sig=="buy" and not in_pos:
            do_buy(usdt, price); return
        if sig=="sell" and in_pos:
            do_sell(pepe, price, usdt); return
        log(f"Geen actie. In positie: {in_pos}")
        with STATE_LOCK:
            if time.time()-STATE["last_start_msg"]>3600:
                STATE["last_start_msg"]=time.time(); need=True
            else: need=False
        if need: send_telegram(f"Bot live: Alleen UT Bot 4/14 - Rente-op-rente AAN - Saldo: {usdt:.2f} USDT")
        save_state()
    finally: _CHECK_RUNNING=False

def loop():
    check_bot()
    while True:
        time.sleep(900)
        try: check_bot()
        except Exception as e: log("Loop: "+str(e))

def start_loop_once():
    global _LOOP_STARTED
    if _LOOP_STARTED: return
    _LOOP_STARTED=True; load_state(); threading.Thread(target=loop, daemon=True).start()

@app.route("/")
def home():
    with STATE_LOCK: st=dict(STATE)
    return jsonify({"status":"running","symbol":SYMBOL,"in_position":st["in_position"],"key_start":BYBIT_API_KEY[:4]+"****" if BYBIT_API_KEY else "LEEG"})
@app.route("/check")
def manual():
    threading.Thread(target=check_bot, daemon=True).start(); return jsonify({"ok":True})
@app.route("/test-key")
def test_key():
    usdt,pepe,acc=get_balances(); return jsonify({"key_start":BYBIT_API_KEY[:4]+"****" if BYBIT_API_KEY else "LEEG","usdt":usdt,"pepe":pepe,"status":acc})
@app.route("/webhook", methods=["POST"])
def webhook():
    data=request.get_json(force=True, silent=True) or {}
    txt=json.dumps(data).lower(); log("Webhook: "+str(data))
    action=data.get("action","").lower() if isinstance(data,dict) else ""
    if "buy" in txt or action=="buy":
        usdt,pepe,acc=get_balances()
        if usdt is not None:
            price=get_price()
            with STATE_LOCK: in_pos=STATE["in_position"]
            if not in_pos: do_buy(usdt, price)
        return jsonify({"ok":True,"action":"buy"})
    if "sell" in txt or action=="sell":
        usdt,pepe,acc=get_balances()
        if pepe is not None:
            price=get_price(); do_sell(pepe, price, usdt)
        return jsonify({"ok":True,"action":"sell"})
    threading.Thread(target=check_bot, daemon=True).start(); return jsonify({"ok":True})

start_loop_once()
if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",10000)))
