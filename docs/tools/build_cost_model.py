# Regenerates AWS_COST_MODEL.xlsx: python docs/tools/build_cost_model.py AWS_COST_MODEL.xlsx  (requires openpyxl)
import sys
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

OUT = sys.argv[1]
F = "Arial"
BLUE = Font(name=F, color="0000FF")
BOLD = Font(name=F, bold=True)
H = Font(name=F, bold=True, color="FFFFFF")
HFILL = PatternFill("solid", fgColor="12243D")
YELLOW = PatternFill("solid", fgColor="FFFF00")
NORMAL = Font(name=F)
GREEN = Font(name=F, color="008000")
RED = Font(name=F, bold=True, color="B91C1C")
thin = Side(style="thin", color="D0D7E2")
BOX = Border(top=thin, bottom=thin, left=thin, right=thin)
USD = '$#,##0.00;($#,##0.00);"-"'

wb = Workbook()


def header(ws, row, values, widths):
    for i, v in enumerate(values, 1):
        c = ws.cell(row=row, column=i, value=v)
        c.font, c.fill, c.alignment, c.border = H, HFILL, Alignment(wrap_text=True, vertical="center"), BOX
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + i)].width = w


# README
ws = wb.active
ws.title = "README"
ws.column_dimensions["A"].width = 130
lines = [
    ("CivicTrack - AWS Cost Model (ESTIMATE ONLY)", Font(name=F, bold=True, size=14)),
    ("Prepared 2026-11-09 for stack civictrack-dev, region ap-south-1 (Mumbai).", NORMAL),
    ("", NORMAL),
    ("THIS WORKBOOK IS AN ESTIMATE. It is NOT actual AWS billing.", RED),
    ("Actual charges must be read from AWS Billing / Cost Explorer (sheet 'Actual Billing'). Nothing here was retrieved from billing.", NORMAL),
    ("", NORMAL),
    ("How to use", BOLD),
    ("1. 'Unit Prices': blue-on-yellow cells are ASSUMED public list prices. Verify each at https://calculator.aws/ for ap-south-1 before relying on totals.", NORMAL),
    ("2. 'Usage Drivers': blue cells are usage assumptions per scenario. Edit them to match expected load.", NORMAL),
    ("3. 'Monthly Cost': black cells are formulas that recalculate from the two input sheets. Free-tier allowances are NOT deducted (conservative).", NORMAL),
    ("", NORMAL),
    ("Colour legend: blue text = editable input; black = formula; green = value linked from another sheet; yellow fill = assumption to verify.", NORMAL),
    ("Example input row: 'Lambda - price per 1M requests' = 0.20 (USD).", NORMAL),
    ("", NORMAL),
    ("Model notes", BOLD),
    ("- GET /complaints performs a full-table DynamoDB Scan for every role, and the SLA sweep scans the table 2,880 times a month, so read cost grows with table size x list loads (audit finding F4).", NORMAL),
    ("- EventBridge scheduled rules carry no charge. Cognito Lite tier: first 10,000 MAU free (verify the tier configured on the pool).", NORMAL),
    ("- The stack has no EC2, NAT Gateway, load balancer or other always-on resource.", NORMAL),
]
for i, (text, font) in enumerate(lines, 1):
    c = ws.cell(row=i, column=1, value=text)
    c.font, c.alignment = font, Alignment(wrap_text=True)

# Unit Prices
up = wb.create_sheet("Unit Prices")
header(up, 1, ["Item", "Price (USD)", "Unit", "Source / note"], [48, 16, 22, 80])
prices = [
    ("lambda_req", "Lambda - price per 1M requests", 0.20, "per 1M requests"),
    ("lambda_gbs", "Lambda - price per GB-second (x86)", 0.0000166667, "per GB-second"),
    ("apigw", "API Gateway REST - price per 1M requests", 3.50, "per 1M requests"),
    ("ddb_wru", "DynamoDB on-demand - per 1M write request units", 0.7125, "per 1M WRU"),
    ("ddb_rru", "DynamoDB on-demand - per 1M read request units", 0.1425, "per 1M RRU"),
    ("ddb_storage", "DynamoDB standard storage", 0.285, "per GB-month"),
    ("s3_storage", "S3 Standard storage", 0.025, "per GB-month"),
    ("s3_put", "S3 PUT requests", 0.005, "per 1,000 requests"),
    ("s3_get", "S3 GET/HEAD requests", 0.0004, "per 1,000 requests"),
    ("ses", "SES outbound email", 0.10, "per 1,000 emails"),
    ("cw_ingest", "CloudWatch Logs ingestion", 0.50, "per GB"),
    ("cw_store", "CloudWatch Logs storage", 0.03, "per GB-month"),
    ("dto", "Data transfer out to internet", 0.1093, "per GB"),
    ("cognito_free", "Cognito MAU included free (Lite tier)", 10000, "MAU"),
    ("cognito_mau", "Cognito Lite - price per MAU above free tier", 0.0055, "per MAU"),
]
P = {}
for i, (key, label, value, unit) in enumerate(prices, 2):
    up.cell(row=i, column=1, value=label).font = NORMAL
    c = up.cell(row=i, column=2, value=value)
    c.font, c.fill = BLUE, YELLOW
    c.number_format = "#,##0" if key == "cognito_free" else ("0.0000000000" if value < 0.001 else "$#,##0.0000")
    up.cell(row=i, column=3, value=unit).font = NORMAL
    up.cell(row=i, column=4, value="Assumed public list price for ap-south-1 - VERIFY at https://calculator.aws/ (not retrieved from AWS)").font = NORMAL
    P[key] = f"'Unit Prices'!$B${i}"

# Usage Drivers
ud = wb.create_sheet("Usage Drivers")
header(ud, 1, ["Driver", "Current (dev)", "Pilot ward", "City scale", "Note"], [50, 15, 15, 15, 62])
drivers = [
    ("complaints", "New complaints per month", (10, 1000, 50000), "Live table held 6 complaints on 2026-11-09"),
    ("table_items", "Complaints stored in table (avg over month)", (10, 6000, 300000), "Grows with retention; no TTL configured"),
    ("item_kb", "Average item size incl. history (KB)", (3, 3, 3), "history list adds about 0.2 KB per action"),
    ("list_loads", "GET /complaints list loads per month (all roles)", (300, 15000, 400000), "Each load scans the whole table"),
    ("api_per_complaint", "Other API calls per complaint lifecycle", (15, 15, 15), "create, upload URLs, detail, assign, start, resolve, reopen/close"),
    ("sla_runs", "SLA sweeps per month", (2880, 2880, 2880), "rate(15 minutes) x 30 days"),
    ("lambda_ms", "Average Lambda duration (ms)", (300, 400, 1500), "Scan latency grows with table size"),
    ("lambda_mb", "Lambda memory (MB)", (512, 512, 512), "template.yaml Globals"),
    ("writes_per_complaint", "DynamoDB write units per complaint", (12, 12, 12), "put + ~6 updates; items over 1 KB cost several WRU"),
    ("photos", "Photos per complaint", (1.5, 1.5, 1.5), "citizen evidence + officer before/after"),
    ("photo_mb", "Average photo size (MB)", (1.5, 1.5, 1.5), "Maximum enforced: 10 MB"),
    ("photo_views", "Photo views per complaint", (5, 5, 5), "presigned GET from browser"),
    ("retained_months", "Months of photos retained (average)", (3, 6, 12), "No S3 expiry configured"),
    ("emails", "Emails per complaint", (7, 7, 7), "create x2, assign x2, start, resolve x2 (+SLA)"),
    ("log_gb", "CloudWatch log ingestion (GB/month)", (0.05, 1, 25), "Retention currently 'never expire'"),
    ("log_stored_gb", "CloudWatch log storage (GB, accumulated)", (0.5, 6, 150), ""),
    ("mau", "Monthly active users", (20, 2000, 60000), ""),
    ("open_share", "Share of stored complaints that are open", (0.5, 0.1, 0.05), "duplicate checks scan open items with coordinates"),
    ("dup_checks", "Duplicate checks per new complaint", (2, 2, 2), "pre-submit /complaints/similar + check during create"),
    ("analytics_loads", "Admin analytics loads per month (full scan)", (30, 600, 3000), "server-side aggregation"),
    ("feed_loads", "Admin notification-feed loads per month (full scan)", (100, 3000, 20000), "feed derived from complaint history"),
    ("gsi_factor", "Write multiplier for citizenSub GSI (projection ALL)", (2, 2, 2), "each write also updates the index"),
]
D = {}
for i, (key, label, vals, note) in enumerate(drivers, 2):
    ud.cell(row=i, column=1, value=label).font = NORMAL
    for j, v in enumerate(vals):
        c = ud.cell(row=i, column=2 + j, value=v)
        c.font, c.number_format = BLUE, "#,##0.0#"
    ud.cell(row=i, column=5, value=note).font = NORMAL
    D[key] = i


def d(key, col):
    return f"'Usage Drivers'!{col}${D[key]}"


def invocations(c):
    return f"({d('list_loads', c)}+{d('complaints', c)}*{d('api_per_complaint', c)}+{d('sla_runs', c)})"


# Monthly Cost
mc = wb.create_sheet("Monthly Cost")
header(mc, 1, ["Service / line item", "Current (dev) USD", "Pilot ward USD", "City scale USD", "Formula basis"], [46, 18, 18, 18, 70])
items = [
    ("Lambda requests", lambda c: f"={invocations(c)}/1000000*{P['lambda_req']}", "(list loads + API calls + SLA sweeps) x price"),
    ("Lambda compute", lambda c: f"={invocations(c)}*{d('lambda_ms', c)}/1000*{d('lambda_mb', c)}/1024*{P['lambda_gbs']}", "invocations x seconds x GB x price"),
    ("API Gateway requests", lambda c: f"=({d('list_loads', c)}+{d('complaints', c)}*{d('api_per_complaint', c)})/1000000*{P['apigw']}", "API calls only (SLA sweep does not use API Gateway)"),
    ("DynamoDB reads - list scans", lambda c: f"={d('list_loads', c)}*{d('table_items', c)}*{d('item_kb', c)}/4/1000000*{P['ddb_rru']}", "strongly consistent full scan: items x KB/4 RRU per load"),
    ("DynamoDB reads - SLA sweeps", lambda c: f"={d('sla_runs', c)}*{d('table_items', c)}*{d('item_kb', c)}/4*0.5/1000000*{P['ddb_rru']}", "eventually consistent full scan (filter applied after read)"),
    ("DynamoDB reads - duplicate checks", lambda c: f"={d('complaints', c)}*{d('dup_checks', c)}*{d('table_items', c)}*{d('open_share', c)}*{d('item_kb', c)}/4*0.5/1000000*{P['ddb_rru']}", "scan of open items per check (filtered after read; conservative: assumes only open items are read)"),
    ("DynamoDB reads - analytics & admin feed", lambda c: f"=({d('analytics_loads', c)}+{d('feed_loads', c)})*{d('table_items', c)}*{d('item_kb', c)}/4*0.5/1000000*{P['ddb_rru']}", "full scans for aggregation and notification feed"),
    ("DynamoDB writes (incl. GSI)", lambda c: f"={d('complaints', c)}*{d('writes_per_complaint', c)}*{d('gsi_factor', c)}/1000000*{P['ddb_wru']}", "complaints x WRU per lifecycle x GSI multiplier"),
    ("DynamoDB storage (incl. GSI copy)", lambda c: f"={d('table_items', c)}*{d('item_kb', c)}*{d('gsi_factor', c)}/1048576*{P['ddb_storage']}", "items x KB x GSI multiplier, converted to GB"),
    ("S3 storage", lambda c: f"={d('complaints', c)}*{d('photos', c)}*{d('photo_mb', c)}/1024*{d('retained_months', c)}*{P['s3_storage']}", "photos retained x GB"),
    ("S3 requests", lambda c: f"={d('complaints', c)}*{d('photos', c)}*(2*{P['s3_put']}+({d('photo_views', c)}+1)*{P['s3_get']})/1000", "PUT + HEAD + GET views"),
    ("Data transfer out (photo views)", lambda c: f"={d('complaints', c)}*{d('photos', c)}*{d('photo_views', c)}*{d('photo_mb', c)}/1024*{P['dto']}", "photo bytes served to browsers"),
    ("SES email", lambda c: f"={d('complaints', c)}*{d('emails', c)}/1000*{P['ses']}", "emails x price"),
    ("CloudWatch Logs", lambda c: f"={d('log_gb', c)}*{P['cw_ingest']}+{d('log_stored_gb', c)}*{P['cw_store']}", "ingestion + storage"),
    ("Cognito", lambda c: f"=MAX(0,{d('mau', c)}-{P['cognito_free']})*{P['cognito_mau']}", "MAU above free tier"),
    ("EventBridge scheduled rule", lambda c: "=0", "Scheduled rules are not charged"),
]
cols = ["B", "C", "D"]
first = 2
for i, (label, fn, basis) in enumerate(items, first):
    mc.cell(row=i, column=1, value=label).font = NORMAL
    for j, c in enumerate(cols):
        cell = mc.cell(row=i, column=2 + j, value=fn(c))
        cell.font, cell.number_format = NORMAL, USD
    mc.cell(row=i, column=5, value=basis).font = NORMAL
last = first + len(items) - 1
t = last + 1
mc.cell(row=t, column=1, value="Estimated monthly total (before free tier)").font = BOLD
for j, c in enumerate(cols):
    cell = mc.cell(row=t, column=2 + j, value=f"=SUM({c}{first}:{c}{last})")
    cell.font, cell.number_format = BOLD, USD
mc.cell(row=t + 1, column=1, value="Cost per new complaint").font = NORMAL
for j, c in enumerate(cols):
    cell = mc.cell(row=t + 1, column=2 + j, value=f"=IF({d('complaints', c)}=0,0,{c}{t}/{d('complaints', c)})")
    cell.font, cell.number_format = NORMAL, "$#,##0.0000"
mc.cell(row=t + 2, column=1, value="Share from DynamoDB scans (list, SLA, duplicate, analytics/feed)").font = NORMAL
for j, c in enumerate(cols):
    cell = mc.cell(row=t + 2, column=2 + j, value=f"=IF({c}{t}=0,0,SUM({c}5:{c}8)/{c}{t})")
    cell.font, cell.number_format = NORMAL, "0.0%"
mc.cell(row=t + 3, column=1, value="Estimated annual total (12 x monthly)").font = BOLD
for j, c in enumerate(cols):
    cell = mc.cell(row=t + 3, column=2 + j, value=f"={c}{t}*12")
    cell.font, cell.number_format = BOLD, USD
mc.cell(row=t + 5, column=1, value="ESTIMATE - not actual billing. Verify unit prices before use.").font = RED

# Optimisations
op = wb.create_sheet("Optimisations")
header(op, 1, ["Proposal", "Effect", "Billing impact", "Needs owner approval?"], [46, 60, 42, 24])
rows = [
    ("Add GSIs on citizenSub and assignedOfficerSub; Query instead of Scan", "Citizen/officer list reads only their own items", "Cuts scan RRU; adds GSI storage and write cost", "Yes (table change + deploy)"),
    ("SLA sweep via sparse GSI on open complaints", "Sweep reads open items only", "Removes 2,880 full scans per month", "Yes"),
    ("CloudWatch log retention 30 days", "Stops indefinite log/PII retention", "Lowers storage cost", "Yes (one CLI command)"),
    ("S3 lifecycle: abort incomplete multipart uploads (in template)", "Housekeeping", "Negligible", "Yes (deploy)"),
    ("DynamoDB point-in-time recovery", "35-day restore window", "Adds a storage-sized monthly charge", "Yes (billing change)"),
    ("Lambda memory 512 to 256 MB after measuring", "Same work at lower GB-s if not CPU bound", "Up to about 50% lower compute", "Yes (deploy)"),
    ("SES production access", "Deliver to any citizen address", "None at this volume", "Yes (AWS request)"),
]
for i, r in enumerate(rows, 2):
    for j, v in enumerate(r, 1):
        c = op.cell(row=i, column=j, value=v)
        c.font, c.alignment = NORMAL, Alignment(wrap_text=True, vertical="top")

# Actual Billing
ab = wb.create_sheet("Actual Billing")
header(ab, 1, ["Month", "Actual AWS charge (USD) - enter from Billing console", "Estimate (Current scenario)", "Difference"], [14, 48, 28, 18])
for i, m in enumerate(["2026-09", "2026-10", "2026-11"], 2):
    ab.cell(row=i, column=1, value=m).font = NORMAL
    c = ab.cell(row=i, column=2)
    c.fill, c.font, c.number_format = YELLOW, BLUE, USD
    e = ab.cell(row=i, column=3, value=f"='Monthly Cost'!B{t}")
    e.font, e.number_format = GREEN, USD
    df = ab.cell(row=i, column=4, value=f'=IF(B{i}="","",B{i}-C{i})')
    df.font, df.number_format = NORMAL, USD
ab.cell(row=6, column=1, value="Actual billing was NOT retrieved. Fill column B from AWS Billing > Bills, or Cost Explorer (Cost Explorer API requests are charged).").font = RED

wb.save(OUT)
print("saved", OUT, "total row", t)
