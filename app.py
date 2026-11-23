import os
import time
import hmac
import hashlib
import json
import requests

# ================= CONFIG =================
API_KEY = os.getenv("BYBIT_API_KEY", "")
API_SECRET = os.getenv("BYBIT_API_SECRET", "")
BASE = "https://api.bybit.eu"  # 【entity-Bybit¦canonical_name=Bybit】 EU, NIET .com

SYMBOL = "PEPEUSDT"
BASE_COIN = "PEPE"
QUOTE_COIN = "USDC"

TRADE_BEDRAG_USDC = 10.0   # hoeveel USDC per koop
WINST_PROCENT = 3.0        # verkoop bij +3%
VERLIES_PROCENT = 2.0      # verkoop bij -2%
WACHT_SECONDEN = 60
MIN_SALDO_OM_TE_HANDELEN = 11.0  # moet iets meer zijn dan trade bedrag + fee

# onthoudt aankoopprijs in geheugen
aankoop_prijs = None
# ===========================================

def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

def sign_get(params_str):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    payload = ts + API_KEY + recv + params_str
    sign = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return ts, recv, sign

def sign_post(body_str):
    ts = str(int(time.time() * 1000))
    recv = "5000"
    payload = ts + API_KEY + recv + body_str
    sign = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return ts, recv, sign

def bybit_get(path, params_str=""):
    ts, recv, sign = sign_get(params_str)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json"
    }
    url = f"{BASE}{path}"
    if params_str:
        url += f"?{params_str}"
    r = requests.get(url, headers=headers, timeout=15)
    return r.json()

def bybit_post(path, body_dict):
    body_str = json.dumps(body_dict)
    ts, recv, sign = sign_post(body_str)
    headers = {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-RECV-WINDOW": recv,
        "X-BAPI-SIGN": sign,
        "Content-Type": "application/json"
    }
    r = requests.post(f"{BASE}{path}", headers=headers, data=body_str, timeout=15)
    return r.json()

# ---------- SALDO ----------
def get_saldo(coin):
    data = bybit_get("/v5/account/wallet-balance", f"accountType=UNIFIED&coin={coin}")
    if data.get("retCode") != 0:
        log(f"SALDO FOUT {coin}: {data}")
        return None
    try:
        coins = data["result"]["list"][0]["coin"]
        for c in coins:
            if c["coin"] == coin:
                return float(c.get("walletBalance", 0))
        return 0.0
    except Exception as e:
        log(f"Saldo parse fout {coin}: {e} | data: {data}")
        return None

def get_alle_saldi():
    saldo_usdc = get_saldo(QUOTE_COIN)
    saldo_pepe = get_saldo(BASE_COIN)
    return saldo_usdc, saldo_pepe

# ---------- PRIJS ----------
def get_prijs():
    try:
        r = requests.get(f"{BASE}/v5/market/tickers?category=spot&symbol={SYMBOL}", timeout=10)
        data = r.json()
        prijs = float(data["result"]["list"][0]["lastPrice"])
        log(f"PRIJS {SYMBOL} = {prijs}")
        return prijs
    except Exception as e:
        log(f"Prijs fout: {e}")
        return None

# ---------- ORDER ----------
def plaats_order(side, qty):
    # qty moet een string zijn, geen float met te veel decimalen
    # PEPE heeft veel decimalen, dus rond af op hele coins
    qty_str = str(int(qty))
    log(f"ORDER: {side} {qty_str} {BASE_COIN}")

    body = {
        "category": "spot",
        "symbol": SYMBOL,
        "side": side,  # Buy of Sell
        "orderType": "Market",
        "qty": qty_str,
    }
    data = bybit_post("/v5/order/create", body)
    log(f"ORDER RESULTAAT: {data}")
    if data.get("retCode") == 0:
        return True
    else:
        log(f"ORDER FOUT: {data.get('retMsg')}")
        return False

# ---------- STRATEGIE ----------
def ronde():
    global aankoop_prijs

    saldo_usdc, saldo_pepe = get_alle_saldi()
    prijs = get_prijs()

    if saldo_usdc is None or saldo_pepe is None or prijs is None:
        log("WACHT: saldo of prijs fout, doe niks deze ronde")
        return

    log(f"STATUS | USDC: {saldo_usdc} | PEPE: {saldo_pepe} | Prijs: {prijs} | Aankoopprijs: {aankoop_prijs}")

    # Waarde van je PEPE in USDC
    pepe_waarde_usdc = saldo_pepe * prijs

    # 1. GEEN PEPE, WEL USDC -> KOOP
    if pepe_waarde_usdc < 1.0 and saldo_usdc >= MIN_SALDO_OM_TE_HANDELEN:
        qty = TRADE_BEDRAG_USDC / prijs
        log(f"ACTIE: Geen PEPE, koop voor {TRADE_BEDRAG_USDC} USDC = {qty:.0f} PEPE")
        if plaats_order("Buy", qty):
            aankoop_prijs = prijs
            log(f"GEKOCHT op {prijs}, onthoud als aankoopprijs")
        return

    # 2. WEL PEPE, MAAR GEEN AANKOOPPRIJS BEKEND (bijv. na herstart) -> onthoud huidige prijs
    if pepe_waarde_usdc >= 1.0 and aankoop_prijs is None:
        aankoop_prijs = prijs
        log(f"Herstart: zet aankoopprijs op huidige prijs {prijs}, wachten op beweging")
        return

    # 3. WEL PEPE -> CHECK WINST / VERLIES
    if pepe_waarde_usdc >= 1.0 and aankoop_prijs is not None:
        verschil_procent = ((prijs - aankoop_prijs) / aankoop_prijs) * 100
        log(f"POSITIE: {verschil_procent:.2f}% t.o.v. aankoop")

        if verschil_procent >= WINST_PROCENT:
            log(f"ACTIE: +{verschil_procent:.2f}% winst, VERKOOP alles")
            if plaats_order("Sell", saldo_pepe):
                aankoop_prijs = None
                log("VERKOCHT met winst, klaar voor nieuwe ronde")
            return

        if verschil_procent <= -VERLIES_PROCENT:
            log(f"ACTIE: {verschil_procent:.2f}% verlies, VERKOOP alles om erger te voorkomen")
            if plaats_order("Sell", saldo_pepe):
                aankoop_prijs = None
                log("VERKOCHT met verlies, klaar voor nieuwe ronde")
            return

        log("ACTIE: Niks doen, wachten op +3% of -2%")
        return

    # 4. TE WEINIG USDC
    if saldo_usdc < MIN_SALDO_OM_TE_HANDELEN and pepe_waarde_usdc < 1.0:
        log(f"WACHT: Te weinig USDC ({saldo_usdc}) om te handelen, minimaal {MIN_SALDO_OM_TE_HANDELEN} nodig")

# ---------- MAIN ----------
def main():
    log("=== Pepebot-3 gestart op Bybit EU - UNIFIED ===")
    log(f"Key prefix: {API_KEY[:4]}**** als dit leeg is staat je key niet in Render")

    if not API_KEY or not API_SECRET:
        log("FOUT: BYBIT_API_KEY / BYBIT_API_SECRET ontbreken in Render Environment")
        return

    # Eerste test: werkt saldo?
    test = get_saldo(QUOTE_COIN)
    if test is None:
        log("START FOUT: Saldo werkt nog steeds niet. Bot start wel, maar handelt niet.")
        log("Dit is geen code-fout meer, dit is Bybit EU die je key blokkeert.")
    else:
        log(f"START OK: Saldo USDC = {test}")

    while True:
        try:
            ronde()
        except Exception as e:
            log(f"Loop fout: {e}")
        log(f"Wacht {WACHT_SECONDEN} sec...")
        time.sleep(WACHT_SECONDEN)

if __name__ == "__main__":
    main()
