#!/usr/bin/env python3
"""Cold-offer pipeline dashboard, sourced entirely from GHL (via the Tonomy
MCP connection) — no Zillow, no Apify.

Where the Zillow-based report (build_report.py) infers ACTIVE/PENDING/
EXITED from an external listing status, this one buckets by the deal's
*native GHL pipeline status* (open/won/lost/abandoned) plus its pipeline
stage — data GHL already tracks for every opportunity, no external lookup
needed.

Pure offline render: reads ./cache/contacts.json and ./cache/pipelines.json
only. No network calls of any kind, no GHL writes. Safe to re-run freely.

Usage:
    python3 scripts/build_tonomy_report.py
"""
from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

import lib
from build_report import (
    AGE_BUCKETS,
    age_bucket_label,
    contact_address,
    contact_name,
    fmt_money,
    fmt_pct,
)

BUCKET_ORDER = ["OPEN", "WON", "LOST", "ABANDONED", "NO_OPPORTUNITY"]

BUCKET_PRIORITY = {"open": 0, "won": 1, "lost": 2, "abandoned": 3}


def load_pipeline_index(pipelines_path: Path) -> dict:
    if not pipelines_path.exists():
        return {}
    data = json.loads(pipelines_path.read_text())
    index = {}
    for p in data.get("pipelines", []):
        stages = {s["id"]: s["name"] for s in p.get("stages", [])}
        index[p["id"]] = {"name": p["name"], "stages": stages}
    return index


def pick_controlling_opportunity(opportunities: list[dict]) -> dict | None:
    """When a contact carries multiple opportunities, pick the one that
    decides the row's bucket: an open deal is the most actionable, so it
    wins over a closed one even if it's not the most recent."""
    if not opportunities:
        return None
    return min(
        opportunities,
        key=lambda o: BUCKET_PRIORITY.get((o.get("status") or "").lower(), 99),
    )


def build_rows(contacts: list[dict], pipeline_index: dict, today: dt.date) -> tuple[list[dict], dict]:
    rows = []
    stats = {
        "age_source_counts": {"offer_sent": 0, "date_added_to_cold_offer": 0, "contact.dateAdded": 0, "none": 0},
        "multi_opportunity": [],
        "unmapped_pipeline_or_stage": {},
        "no_asking_price": 0,
        "no_offer": 0,
        "zero_asking_price": [],
        "pipeline_distribution": {},
    }

    for c in contacts:
        opportunities = c.get("opportunities") or []
        controlling = pick_controlling_opportunity(opportunities)

        if controlling is None:
            bucket = "NO_OPPORTUNITY"
            pipeline_name, stage_name = "", ""
        else:
            status = (controlling.get("status") or "").upper()
            bucket = status if status in BUCKET_ORDER else "NO_OPPORTUNITY"
            pinfo = pipeline_index.get(controlling.get("pipelineId"))
            if pinfo is None:
                pipeline_name = "(unknown pipeline)"
                stage_name = "(unknown stage)"
                key = f"pipelineId={controlling.get('pipelineId')}"
                stats["unmapped_pipeline_or_stage"][key] = stats["unmapped_pipeline_or_stage"].get(key, 0) + 1
            else:
                pipeline_name = pinfo["name"]
                stage_name = pinfo["stages"].get(controlling.get("pipelineStageId"), "(unknown stage)")
                if stage_name == "(unknown stage)":
                    key = f"{pipeline_name} / stageId={controlling.get('pipelineStageId')}"
                    stats["unmapped_pipeline_or_stage"][key] = stats["unmapped_pipeline_or_stage"].get(key, 0) + 1
            stats["pipeline_distribution"][pipeline_name] = stats["pipeline_distribution"].get(pipeline_name, 0) + 1

        # age + fallback source tracking (same chain as the Zillow report)
        offer_sent_date = lib.parse_date_loose(lib.get_custom_field(c, lib.FIELD["offer_sent"]))
        date_added_date = lib.parse_date_loose(lib.get_custom_field(c, lib.FIELD["date_added_to_cold_offer"]))
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
        if asking_price is None:
            stats["no_asking_price"] += 1
        elif asking_price == 0:
            stats["zero_asking_price"].append({"contactId": c.get("id"), "name": contact_name(c)})
        if my_offer is None:
            stats["no_offer"] += 1

        spread_pct = None
        if asking_price and my_offer is not None and asking_price != 0:
            spread_pct = (my_offer - asking_price) / asking_price * 100.0

        opp_count = len(opportunities)
        if opp_count > 1:
            stats["multi_opportunity"].append({
                "contactId": c.get("id"), "name": contact_name(c), "count": opp_count,
                "statuses": [o.get("status") for o in opportunities],
            })

        rows.append({
            "contactId": c.get("id"),
            "agent": contact_name(c),
            "business": c.get("businessName") or "",
            "address": contact_address(c),
            "listing_link": lib.get_custom_field(c, lib.FIELD["listing_link"]) or "",
            "bucket": bucket,
            "pipeline": pipeline_name,
            "stage": stage_name,
            "mls_status": lib.get_custom_field(c, lib.FIELD["mls_status"]) or "",
            "age_days": age_days,
            "age_source": age_source,
            "asking_price": asking_price,
            "my_offer": my_offer,
            "spread_pct": spread_pct,
            "opportunities_count": opp_count,
        })

    return rows, stats


def print_terminal_summary(rows: list[dict], stats: dict) -> None:
    counts = {b: 0 for b in BUCKET_ORDER}
    for r in rows:
        counts[r["bucket"]] = counts.get(r["bucket"], 0) + 1

    print("=" * 60)
    print("TONOMY (GHL-NATIVE) PIPELINE SUMMARY — no Zillow/Apify")
    print("=" * 60)
    for b in BUCKET_ORDER:
        print(f"  {b:<15} {counts.get(b, 0)}")
    print(f"  {'TOTAL':<15} {len(rows)}")

    print("\nPipeline distribution (by controlling opportunity):")
    for name, n in sorted(stats["pipeline_distribution"].items(), key=lambda kv: -kv[1]):
        print(f"  {name:<25} {n}")

    print("\nOPEN deals by age:")
    open_ages = [r["age_days"] for r in rows if r["bucket"] == "OPEN" and r["age_days"] is not None]
    hist = {label: 0 for _, _, label in AGE_BUCKETS}
    for d in open_ages:
        hist[age_bucket_label(d)] += 1
    max_count = max(hist.values(), default=0)
    bar_width = 40
    for _, _, label in AGE_BUCKETS:
        n = hist[label]
        bar_len = int((n / max_count) * bar_width) if max_count else 0
        print(f"  {label:>8} | {'#' * bar_len}{' ' * (bar_width - bar_len)} {n}")
    open_no_age = sum(1 for r in rows if r["bucket"] == "OPEN" and r["age_days"] is None)
    if open_no_age:
        print(f"  (+{open_no_age} OPEN rows with no age date at all)")

    print("\n15 oldest OPEN rows:")
    oldest = sorted(
        (r for r in rows if r["bucket"] == "OPEN" and r["age_days"] is not None),
        key=lambda r: -r["age_days"],
    )[:15]
    if not oldest:
        print("  (none)")
    for r in oldest:
        print(f"  {r['age_days']:>4}d  {r['agent']:<22} {r['stage']:<20} {r['address'] or '(no address)':<34} "
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
    print(f"  ==> {fallback_rate:.0f}% of rows are NOT using offer_sent — ages for those rows are soft.")
    no_opp_n = sum(1 for r in rows if r["bucket"] == "NO_OPPORTUNITY")
    print(f"\n  Contacts with a listing_link but ZERO opportunities: {no_opp_n} ({no_opp_n/total_rows*100:.0f}% of the population) "
          f"— these are leads that were never actually pushed into a pipeline stage.")
    print(f"  Rows with no asking_price at all: {stats['no_asking_price']} ({stats['no_asking_price']/total_rows*100:.0f}%)")
    print(f"  Rows with no offer amount at all: {stats['no_offer']} ({stats['no_offer']/total_rows*100:.0f}%)")
    if stats["zero_asking_price"]:
        print(f"  Rows with asking_price LITERALLY $0 (not missing — bad data, distinct from the above): "
              f"{len(stats['zero_asking_price'])}")
        for z in stats["zero_asking_price"]:
            print(f"    {z['name']} ({z['contactId']})")
    if stats["unmapped_pipeline_or_stage"]:
        print(f"\n  Unmapped pipeline/stage ids seen (pipelines.json may be stale):")
        for k, n in stats["unmapped_pipeline_or_stage"].items():
            print(f"    {k}: {n}")
    if stats["multi_opportunity"]:
        print(f"\n  Contacts with >1 opportunity ({len(stats['multi_opportunity'])}):")
        for m in stats["multi_opportunity"][:25]:
            print(f"    {m['name']} ({m['contactId']}): {m['count']} opportunities, statuses={m['statuses']}")
        if len(stats["multi_opportunity"]) > 25:
            print(f"    ... and {len(stats['multi_opportunity']) - 25} more")
    else:
        print("\n  No contacts with multiple opportunities.")


HTML_TEMPLATE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Cold Offer Pipeline (GHL-native) — {date}</title>
<style>
  :root {{
    --open: #1a7f37; --open-bg: #e6f4ea;
    --won: #8250df; --won-bg: #f3ecfd;
    --lost: #b91c1c; --lost-bg: #fde8e8;
    --abandoned: #6e7781; --abandoned-bg: #eef0f2;
    --noopp: #9a6700; --noopp-bg: #fff3d6;
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
  .hist-bar {{ width: 70%; background: var(--open); border-radius: 3px 3px 0 0; min-height: 2px; }}
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
  tr.OPEN td.status-cell {{ background: var(--open-bg); color: var(--open); font-weight: 600; }}
  tr.WON td.status-cell {{ background: var(--won-bg); color: var(--won); font-weight: 600; }}
  tr.LOST td.status-cell {{ background: var(--lost-bg); color: var(--lost); font-weight: 600; }}
  tr.ABANDONED td.status-cell {{ background: var(--abandoned-bg); color: var(--abandoned); font-weight: 600; }}
  tr.NO_OPPORTUNITY td.status-cell {{ background: var(--noopp-bg); color: var(--noopp); font-weight: 600; }}
  td.addr {{ white-space: normal; max-width: 240px; }}
  a {{ color: #0969da; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .table-wrap {{ overflow-x: auto; border: 1px solid var(--border); border-radius: 8px; }}
  .footnote {{ font-size: 11px; color: #57606a; margin-top: 16px; }}
  .row-count {{ font-size: 12px; color: #57606a; margin-bottom: 8px; }}
</style>
</head>
<body>
  <h1>Cold Offer Pipeline — GHL-native</h1>
  <div class="subtitle">Generated {date} &middot; {total} rows &middot; read-only snapshot from GHL only
    (no Zillow/Apify) &middot; bucketed by native opportunity status</div>

  <div class="summary-row" id="summaryCards"></div>

  <div class="hist" id="histogram"></div>

  <div class="filters">
    <button data-bucket="ALL" class="active">All ({total})</button>
    <button data-bucket="OPEN">Open ({n_open})</button>
    <button data-bucket="WON">Won ({n_won})</button>
    <button data-bucket="LOST">Lost ({n_lost})</button>
    <button data-bucket="ABANDONED">Abandoned ({n_abandoned})</button>
    <button data-bucket="NO_OPPORTUNITY">No Opportunity ({n_noopp})</button>
    <input class="search" id="searchBox" placeholder="Filter by agent / address / stage...">
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
          <th data-key="stage">Stage</th>
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
    "Address" is the property address stored on the contact record. "Status" is the GHL opportunity
    status (open/won/lost/abandoned) of the deal contacts' furthest-along opportunity is used when a
    contact has multiple. Days is age since offer_sent where present, else date_added_to_cold_offer,
    else the contact's GHL dateAdded — see the CSV's age_source column for the exact source per row.
    No Zillow or Apify data is used anywhere in this report.
  </div>

<script>
const ROWS = {rows_json};
const HIST = {hist_json};

function fmtMoney(v) {{ return v === null ? '-' : '$' + Math.round(v).toLocaleString(); }}
function fmtPct(v) {{ return v === null ? '-' : (v >= 0 ? '+' : '') + v.toFixed(1) + '%'; }}

function renderSummary() {{
  const counts = {{OPEN:0, WON:0, LOST:0, ABANDONED:0, NO_OPPORTUNITY:0}};
  ROWS.forEach(r => counts[r.bucket] = (counts[r.bucket]||0) + 1);
  const el = document.getElementById('summaryCards');
  el.innerHTML = Object.entries(counts).map(([k,v]) =>
    `<div class="stat-card"><div class="n">${{v}}</div><div class="l">${{k.replace('_',' ')}}</div></div>`
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
        || (r.stage||'').toLowerCase().includes(q)
        || (r.bucket||'').toLowerCase().includes(q);
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
      <td class="status-cell">${{r.bucket.replace('_',' ')}}</td>
      <td>${{escapeHtml(r.pipeline)}}${{r.pipeline && r.stage ? ' / ' : ''}}${{escapeHtml(r.stage)}}</td>
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

    open_ages = [r["age_days"] for r in rows if r["bucket"] == "OPEN" and r["age_days"] is not None]
    hist_counts = {label: 0 for _, _, label in AGE_BUCKETS}
    for d in open_ages:
        hist_counts[age_bucket_label(d)] += 1
    hist_json = json.dumps([{"label": label, "n": hist_counts[label]} for _, _, label in AGE_BUCKETS])

    html_out = HTML_TEMPLATE.format(
        date=today.isoformat(),
        total=len(rows),
        n_open=counts.get("OPEN", 0),
        n_won=counts.get("WON", 0),
        n_lost=counts.get("LOST", 0),
        n_abandoned=counts.get("ABANDONED", 0),
        n_noopp=counts.get("NO_OPPORTUNITY", 0),
        rows_json=json.dumps(rows),
        hist_json=hist_json,
    )
    out_path.write_text(html_out)


def write_csv(rows: list[dict], out_path: Path) -> None:
    fieldnames = ["agent", "business", "address", "listing_link", "bucket", "pipeline", "stage",
                  "mls_status", "age_days", "age_source", "asking_price", "my_offer", "spread_pct",
                  "opportunities_count", "contactId"]
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k) for k in fieldnames})


def main() -> None:
    contacts_path = lib.CACHE_DIR / "contacts.json"
    pipelines_path = lib.CACHE_DIR / "pipelines.json"
    if not contacts_path.exists():
        raise SystemExit(f"{contacts_path} not found.")

    contacts = json.loads(contacts_path.read_text()).get("contacts", [])
    pipeline_index = load_pipeline_index(pipelines_path)
    if not pipeline_index:
        print(f"!! {pipelines_path} not found or empty — pipeline/stage names will show as unknown.\n")

    today = dt.date.today()
    rows, stats = build_rows(contacts, pipeline_index, today)

    print_terminal_summary(rows, stats)

    lib.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = today.isoformat()
    html_path = lib.REPORTS_DIR / f"tonomy-pipeline-{date_str}.html"
    csv_path = lib.REPORTS_DIR / f"tonomy-pipeline-{date_str}.csv"
    render_html(rows, html_path, today)
    write_csv(rows, csv_path)

    print(f"\nWrote {html_path}")
    print(f"Wrote {csv_path}")


if __name__ == "__main__":
    main()
