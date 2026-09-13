#!/usr/bin/env python3
"""Step 3+4 — classify, age, and render the pipeline dashboard.

Pure offline render: reads ./cache/contacts.json and
./cache/zillow_status.json only. No network calls, no GHL writes of any
kind. Safe to re-run as many times as you want without touching either
API.

Usage:
    python3 scripts/build_report.py
"""
from __future__ import annotations

import csv
import datetime as dt
import html
import json
from pathlib import Path

import lib

AGE_BUCKETS = [
    (0, 30, "0-30"),
    (31, 60, "31-60"),
    (61, 90, "61-90"),
    (91, 120, "91-120"),
    (121, 150, "121-150"),
    (151, 180, "151-180"),
    (181, None, "180+"),
]

BUCKET_ORDER = ["ACTIVE", "PENDING", "EXITED", "UNKNOWN"]


def contact_name(c: dict) -> str:
    name = f"{c.get('firstName', '') or ''} {c.get('lastName', '') or ''}".strip()
    return name or c.get("name") or c.get("email") or c.get("id", "unknown")


def contact_address(c: dict) -> str:
    """Property address — stored in the contact record's standard address
    fields (address1/city/state/postalCode). Confirmed: this is the
    property address, not the agent's own address."""
    parts = [c.get("address1"), c.get("city"), c.get("state"), c.get("postalCode")]
    joined = ", ".join(p for p in parts if p)
    return joined or ""


def build_rows(contacts: list[dict], zillow_cache: dict, today: dt.date) -> tuple[list[dict], dict]:
    rows = []
    stats = {
        "no_link": 0,
        "non_zillow": 0,
        "no_zpid_match": 0,
        "unrecognized_status": {},
        "age_source_counts": {"offer_sent": 0, "date_added_to_cold_offer": 0, "contact.dateAdded": 0, "none": 0},
        "multi_opportunity": [],
    }

    for c in contacts:
        link = lib.get_custom_field(c, lib.FIELD["listing_link"])
        zpid = None
        status_detail = None

        if not link:
            stats["no_link"] += 1
            bucket = "UNKNOWN"
            status_detail = "no listing_link"
        elif not lib.is_zillow_homedetails(link):
            stats["non_zillow"] += 1
            bucket = "UNKNOWN"
            status_detail = "non-Zillow link (e.g. LoopNet)"
        else:
            zpid = lib.extract_zpid(link)
            if not zpid:
                stats["no_zpid_match"] += 1
                bucket = "UNKNOWN"
                status_detail = "zillow.com/homedetails link but no _zpid parsed"
            else:
                entry = zillow_cache.get(zpid)
                if entry is None:
                    bucket = "UNKNOWN"
                    status_detail = "not yet looked up in Apify"
                elif entry.get("error"):
                    bucket = "UNKNOWN"
                    status_detail = f"lookup failed: {entry['error']}"
                else:
                    home_status = entry.get("homeStatus")
                    bucket = lib.classify_status(home_status)
                    status_detail = home_status or "(empty homeStatus)"
                    if bucket.startswith("UNRECOGNIZED:"):
                        stats["unrecognized_status"][home_status] = stats["unrecognized_status"].get(home_status, 0) + 1
                        bucket = "UNKNOWN"
                        status_detail = f"unrecognized homeStatus: {home_status}"

        # age + fallback source tracking
        offer_sent_raw = lib.get_custom_field(c, lib.FIELD["offer_sent"])
        offer_sent_date = lib.parse_date_loose(offer_sent_raw)
        date_added_raw = lib.get_custom_field(c, lib.FIELD["date_added_to_cold_offer"])
        date_added_date = lib.parse_date_loose(date_added_raw)
        contact_added_date = lib.parse_date_loose(c.get("dateAdded"))

        if offer_sent_date:
            age_date, age_source = offer_sent_date, "offer_sent"
        elif date_added_date:
            age_date, age_source = date_added_date, "date_added_to_cold_offer"
        elif contact_added_date:
            age_date, age_source = contact_added_date, "contact.dateAdded"
        else:
            age_date, age_source = None, "none"

        age_days = (today - age_date).days if age_date else None
        stats["age_source_counts"][age_source] += 1

        asking_price = lib.to_number(lib.get_custom_field(c, lib.FIELD["asking_price"]))
        my_offer = lib.to_number(lib.get_custom_field(c, lib.FIELD["offer_made"]))
        if my_offer is None:
            my_offer = lib.to_number(lib.get_custom_field(c, lib.FIELD["offer_price"]))

        spread_pct = None
        if asking_price and my_offer is not None and asking_price != 0:
            spread_pct = (my_offer - asking_price) / asking_price * 100.0

        opp_count = len(c.get("opportunities") or [])
        if opp_count > 1:
            stats["multi_opportunity"].append({"contactId": c.get("id"), "name": contact_name(c), "count": opp_count})

        rows.append({
            "contactId": c.get("id"),
            "agent": contact_name(c),
            "address": contact_address(c),
            "listing_link": link or "",
            "zpid": zpid or "",
            "bucket": bucket,
            "status_detail": status_detail,
            "age_days": age_days,
            "age_source": age_source,
            "asking_price": asking_price,
            "my_offer": my_offer,
            "spread_pct": spread_pct,
            "mls_status": lib.get_custom_field(c, lib.FIELD["mls_status"]) or "",
            "mls_agent_name": lib.get_custom_field(c, lib.FIELD["mls_agent_name"]) or "",
            "opportunities_count": opp_count,
        })

    return rows, stats


def age_bucket_label(days: int) -> str:
    for lo, hi, label in AGE_BUCKETS:
        if hi is None:
            if days >= lo:
                return label
        elif lo <= days <= hi:
            return label
    return "?"


def fmt_money(v: float | None) -> str:
    if v is None:
        return "-"
    return f"${v:,.0f}"


def fmt_pct(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:+.1f}%"


def print_terminal_summary(rows: list[dict], stats: dict, total_contacts: int) -> None:
    counts = {b: 0 for b in BUCKET_ORDER}
    for r in rows:
        counts[r["bucket"]] = counts.get(r["bucket"], 0) + 1

    print("=" * 60)
    print("PIPELINE SUMMARY")
    print("=" * 60)
    for b in BUCKET_ORDER:
        print(f"  {b:<10} {counts.get(b, 0)}")
    print(f"  {'TOTAL':<10} {len(rows)}")

    print("\nACTIVE listings by age:")
    active_ages = [r["age_days"] for r in rows if r["bucket"] == "ACTIVE" and r["age_days"] is not None]
    hist = {label: 0 for _, _, label in AGE_BUCKETS}
    for d in active_ages:
        hist[age_bucket_label(d)] += 1
    max_count = max(hist.values(), default=0)
    bar_width = 40
    for _, _, label in AGE_BUCKETS:
        n = hist[label]
        bar_len = int((n / max_count) * bar_width) if max_count else 0
        print(f"  {label:>8} | {'#' * bar_len}{' ' * (bar_width - bar_len)} {n}")
    active_no_age = sum(1 for r in rows if r["bucket"] == "ACTIVE" and r["age_days"] is None)
    if active_no_age:
        print(f"  (+{active_no_age} ACTIVE rows with no age date at all)")

    print("\n15 oldest ACTIVE rows:")
    oldest = sorted(
        (r for r in rows if r["bucket"] == "ACTIVE" and r["age_days"] is not None),
        key=lambda r: -r["age_days"],
    )[:15]
    if not oldest:
        print("  (none)")
    for r in oldest:
        print(f"  {r['age_days']:>4}d  {r['agent']:<24} {r['address'] or '(no address on file)':<40} "
              f"asking={fmt_money(r['asking_price']):>12}  offer={fmt_money(r['my_offer']):>12}")

    print("\n" + "=" * 60)
    print("DATA QUALITY")
    print("=" * 60)
    total_rows = len(rows)
    offer_sent_n = stats["age_source_counts"]["offer_sent"]
    fallback_n = total_rows - offer_sent_n
    fallback_rate = (fallback_n / total_rows * 100) if total_rows else 0
    print(f"  Age date source breakdown:")
    print(f"    offer_sent field:              {offer_sent_n} ({offer_sent_n/total_rows*100:.0f}%)")
    print(f"    date_added_to_cold_offer:       {stats['age_source_counts']['date_added_to_cold_offer']}")
    print(f"    contact.dateAdded (fallback):   {stats['age_source_counts']['contact.dateAdded']}")
    print(f"    no date at all:                 {stats['age_source_counts']['none']}")
    print(f"  ==> {fallback_rate:.0f}% of rows are NOT using offer_sent — ages for those rows are soft "
          f"(they measure 'time in the cold-offer pipeline', not 'time since offer sent').")
    print(f"\n  Non-Zillow listing links (e.g. LoopNet): {stats['non_zillow']}")
    print(f"  Contacts with no listing_link at all:    {stats['no_link']}")
    print(f"  Zillow links with unparseable zpid:      {stats['no_zpid_match']}")
    unknown_n = counts.get("UNKNOWN", 0)
    print(f"  Total UNKNOWN-bucket rows:                {unknown_n}")
    if stats["unrecognized_status"]:
        print(f"  Unrecognized Zillow homeStatus values seen: {stats['unrecognized_status']} "
              f"(folded into UNKNOWN above — bucket list in the task didn't cover these)")
    if stats["multi_opportunity"]:
        print(f"\n  Contacts with >1 opportunity ({len(stats['multi_opportunity'])}):")
        for m in stats["multi_opportunity"][:25]:
            print(f"    {m['name']} ({m['contactId']}): {m['count']} opportunities")
        if len(stats["multi_opportunity"]) > 25:
            print(f"    ... and {len(stats['multi_opportunity']) - 25} more")
    else:
        print("\n  No contacts with multiple opportunities.")


HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Cold Offer Pipeline — {date}</title>
<style>
  :root {{
    --active: #1a7f37; --active-bg: #e6f4ea;
    --pending: #9a6700; --pending-bg: #fff3d6;
    --exited: #6e7781; --exited-bg: #eef0f2;
    --unknown: #b91c1c; --unknown-bg: #fde8e8;
    --border: #d0d7de; --text: #1f2328; --bg: #ffffff;
  }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; margin: 0; padding: 24px;
          background: var(--bg); color: var(--text); }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .subtitle {{ color: #57606a; font-size: 13px; margin-bottom: 20px; }}
  .summary-row {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }}
  .stat-card {{ border: 1px solid var(--border); border-radius: 8px; padding: 10px 16px; min-width: 100px; }}
  .stat-card .n {{ font-size: 22px; font-weight: 700; }}
  .stat-card .l {{ font-size: 11px; color: #57606a; text-transform: uppercase; }}
  .hist {{ display: flex; align-items: flex-end; gap: 6px; height: 140px; border: 1px solid var(--border);
           border-radius: 8px; padding: 12px; margin-bottom: 20px; }}
  .hist-col {{ display: flex; flex-direction: column; align-items: center; justify-content: flex-end;
               flex: 1; height: 100%; }}
  .hist-bar {{ width: 70%; background: var(--active); border-radius: 3px 3px 0 0; min-height: 2px; }}
  .hist-n {{ font-size: 11px; margin-bottom: 2px; }}
  .hist-label {{ font-size: 10px; color: #57606a; margin-top: 4px; }}
  .filters {{ margin-bottom: 12px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
  .filters button {{ border: 1px solid var(--border); background: #fff; padding: 6px 14px; border-radius: 16px;
                      cursor: pointer; font-size: 13px; }}
  .filters button.active {{ background: var(--text); color: #fff; border-color: var(--text); }}
  .search {{ margin-left: auto; padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px;
             font-size: 13px; min-width: 220px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
  th, td {{ padding: 6px 10px; border-bottom: 1px solid var(--border); text-align: left; white-space: nowrap; }}
  th {{ cursor: pointer; user-select: none; position: sticky; top: 0; background: #fff; border-bottom: 2px solid var(--border); }}
  th:hover {{ background: #f6f8fa; }}
  th .arrow {{ font-size: 10px; color: #57606a; }}
  tr.ACTIVE td.status-cell {{ background: var(--active-bg); color: var(--active); font-weight: 600; }}
  tr.PENDING td.status-cell {{ background: var(--pending-bg); color: var(--pending); font-weight: 600; }}
  tr.EXITED td.status-cell {{ background: var(--exited-bg); color: var(--exited); font-weight: 600; }}
  tr.UNKNOWN td.status-cell {{ background: var(--unknown-bg); color: var(--unknown); font-weight: 600; }}
  td.addr {{ white-space: normal; max-width: 260px; }}
  a {{ color: #0969da; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .table-wrap {{ overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; }}
  .footnote {{ font-size: 11px; color: #57606a; margin-top: 16px; }}
  .row-count {{ font-size: 12px; color: #57606a; margin-bottom: 8px; }}
</style>
</head>
<body>
  <h1>Cold Offer Pipeline</h1>
  <div class="subtitle">Generated {date} &middot; {total} rows &middot; read-only snapshot from GHL + Zillow (via Apify)</div>

  <div class="summary-row" id="summaryCards"></div>

  <div class="hist" id="histogram"></div>

  <div class="filters">
    <button data-bucket="ALL" class="active">All ({total})</button>
    <button data-bucket="ACTIVE">Active ({n_active})</button>
    <button data-bucket="PENDING">Pending ({n_pending})</button>
    <button data-bucket="EXITED">Exited ({n_exited})</button>
    <button data-bucket="UNKNOWN">Unknown ({n_unknown})</button>
    <input class="search" id="searchBox" placeholder="Filter by agent / address / status...">
  </div>

  <div class="row-count" id="rowCount"></div>

  <div class="table-wrap">
    <table id="pipelineTable">
      <thead>
        <tr>
          <th data-key="agent">Agent</th>
          <th data-key="address">Address</th>
          <th data-key="listing_link">Zillow Link</th>
          <th data-key="bucket">Status</th>
          <th data-key="age_days">Days</th>
          <th data-key="asking_price">Asking</th>
          <th data-key="my_offer">My Offer</th>
          <th data-key="spread_pct">Spread %</th>
        </tr>
      </thead>
      <tbody id="tableBody"></tbody>
    </table>
  </div>

  <div class="footnote">
    "Address" is the property address stored on the contact record. Days is age since
    offer_sent where present, else date_added_to_cold_offer, else the contact's GHL dateAdded — hover status
    cells for the exact source per row is not shown here, see the CSV for the age_source column.
  </div>

<script>
const ROWS = {rows_json};
const HIST = {hist_json};

function fmtMoney(v) {{ return v === null ? '-' : '$' + Math.round(v).toLocaleString(); }}
function fmtPct(v) {{ return v === null ? '-' : (v >= 0 ? '+' : '') + v.toFixed(1) + '%'; }}

function renderSummary() {{
  const counts = {{ACTIVE:0, PENDING:0, EXITED:0, UNKNOWN:0}};
  ROWS.forEach(r => counts[r.bucket] = (counts[r.bucket]||0) + 1);
  const el = document.getElementById('summaryCards');
  el.innerHTML = Object.entries(counts).map(([k,v]) =>
    `<div class="stat-card"><div class="n">${{v}}</div><div class="l">${{k}}</div></div>`
  ).join('') + `<div class="stat-card"><div class="n">${{ROWS.length}}</div><div class="l">Total</div></div>`;
}}

function renderHistogram() {{
  const max = Math.max(1, ...HIST.map(h => h.n));
  const el = document.getElementById('histogram');
  el.innerHTML = HIST.map(h =>
    `<div class="hist-col">
       <div class="hist-n">${{h.n}}</div>
       <div class="hist-bar" style="height:${{Math.max(2, (h.n/max)*100)}}px"></div>
       <div class="hist-label">${{h.label}}</div>
     </div>`
  ).join('');
}}

let currentBucket = 'ALL';
let sortKey = 'age_days';
let sortDir = -1;

function escapeHtml(s) {{
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}}

function filteredRows() {{
  const q = document.getElementById('searchBox').value.trim().toLowerCase();
  return ROWS.filter(r => {{
    if (currentBucket !== 'ALL' && r.bucket !== currentBucket) return false;
    if (!q) return true;
    return (r.agent||'').toLowerCase().includes(q)
        || (r.address||'').toLowerCase().includes(q)
        || (r.bucket||'').toLowerCase().includes(q)
        || (r.status_detail||'').toLowerCase().includes(q);
  }});
}}

function renderTable() {{
  let rows = filteredRows();
  rows.sort((a,b) => {{
    let av = a[sortKey], bv = b[sortKey];
    if (av === null || av === undefined) av = -Infinity;
    if (bv === null || bv === undefined) bv = -Infinity;
    if (typeof av === 'string') av = av.toLowerCase();
    if (typeof bv === 'string') bv = bv.toLowerCase();
    if (av < bv) return -1 * sortDir;
    if (av > bv) return 1 * sortDir;
    return 0;
  }});
  document.getElementById('rowCount').textContent = rows.length + ' row(s) shown';
  document.getElementById('tableBody').innerHTML = rows.map(r => `
    <tr class="${{r.bucket}}">
      <td>${{escapeHtml(r.agent)}}</td>
      <td class="addr">${{escapeHtml(r.address) || '<span style=\"color:#999\">(none)</span>'}}</td>
      <td>${{r.listing_link ? `<a href="${{escapeHtml(r.listing_link)}}" target="_blank" rel="noopener">listing</a>` : '-'}}</td>
      <td class="status-cell" title="${{escapeHtml(r.status_detail)}}">${{r.bucket}}</td>
      <td>${{r.age_days === null ? '-' : r.age_days}}</td>
      <td>${{fmtMoney(r.asking_price)}}</td>
      <td>${{fmtMoney(r.my_offer)}}</td>
      <td>${{fmtPct(r.spread_pct)}}</td>
    </tr>
  `).join('');
}}

document.querySelectorAll('.filters button').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.filters button').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentBucket = btn.dataset.bucket;
    renderTable();
  }});
}});

document.getElementById('searchBox').addEventListener('input', renderTable);

document.querySelectorAll('th[data-key]').forEach(th => {{
  th.addEventListener('click', () => {{
    const key = th.dataset.key;
    if (sortKey === key) {{ sortDir *= -1; }} else {{ sortKey = key; sortDir = 1; }}
    renderTable();
  }});
}});

renderSummary();
renderHistogram();
renderTable();
</script>
</body>
</html>
"""


def render_html(rows: list[dict], out_path: Path, today: dt.date) -> None:
    counts = {b: 0 for b in BUCKET_ORDER}
    for r in rows:
        counts[r["bucket"]] = counts.get(r["bucket"], 0) + 1

    active_ages = [r["age_days"] for r in rows if r["bucket"] == "ACTIVE" and r["age_days"] is not None]
    hist_counts = {label: 0 for _, _, label in AGE_BUCKETS}
    for d in active_ages:
        hist_counts[age_bucket_label(d)] += 1
    hist_json = json.dumps([{"label": label, "n": hist_counts[label]} for _, _, label in AGE_BUCKETS])

    html_out = HTML_TEMPLATE.format(
        date=today.isoformat(),
        total=len(rows),
        n_active=counts.get("ACTIVE", 0),
        n_pending=counts.get("PENDING", 0),
        n_exited=counts.get("EXITED", 0),
        n_unknown=counts.get("UNKNOWN", 0),
        rows_json=json.dumps(rows),
        hist_json=hist_json,
    )
    out_path.write_text(html_out)


def write_csv(rows: list[dict], out_path: Path) -> None:
    fieldnames = ["agent", "address", "listing_link", "zpid", "bucket", "status_detail",
                  "age_days", "age_source", "asking_price", "my_offer", "spread_pct",
                  "mls_status", "mls_agent_name", "opportunities_count", "contactId"]
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k) for k in fieldnames})


def main() -> None:
    contacts_path = lib.CACHE_DIR / "contacts.json"
    zillow_path = lib.CACHE_DIR / "zillow_status.json"
    if not contacts_path.exists():
        raise SystemExit(f"{contacts_path} not found — run scripts/fetch_contacts.py first.")

    contacts = json.loads(contacts_path.read_text()).get("contacts", [])
    zillow_cache = json.loads(zillow_path.read_text()) if zillow_path.exists() else {}
    if not zillow_path.exists():
        print(f"!! {zillow_path} not found — every row will be UNKNOWN until you run scripts/fetch_zillow.py --yes.\n")

    today = dt.date.today()
    rows, stats = build_rows(contacts, zillow_cache, today)

    print_terminal_summary(rows, stats, len(contacts))

    lib.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = today.isoformat()
    html_path = lib.REPORTS_DIR / f"pipeline-{date_str}.html"
    csv_path = lib.REPORTS_DIR / f"pipeline-{date_str}.csv"
    render_html(rows, html_path, today)
    write_csv(rows, csv_path)

    print(f"\nWrote {html_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
