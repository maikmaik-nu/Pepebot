import os
from pybit.unified_trading import HTTP
from flask import Flask
import requests

app = Flask(__name__)
session = HTTP(testnet=False, api_key=os.getenv("BYBIT_API_KEY"), api_secret=os.getenv("BYBIT_API_SECRET"), domain="【entity-bybit¦canonical_name=Bybit】.eu")

@app.route("/")
def home():
    bal = session.get_wallet_balance(accountType="SPOT", coin="USDT")
    usdt = float(bal["result"]["list"][0]["coin"][0]["walletBalance"])
    kl = requests.get("https://api.bybit.com/v5/market/kline", params={"category":"spot","symbol":"PEPEUSDT","interval":"15","limit":50}).json()["result"]["list"][::-1]
    closes = [float(k[4]) for k in kl]
    # UTBOT 4/14 simpel
    atr = sum([max(float(kl[i][2])-float(kl[i][3]), abs(float(kl[i][2])-closes[i-1])) for i in range(1, len(kl))][-14:])/14
    sig = "BUY" if closes[-1] > closes[-2] else "SELL" if closes[-1] < closes[-2] else "HOLD"
    return f"BAL={usdt} SIG={sig} PRICE={closes[-1]} ATR={atr}"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
