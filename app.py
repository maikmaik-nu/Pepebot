def get_balances():
    balances = {}

    # Probeer eerst UNIFIED, daarna SPOT - 【entity-Bybit¦canonical_name=Bybit】 EU zet Spot & Margin onder UNIFIED
    for acc_type in ["UNIFIED", "SPOT"]:
        res = bybit_request("GET", "/v5/account/wallet-balance", {"accountType": acc_type})
        print(f"Saldo check {acc_type}: retCode={res.get('retCode')} retMsg={res.get('retMsg')}")
        try:
            for acc in res.get("result", {}).get("list", []):
                for c in acc.get("coin", []):
                    # walletBalance + available, neem de hoogste die we vinden
                    val = float(c.get("walletBalance", 0) or 0)
                    if val > 0:
                        balances[c["coin"]] = val
            if balances:
                break
        except Exception as e:
            print(f"Parse saldo error {acc_type}: {e}")

    print(f"Balances gevonden: {balances}")
    return balances
