import functions_framework

@functions_framework.http
def run_pulse(request):
    import os, json, time, calendar as _cal
    import urllib.request
    from datetime import date, datetime
    import requests

    GREEN_INSTANCE = os.environ.get("GREEN_API_INSTANCE")
    GREEN_TOKEN    = os.environ.get("GREEN_API_TOKEN")
    GREEN_GROUP    = os.environ.get("GREEN_API_GROUP")
    GREEN_HOST     = os.environ.get("GREEN_API_HOST")
    LIMETRAY_TOKEN = os.environ.get("LIMETRAY_AUTH_TOKEN")  # optional; sent only if set

    def _load_config():
        """Outlet IDs, names and labels come from REPORT_CONFIG (inline JSON) or
        REPORT_CONFIG_FILE (path to a JSON file). See config.example.json."""
        raw = os.environ.get("REPORT_CONFIG")
        if not raw:
            with open(os.environ.get("REPORT_CONFIG_FILE", "config.json"), encoding="utf-8") as fh:
                raw = fh.read()
        return json.loads(raw)

    CONFIG = _load_config()

    API_BASE    = os.environ.get("LIMETRAY_API_BASE")
    CFG         = CONFIG["hourly_pulse"]
    BRAND       = CFG["brand_name"]
    OUTLET_ID   = int(CFG["outlet_id"])
    OUTLET_NAME = CFG["outlet_name"]
    AGGREGATORS = ["POS","Talabat","Noon","Careem","Keeta","Deliveroo","Smiles"]
    SOURCE_MAP  = {"talabat":"Talabat","noon":"Noon","careem":"Careem","pos":"POS",
                   "keeta":"Keeta","deliveroo":"Deliveroo","smiles":"Smiles"}

    def normalize_source(s):
        return SOURCE_MAP.get((s or "").strip().lower(), "Other")

    def _ms(d, h=0, m=0, s=0):
        dt = datetime(d.year, d.month, d.day, h, m, s)
        return int((_cal.timegm(dt.timetuple()) - 4*3600) * 1000)

    def _post(url, payload, label, retries=3, delay=10):
        for attempt in range(1, retries+1):
            try:
                hdrs = {"Content-Type":"application/json"}
                if LIMETRAY_TOKEN: hdrs["x-auth-token"] = LIMETRAY_TOKEN
                r = requests.post(url, json=payload, headers=hdrs, timeout=20)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                print(f"⚠️  {label} attempt {attempt}: {e}")
                if attempt < retries: time.sleep(delay)
        return None

    def _payload(target, page_size=2000):
        return {"selectedDate":[_ms(target,0,0,0),_ms(target,23,59,59)],
                "isBusinessDay":0,"outletIds":[OUTLET_ID],
                "orderType":[],"orderSource":[],"isPreOrder":[],
                "status":[],"paymentMode":[],"deliverySource":[],
                "user":[],"server":[],"deliveryBoy":[],"pageSize":page_size}

    report_date = date.today()

    data = _post(f"{API_BASE}/order/count", _payload(report_date,1), "count")
    total_count = (data.get("result",[{}]) or [{}])[0].get("totalcount",0) if data else 0

    data = _post(f"{API_BASE}/order", _payload(report_date), "orders")
    orders = (data.get("result") or []) if data else []

    by_source = {src: {"revenue":0.0,"orders":0} for src in AGGREGATORS}
    total_revenue = 0.0
    total_orders  = 0

    for o in orders:
        if o.get("paymentstatus") != "PAID": continue
        rev = float(o.get("revenue") or 0)
        src = normalize_source(o.get("ordersource"))
        total_revenue += rev; total_orders += 1
        if src in by_source:
            by_source[src]["revenue"] += rev; by_source[src]["orders"] += 1

    now_uae = datetime.utcfromtimestamp(time.time() + 4*3600)

    lines = [
        f"🛍️ *{BRAND} — {OUTLET_NAME} Pulse*",
        f"🕐 {now_uae.strftime('%A, %d %b %Y — %I:%M %p')} UAE",
        "─"*30, "💰 *TODAY SO FAR*",
        f"   Revenue:  AED {total_revenue:,.0f}",
        f"   Orders:   {total_orders} paid  ({total_count} total)",
        "─"*30, "📲 *BY AGGREGATOR*",
    ]
    for src in AGGREGATORS:
        gs = by_source[src]
        lines.append(f"   {src}: AED {gs['revenue']:,.0f}  ({gs['orders']} orders)")
    lines += ["─"*30, "_Sent automatically — hourly pulse_"]
    message = "\n".join(lines)

    url  = f"https://{GREEN_HOST}/waInstance{GREEN_INSTANCE}/sendMessage/{GREEN_TOKEN}"
    body = json.dumps({"chatId":GREEN_GROUP,"message":message}).encode()
    req  = urllib.request.Request(url, data=body, headers={"Content-Type":"application/json"})
    try:
        urllib.request.urlopen(req, timeout=30)
        print("✅ Pulse sent.")
        return ("✅ Pulse sent.", 200)
    except Exception as e:
        print(f"❌ Pulse failed: {e}")
        return (f"❌ Pulse failed: {e}", 500)
