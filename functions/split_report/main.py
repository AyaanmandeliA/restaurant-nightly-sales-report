import functions_framework


@functions_framework.http
def run_report(request):
    import os, json, time, calendar as _cal
    import urllib.request
    from datetime import date, datetime
    import requests

    GREEN_INSTANCE = os.environ.get("GREEN_API_INSTANCE")
    GREEN_TOKEN    = os.environ.get("GREEN_API_TOKEN")
    GREEN_GROUP    = os.environ.get("GREEN_API_GROUP")
    GREEN_HOST     = os.environ.get("GREEN_API_HOST")
    AUTH_TOKEN = os.environ.get("LIMETRAY_AUTH_TOKEN")

    def _load_config():
        """Outlet IDs, names and labels come from REPORT_CONFIG (inline JSON) or
        REPORT_CONFIG_FILE (path to a JSON file). See config.example.json."""
        raw = os.environ.get("REPORT_CONFIG")
        if not raw:
            with open(os.environ.get("REPORT_CONFIG_FILE", "config.json"), encoding="utf-8") as fh:
                raw = fh.read()
        return json.loads(raw)

    CONFIG = _load_config()

    API_BASE = os.environ.get("LIMETRAY_API_BASE")

    CFG           = CONFIG["split_report"]
    BRAND         = CFG["brand_name"]
    DININ_LABEL   = CFG["dine_in_label"]
    DININ_ID  = str(CFG["dine_in_outlet_id"])
    CLOUD_NAMES   = {str(k): v for k, v in CFG["cloud_kitchens"].items()}  # {"<outlet id>": "<name>"}
    CLOUD_IDS = list(CLOUD_NAMES)
    AGGREGATORS   = ["POS","Talabat","Noon","Careem","Keeta","Deliveroo","Smiles"]
    SOURCE_MAP    = {"talabat":"Talabat","noon":"Noon","careem":"Careem","pos":"POS",
                     "keeta":"Keeta","deliveroo":"Deliveroo","smiles":"Smiles"}

    def normalize_source(s):
        return SOURCE_MAP.get((s or "").strip().lower(), "Other")

    def _ms(d, h=0, m=0, s=0):
        dt = datetime(d.year, d.month, d.day, h, m, s)
        return int((_cal.timegm(dt.timetuple()) - 4 * 3600) * 1000)

    def _post(url, payload, label, token=None, retries=3, delay=10):
        hdrs = {"Content-Type": "application/json"}
        if token:
            hdrs["x-auth-token"] = token
        for attempt in range(1, retries + 1):
            try:
                r = requests.post(url, json=payload, headers=hdrs, timeout=30)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                print(f"⚠️  {label} attempt {attempt}: {e}")
                if attempt < retries:
                    time.sleep(delay)
        return None

    def _payload(outlet_ids, start_d, end_d, page_size=2000):
        return {
            "selectedDate":  [_ms(start_d, 0, 0, 0), _ms(end_d, 23, 59, 59)],
            "isBusinessDay": 0, "outletIds": outlet_ids,
            "orderType": [], "orderSource": [], "isPreOrder": [],
            "status": [], "paymentMode": [], "deliverySource": [],
            "user": [], "server": [], "deliveryBoy": [], "pageSize": page_size,
        }

    def _blank_agg():
        return {src: {"revenue": 0.0, "orders": 0} for src in AGGREGATORS}

    report_date = date.today()
    all_ids = [int(DININ_ID)] + [int(i) for i in CLOUD_IDS]
    cloud_id_set = set(CLOUD_IDS)

    def _tally(orders_list):
        """Bucket a list of orders into dine-in / aggregator / cloud-kitchen totals."""
        dinin  = {"revenue": 0.0, "orders": 0, "by_src": _blank_agg()}
        aggr   = {"revenue": 0.0, "orders": 0, "by_src": _blank_agg()}
        cloud  = {CLOUD_NAMES.get(oid, oid): {"revenue": 0.0, "orders": 0, "by_src": _blank_agg()}
                  for oid in CLOUD_IDS}
        for o in orders_list:
            if o.get("paymentstatus") != "PAID":
                continue
            oid = str(o.get("outletid", ""))
            src = normalize_source(o.get("ordersource") or "")
            rev = float(o.get("revenue") or 0)
            if oid in cloud_id_set:
                name = CLOUD_NAMES.get(oid, o.get("outletname") or oid)
                if name not in cloud:
                    cloud[name] = {"revenue": 0.0, "orders": 0, "by_src": _blank_agg()}
                cloud[name]["revenue"] += rev
                cloud[name]["orders"]  += 1
                if src in cloud[name]["by_src"]:
                    cloud[name]["by_src"][src]["revenue"] += rev
                    cloud[name]["by_src"][src]["orders"]  += 1
            elif oid == DININ_ID:
                bucket = dinin if src == "POS" else aggr
                bucket["revenue"] += rev
                bucket["orders"]  += 1
                if src in bucket["by_src"]:
                    bucket["by_src"][src]["revenue"] += rev
                    bucket["by_src"][src]["orders"]  += 1
        return dinin, aggr, cloud

    data   = _post(f"{API_BASE}/order", _payload(all_ids, report_date, report_date),
                   "orders", token=AUTH_TOKEN)
    orders = (data.get("result") or []) if data else []

    s_dinin, s_aggr, cloud_branches = _tally(orders)

    s_cloud_total_rev = sum(b["revenue"] for b in cloud_branches.values())
    s_cloud_total_ord = sum(b["orders"]  for b in cloud_branches.values())
    total_rev = s_dinin["revenue"] + s_aggr["revenue"] + s_cloud_total_rev
    total_ord = s_dinin["orders"]  + s_aggr["orders"]  + s_cloud_total_ord

    if total_ord == 0:
        print("No orders today — skipping WhatsApp.")
        return ("No orders", 200)

    # Merge dine-in + aggregators into one combined dine-in outlet block
    s_ibn_rev = s_dinin["revenue"] + s_aggr["revenue"]
    s_ibn_ord = s_dinin["orders"]  + s_aggr["orders"]
    s_ibn_by_src = {
        src: {
            "revenue": s_dinin["by_src"][src]["revenue"] + s_aggr["by_src"][src]["revenue"],
            "orders":  s_dinin["by_src"][src]["orders"]  + s_aggr["by_src"][src]["orders"],
        }
        for src in AGGREGATORS
    }

    def _src_lines(by_src):
        return [f"      {src}: AED {by_src[src]['revenue']:,.0f}  ({by_src[src]['orders']})"
                for src in AGGREGATORS]

    day_str = report_date.strftime("%A, %d %b %Y")
    D = "─" * 30

    msg_lines = [
        f"🏠 *{BRAND} — Daily Sales*",
        f"📅 {day_str}",
        D,
        f"💰 *TOTAL:  AED {total_rev:,.0f}  ({total_ord} orders)*",
        D,
    ]

    # Month-to-date total (this brand's order volume is low enough to fetch the
    # whole month in one shot, unlike the multi-outlet report which goes day-by-day).
    month_start = report_date.replace(day=1)
    mtd_count_data = _post(f"{API_BASE}/order/count", _payload(all_ids, month_start, report_date, 1),
                            "MTD count", token=AUTH_TOKEN)
    mtd_count = (mtd_count_data.get("result", [{}]) or [{}])[0].get("totalcount", 0) if mtd_count_data else 0

    if month_start == report_date:
        mtd_orders_raw = orders  # today's fetch already covers the whole "month" (1st of month)
    else:
        mtd_data = _post(f"{API_BASE}/order", _payload(all_ids, month_start, report_date, max(mtd_count + 200, 200)),
                          "MTD orders", token=AUTH_TOKEN)
        mtd_orders_raw = (mtd_data.get("result") or []) if mtd_data else []

    mtd_dinin, mtd_aggr, mtd_cloud = _tally(mtd_orders_raw)
    mtd_cloud_total_rev = sum(b["revenue"] for b in mtd_cloud.values())
    mtd_cloud_total_ord = sum(b["orders"]  for b in mtd_cloud.values())
    mtd_total_rev = mtd_dinin["revenue"] + mtd_aggr["revenue"] + mtd_cloud_total_rev
    mtd_total_ord = mtd_dinin["orders"]  + mtd_aggr["orders"]  + mtd_cloud_total_ord

    mtd_ibn_rev = mtd_dinin["revenue"] + mtd_aggr["revenue"]
    mtd_ibn_ord = mtd_dinin["orders"]  + mtd_aggr["orders"]
    mtd_ibn_by_src = {
        src: {
            "revenue": mtd_dinin["by_src"][src]["revenue"] + mtd_aggr["by_src"][src]["revenue"],
            "orders":  mtd_dinin["by_src"][src]["orders"]  + mtd_aggr["by_src"][src]["orders"],
        }
        for src in AGGREGATORS
    }

    msg_lines += [
        "📆 *MONTH-TO-DATE*",
        f"   Revenue:  AED {mtd_total_rev:,.0f}",
        f"   Orders:   {mtd_total_ord} paid",
        D,
    ]

    msg_lines += [
        f"🍽️ *1. {DININ_LABEL}*",
        f"   Revenue:  AED {s_ibn_rev:,.0f}",
        f"   Orders:   {s_ibn_ord}",
    ]
    msg_lines += _src_lines(s_ibn_by_src)
    msg_lines.append(D)

    msg_lines += [
        "🏭 *2. Cloud Kitchens*",
        f"   Revenue:  AED {s_cloud_total_rev:,.0f}",
        f"   Orders:   {s_cloud_total_ord}",
    ]
    for branch, stats in sorted(cloud_branches.items(), key=lambda x: x[1]["revenue"], reverse=True):
        msg_lines.append(f"\n   *{branch}*  —  AED {stats['revenue']:,.0f}  ({stats['orders']})")
        msg_lines += _src_lines(stats["by_src"])
    msg_lines += [D, "_Sent automatically_"]

    message = "\n".join(msg_lines)
    print(message)

    # Separate message: Month-to-Date by Branch & Aggregator
    mtd_lines = [
        f"📆 *{BRAND} — Month-to-Date by Branch & Aggregator*",
        f"🗓️ {month_start.strftime('%d %b')}–{report_date.strftime('%d %b %Y')}",
        D,
        f"🍽️ *1. {DININ_LABEL}*  —  AED {mtd_ibn_rev:,.0f}  ({mtd_ibn_ord} orders)",
    ]
    mtd_lines += _src_lines(mtd_ibn_by_src)
    mtd_lines.append(D)

    mtd_lines += [
        f"🏭 *2. Cloud Kitchens*  —  AED {mtd_cloud_total_rev:,.0f}  ({mtd_cloud_total_ord} orders)",
    ]
    for branch, stats in sorted(mtd_cloud.items(), key=lambda x: x[1]["revenue"], reverse=True):
        mtd_lines.append(f"\n   *{branch}*  —  AED {stats['revenue']:,.0f}  ({stats['orders']})")
        mtd_lines += _src_lines(stats["by_src"])

    mtd_lines += ["\n"+D, "📲 *BY AGGREGATOR (ALL)*"]
    for src in AGGREGATORS:
        tot_rev = mtd_ibn_by_src[src]["revenue"] + sum(mtd_cloud[b]["by_src"][src]["revenue"] for b in mtd_cloud)
        tot_ord = mtd_ibn_by_src[src]["orders"]  + sum(mtd_cloud[b]["by_src"][src]["orders"]  for b in mtd_cloud)
        mtd_lines.append(f"   {src}: AED {tot_rev:,.0f}  ({tot_ord} orders)")

    mtd_lines += [
        D, "💰 *TOTAL MTD*",
        f"   AED {mtd_total_rev:,.0f}  ({mtd_total_ord} orders)",
        D, "_Sent automatically_",
    ]
    mtd_message = "\n".join(mtd_lines)

    def _send(msg, label):
        url  = f"https://{GREEN_HOST}/waInstance{GREEN_INSTANCE}/sendMessage/{GREEN_TOKEN}"
        body = json.dumps({"chatId": GREEN_GROUP, "message": msg}).encode()
        req  = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
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
