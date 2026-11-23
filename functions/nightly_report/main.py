import functions_framework

@functions_framework.http
def run_report(request):
    import os, json, time, calendar as _cal
    import urllib.request
    from datetime import date, datetime, timedelta
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
    CFG         = CONFIG["nightly_report"]
    BRAND       = CFG["brand_name"]
    OUTLETS     = {str(k): v for k, v in CFG["outlets"].items()}   # {"<outlet id>": "<branch name>"}
    OUTLET_IDS  = [int(k) for k in OUTLETS]
    BRANCHES    = list(OUTLETS.values())
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
                "isBusinessDay":0,"outletIds":OUTLET_IDS,
                "orderType":[],"orderSource":[],"isPreOrder":[],
                "status":[],"paymentMode":[],"deliverySource":[],
                "user":[],"server":[],"deliveryBoy":[],"pageSize":page_size}

    report_date = date.today()

    data = _post(f"{API_BASE}/order/count", _payload(report_date,1), "count")
    total_count = (data.get("result",[{}]) or [{}])[0].get("totalcount",0) if data else 0

    data = _post(f"{API_BASE}/order", _payload(report_date), "orders")
    orders = (data.get("result") or []) if data else []

    # Pre-seed every branch and aggregator with zeros
    by_outlet = {name: {"revenue":0.0,"orders":0,
                         "by_source":{src:{"revenue":0.0,"orders":0} for src in AGGREGATORS}}
                 for name in BRANCHES}
    by_source = {src: {"revenue":0.0,"orders":0} for src in AGGREGATORS}
    total_revenue = 0.0
    total_orders  = 0

    for o in orders:
        if o.get("paymentstatus") != "PAID": continue
        outlet = OUTLETS.get(str(o.get("outletid","")), o.get("outletname","Other"))
        rev    = float(o.get("revenue") or 0)
        src    = normalize_source(o.get("ordersource"))
        total_revenue += rev; total_orders += 1
        if outlet not in by_outlet:
            by_outlet[outlet] = {"revenue":0.0,"orders":0,
                                  "by_source":{s:{"revenue":0.0,"orders":0} for s in AGGREGATORS}}
        by_outlet[outlet]["revenue"] += rev; by_outlet[outlet]["orders"] += 1
        if src not in by_outlet[outlet]["by_source"]:
            by_outlet[outlet]["by_source"][src] = {"revenue":0.0,"orders":0}
        by_outlet[outlet]["by_source"][src]["revenue"] += rev
        by_outlet[outlet]["by_source"][src]["orders"]  += 1
        if src in by_source:
            by_source[src]["revenue"] += rev; by_source[src]["orders"] += 1

    if total_orders == 0 and total_count == 0:
        msg = "⚠️ No order data — WhatsApp skipped."
        print(msg); return (msg, 200)

    # Month-to-date aggregate (sum day-by-day; the analytics API times out on
    # whole-month single-shot queries once order volume gets into the tens of
    # thousands, so we fetch one day at a time and accumulate).
    month_start = report_date.replace(day=1)
    mtd_revenue = 0.0
    mtd_orders  = 0
    mtd_by_outlet = {name: {"revenue":0.0,"orders":0,
                             "by_source":{src:{"revenue":0.0,"orders":0} for src in AGGREGATORS}}
                      for name in BRANCHES}
    day = month_start
    while day <= report_date:
        if day == report_date:
            day_orders = orders  # reuse what we already fetched for today
        else:
            d = _post(f"{API_BASE}/order", _payload(day, 3000), f"mtd {day}", retries=2, delay=5)
            day_orders = (d.get("result") or []) if d else []
        for o in day_orders:
            if o.get("paymentstatus") != "PAID": continue
            rev = float(o.get("revenue") or 0)
            src = normalize_source(o.get("ordersource"))
            mtd_revenue += rev
            mtd_orders  += 1
            outlet = OUTLETS.get(str(o.get("outletid","")), o.get("outletname","Other"))
            if outlet not in mtd_by_outlet:
                mtd_by_outlet[outlet] = {"revenue":0.0,"orders":0,
                                          "by_source":{s:{"revenue":0.0,"orders":0} for s in AGGREGATORS}}
            mtd_by_outlet[outlet]["revenue"] += rev
            mtd_by_outlet[outlet]["orders"]  += 1
            if src not in mtd_by_outlet[outlet]["by_source"]:
                mtd_by_outlet[outlet]["by_source"][src] = {"revenue":0.0,"orders":0}
            mtd_by_outlet[outlet]["by_source"][src]["revenue"] += rev
            mtd_by_outlet[outlet]["by_source"][src]["orders"]  += 1
        day += timedelta(days=1)

    lines = [
        f"🍽️ *{BRAND} — Daily Sales*",
        f"📅 {report_date.strftime('%A, %d %b %Y')}",
        "─"*30, "💰 *TOTAL*",
        f"   Revenue:  AED {total_revenue:,.0f}",
        f"   Orders:   {total_orders} paid  ({total_count} total)",
        "─"*30, "📆 *MONTH-TO-DATE*",
        f"   {month_start.strftime('%d %b')}–{report_date.strftime('%d %b')}",
        f"   Revenue:  AED {mtd_revenue:,.0f}",
        f"   Orders:   {mtd_orders} paid",
        "─"*30, "🏪 *BY BRANCH*",
    ]
    for name in sorted(by_outlet, key=lambda x: by_outlet[x]["revenue"], reverse=True):
        stats = by_outlet[name]
        lines.append(f"\n   *{name}*  —  AED {stats['revenue']:,.0f}  ({stats['orders']} orders)")
        for src in AGGREGATORS:
            bs = stats["by_source"].get(src, {"revenue":0.0,"orders":0})
            lines.append(f"      {src}: AED {bs['revenue']:,.0f}  ({bs['orders']})")
    lines += ["\n"+"─"*30, "📲 *BY AGGREGATOR (ALL BRANCHES)*"]
    for src in AGGREGATORS:
        gs = by_source[src]
        lines.append(f"   {src}: AED {gs['revenue']:,.0f}  ({gs['orders']} orders)")
    lines += ["─"*30, "_Sent automatically_"]
    message = "\n".join(lines)

    mtd_lines = [
        f"📆 *{BRAND} — Month-to-Date by Branch & Aggregator*",
        f"🗓️ {month_start.strftime('%d %b')}–{report_date.strftime('%d %b %Y')}",
        "─"*30,
    ]
    for name in sorted(mtd_by_outlet, key=lambda x: mtd_by_outlet[x]["revenue"], reverse=True):
        stats = mtd_by_outlet[name]
        mtd_lines.append(f"\n   *{name}*  —  AED {stats['revenue']:,.0f}  ({stats['orders']} orders)")
        for src in AGGREGATORS:
            bs = stats["by_source"].get(src, {"revenue":0.0,"orders":0})
            mtd_lines.append(f"      {src}: AED {bs['revenue']:,.0f}  ({bs['orders']})")
    mtd_lines += ["\n"+"─"*30, "📲 *BY AGGREGATOR (ALL BRANCHES)*"]
    for src in AGGREGATORS:
        tot_rev = sum(mtd_by_outlet[n]["by_source"].get(src, {"revenue":0.0})["revenue"] for n in mtd_by_outlet)
        tot_ord = sum(mtd_by_outlet[n]["by_source"].get(src, {"orders":0})["orders"] for n in mtd_by_outlet)
        mtd_lines.append(f"   {src}: AED {tot_rev:,.0f}  ({tot_ord} orders)")
    mtd_lines += [
        "─"*30, "💰 *TOTAL MTD*",
        f"   AED {mtd_revenue:,.0f}  ({mtd_orders} orders)",
        "─"*30, "_Sent automatically_",
    ]
    mtd_message = "\n".join(mtd_lines)

    def _send(msg, label):
        url  = f"https://{GREEN_HOST}/waInstance{GREEN_INSTANCE}/sendMessage/{GREEN_TOKEN}"
        body = json.dumps({"chatId":GREEN_GROUP,"message":msg}).encode()
        req  = urllib.request.Request(url, data=body, headers={"Content-Type":"application/json"})
        try:
            urllib.request.urlopen(req, timeout=30)
            print(f"✅ {label} sent.")
            return True
        except Exception as e:
            print(f"❌ {label} failed: {e}")
            return False

    ok1 = _send(message, "Daily report")
    ok2 = _send(mtd_message, "MTD-by-branch report")

    if ok1 and ok2:
        return ("✅ Both reports sent.", 200)
    elif ok1:
        return ("⚠️ Daily sent, MTD-by-branch failed.", 500)
    elif ok2:
        return ("⚠️ MTD-by-branch sent, daily failed.", 500)
    else:
        return ("❌ Both reports failed.", 500)
