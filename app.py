def bybit_get(path, params):
    timestamp = str(int(time.time() * 1000))
    query = urllib.parse.urlencode(sorted(params.items()))
    sign, recv_window = _sign(query, timestamp)
    headers = _headers(sign, recv_window, timestamp)
    for base in [WORKING_BASE_URL["url"]] + [b for b in BASE_URLS if b != WORKING_BASE_URL["url"]]:
        try:
            r = requests.get(base + path + "?" + query, headers=headers, timeout=15)
            try:
                data = r.json()
            except:
                continue
            # Als Bybit zegt: verkeerde regio / key niet gevonden, probeer andere base
            if data.get("retCode") in [10002, 10003, 10004, 10005]:
                continue
            WORKING_BASE_URL["url"] = base
            return data
        except:
            continue
    return {"retCode": -1, "retMsg": "ERROR"}

def bybit_post(path, body):
    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(body, separators=(",", ":"))
    sign, recv_window = _sign(body_str, timestamp)
    headers = _headers(sign, recv_window, timestamp)
    for base in [WORKING_BASE_URL["url"]] + [b for b in BASE_URLS if b != WORKING_BASE_URL["url"]]:
        try:
            r = requests.post(base + path, headers=headers, data=body_str, timeout=15)
            try:
                data = r.json()
            except:
                continue
            if data.get("retCode") in [10002, 10003, 10004, 10005]:
                continue
            WORKING_BASE_URL["url"] = base
            return data
        except:
            continue
    return {"retCode": -1, "retMsg": "ERROR"}
