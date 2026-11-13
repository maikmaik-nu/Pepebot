import os
import math
import time
import json
import threading
import requests
from flask import Flask, request, jsonify
from pybit.unified_trading import HTTP

app = Flask(__name__)

# ===== CONFIG - alles uit Render Environment =====
SYMBOOL = "PEPEUSDT"
BYBIT_API_KEY = os.environ.get("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.environ.get("BYBIT_API_SECRET", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

TRADE_PCT = float(os.environ.get("TRADE_PCT", "0.98"))
TAKE_PROFIT_PCT = float(os.environ.get("TAKE_PROFIT_PCT", "0.015"))
STOP_LOSS_PCT = float(os.environ.get("STOP_LOSS_PCT", "0.03"))  # NIEUW: verkoop bij -3%
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "60"))
COOLDOWN_SEC = int(os.environ.get("COOLDOWN_SEC", "300"))  # NIEUW: 5 min wachten na verkoop
STATE_FILE = "bot_state.json"

session = HTTP(testnet=False, api_key=BYBIT_API_KEY, api_secret=BYBIT_API_SECRET)

# ===== STATE - blijft bewaard bij herstart =====
def laad_state():
    default = {
        "in_positie": False,
        "koop_prijs": 0.0,
        "aantal": 0.0,
        "laatste_actie": "gestart",
        "laatste_ping": time.time(),
        "actief": True,  # NIEUW: voor /stop en /start
        "laatste_verkoop": 0
    }
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                default.update(data)
                print(f"State hersteld: {default}")
    except Exception as e:
        print(f"State laden fout: {e}")
    return default

def sla_state_op():
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(bot_state, f)
    except Exception as e:
        print(f"State opslaan fout: {e}")

bot_state = laad_state()
trade_lock = threading.Lock()

# ===== TELEGRAM =====
def send_telegram(msg):
    try:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            print("Telegram niet ingesteld, msg:", msg)
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=10)
    except Exception as e:
        print("Telegram fout:", e)

# ===== 【entity-BYBIT¦canonical_name=BYBIT】 HELPERS =====
def get_prijs():
    r = session.get_tickers(category="spot", symbol=SYMBOOL)
    return float(r["result"]["list"][0]["lastPrice"])

def get_balances():
    # 1x ophalen i.p.v. 2x, scheelt API calls
    r = session.get_wallet_balance(accountType="UNIFIED")
    coins = {c["coin"]: float(c["walletBalance"]) for c in r["result"]["list"][0]["coin"]}
    return coins.get("USDT", 0.0), coins.get("PEPE", 0.0)

def koop():
    with trade_lock:
        if bot_state["in_positie"]:
            return
        usdt, _ = get_balances()
        bedrag = usdt * TRADE_PCT
        if bedrag < 5:
            send_telegram(f"⚠️ Te weinig USDT om te kopen: {usdt:.2f}")
            return
        prijs = get_prijs()
        aantal = math.floor(bedrag / prijs)
        if aantal <= 0:
            return
        try:
            session.place_order(category="spot", symbol=SYMBOOL, side="Buy", orderType="Market", qty=str(aantal))
        except Exception as e:
            send_telegram(f"❌ Koop mislukt: {e}")
            return
        # Prijs na order ophalen = dichter bij echte fill-prijs
        time.sleep(2)
        echte_prijs = get_prijs()
        bot_state.update({
            "in_positie": True,
            "koop_prijs": echte_prijs,
            "aantal": float(aantal),
            "laatste_actie": f"GEKOCHT {aantal} @ {echte_prijs}"
        })
        sla_state_op()
        send_telegram(f"🟢 GEKOCHT\n{aantal} PEPE @ {echte_prijs}\nUSDT gebruikt: {bedrag:.2f}")

def verkoop(reden="TP"):
    with trade_lock:
        if not bot_state["in_positie"]:
            return
        _, pepe = get_balances()
        if pepe <= 0:
            bot_state["in_positie"] = False
            sla_state_op()
            return
        try:
            session.place_order(category="spot", symbol=SYMBOOL, side="Sell", orderType="Market", qty=str(int(pepe)))
        except Exception as e:
            send_telegram(f"❌ Verkoop mislukt: {e}")
            return
        time.sleep(2)
        prijs = get_prijs()
        koop_prijs = bot_state["koop_prijs"]
        winst_pct = (prijs - koop_prijs) / koop_prijs * 100 if koop_prijs else 0
        emoji = "🔴" if reden == "TP" else "🛑"
        label = "VERKOCHT (Take Profit)" if reden == "TP" else "VERKOCHT (Stop-Loss)"
        bot_state.update({
            "in_positie": False,
            "koop_prijs": 0.0,
            "aantal": 0.0,
            "laatste_actie": f"{label} @ {prijs} winst {winst_pct:.2f}%",
            "laatste_verkoop": time.time()
        })
        sla_state_op()
        send_telegram(f"{emoji} {label}\n{int(pepe)} PEPE @ {prijs}\nWinst: {winst_pct:.2f}%")

# ===== HERSTEL BIJ OPSTART =====
def herstel_positie_bij_opstart():
    """Als Render herstart is en we hebben nog PEPE, dan zaten we in een positie."""
    try:
        _, pepe = get_balances()
        if pepe > 0 and not bot_state["in_positie"]:
            # We weten de oude koopprijs niet meer, gebruik huidige prijs als schatting
            # en laat de gebruiker het corrigeren via /setkoop
            prijs = get_prijs()
            bot_state.update({
                "in_positie": True,
                "koop_prijs": prijs,
                "aantal": float(pepe),
                "laatste_actie": f"HERSTELD na herstart: {int(pepe)} PEPE @ ~{prijs} (check koopprijs!)"
            })
            sla_state_op()
            send_telegram(f"♻️ Herstart gedetecteerd\nJe hebt nog {int(pepe)} PEPE.\nIk heb de koopprijs op huidige prijs gezet ({prijs}).\nStuur /setkoop <prijs> als je de echte koopprijs weet.")
        elif pepe == 0 and bot_state["in_positie"]:
            bot_state.update({"in_positie": False, "koop_prijs": 0.0, "aantal": 0.0, "laatste_actie": "Hersteld: geen PEPE meer, positie gereset"})
            sla_state_op()
    except Exception as e:
        print(f"Herstel fout: {e}")

# ===== TRADING LOOP =====
def trading_loop():
    time.sleep(5)
    herstel_positie_bij_opstart()
    send_telegram("🤖 Bot gestart - NIEUWE VERSIE\n✅ Stop-loss AAN\n✅ Herstel na herstart AAN\n✅ /stop en /start werken echt\nStuur /ping om te testen")
    while True:
        try:
            if not bot_state.get("actief", True):
                print("Bot is gepauzeerd via /stop, wachten...")
                time.sleep(CHECK_INTERVAL)
                continue

            prijs = get_prijs()

            if not bot_state["in_positie"]:
                # Cooldown na verkoop
                if time.time() - bot_state.get("laatste_verkoop", 0) < COOLDOWN_SEC:
                    print(f"Cooldown... nog {int(COOLDOWN_SEC - (time.time() - bot_state['laatste_verkoop']))}s")
                else:
                    koop()
            else:
                koop_prijs = bot_state["koop_prijs"]
                if koop_prijs > 0:
                    # Take Profit
                    if prijs >= koop_prijs * (1 + TAKE_PROFIT_PCT):
                        verkoop(reden="TP")
                    # Stop-Loss - NIEUW
                    elif prijs <= koop_prijs * (1 - STOP_LOSS_PCT):
                        verkoop(reden="SL")
                    else:
                        print(f"Wachten... prijs {prijs} koop {koop_prijs} | TP: {koop_prijs * (1 + TAKE_PROFIT_PCT):.8f} | SL: {koop_prijs * (1 - STOP_LOSS_PCT):.8f}")
            time.sleep(CHECK_INTERVAL)
        except Exception as e:
            print("Loop fout:", e)
            time.sleep(30)

# ===== FLASK ROUTES =====
@app.route("/")
def home():
    return "Pepebot is LIVE ✅ (v2)", 200

@app.route("/ping")
def ping():
    bot_state["laatste_ping"] = time.time()
    sla_state_op()
    return jsonify({"status": "ok", "bot": "live", "symbool": SYMBOOL, "in_positie": bot_state["in_positie"], "actief": bot_state.get("actief", True), "laatste_actie": bot_state["laatste_actie"]}), 200

@app.route("/health")
def health():
    return jsonify({"ok": True, "in_positie": bot_state["in_positie"], "actief": bot_state.get("actief", True)}), 200

@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    data = request.get_json(force=True, silent=True) or {}
    msg = data.get("message", {})
    text = msg.get("text", "").strip().lower()
    chat_id = str(msg.get("chat", {}).get("id", ""))

    # BEVEILIGING: alleen jouw chat mag commando's sturen
    if chat_id != str(TELEGRAM_CHAT_ID):
        print(f"Geblokkeerde Telegram poging van chat_id: {chat_id}")
        return jsonify({"ok": True}), 200

    if text == "/ping":
        send_telegram(f"🏓 PONG - Ik ben LIVE ✅\nSymbool: {SYMBOOL}\nActief: {bot_state.get('actief', True)}\nIn positie: {bot_state['in_positie']}\nLaatste actie: {bot_state['laatste_actie']}")
    elif text == "/status":
        try:
            prijs = get_prijs()
            usdt, pepe = get_balances()
            send_telegram(f"📊 STATUS\nPrijs: {prijs}\nUSDT: {usdt:.2f}\nPEPE: {int(pepe)}\nActief: {bot_state.get('actief', True)}\nIn positie: {bot_state['in_positie']}\nKoopprijs: {bot_state['koop_prijs']}\nLaatste actie: {bot_state['laatste_actie']}")
        except Exception as e:
            send_telegram(f"Status fout: {e}")
    elif text == "/stop":
        bot_state["actief"] = False
        sla_state_op()
        send_telegram("⏸️ Bot GEPAUZEERD.\nIk koop/verkoop niks meer tot je /start stuurt.\nOpen posities blijven gewoon open.")
    elif text == "/start":
        bot_state["actief"] = True
        sla_state_op()
        send_telegram("▶️ Bot weer ACTIEF.\nIk ga weer handelen.")
    elif text.startswith("/setkoop"):
        # Voorbeeld: /setkoop 0.00001234
        try:
            nieuwe_prijs = float(text.split()[1])
            bot_state["koop_prijs"] = nieuwe_prijs
            sla_state_op()
            send_telegram(f"✅ Koopprijs aangepast naar {nieuwe_prijs}")
        except:
            send_telegram("Gebruik: /setkoop 0.00001234")
    return jsonify({"ok": True}), 200

# Start trading in background, niet 2x in debug
if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
    threading.Thread(target=trading_loop, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
