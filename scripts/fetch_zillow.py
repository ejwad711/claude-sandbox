#!/usr/bin/env python3
"""Step 2 — resolve Zillow listing status via Apify (never zillow.com directly).

READ-ONLY against GHL (this script doesn't touch GHL at all — it only
reads ./cache/contacts.json) and only ever *reads* Zillow data through
Apify's actor-run API.

IMPORTANT — cost gate:
This script ALWAYS stops after printing the lookup count and estimated
cost. It will not call Apify unless you re-run it with --yes. That is
intentional per the task's ground rules ("print cost, wait for
confirmation") — do not remove this gate.

IMPORTANT — actor input schema is unverified:
There are several different "Zillow detail" actors on the Apify store and
they don't share one input schema. This script defaults to sending
{"zpids": [...]} per batch. If your actor expects something else (e.g.
{"startUrls": [...]} of zillow.com/homedetails/<zpid>_zpid URLs), override
via the APIFY_INPUT_TEMPLATE env var (a JSON object where the string
"__ZPIDS__" is replaced with the batch's zpid list) before running for
real. Check your actor's "Input" tab on Apify before trusting the first
run.

Usage:
    python3 scripts/fetch_zillow.py           # dry-run: prints plan + cost, does nothing else
    python3 scripts/fetch_zillow.py --yes     # actually calls Apify
    python3 scripts/fetch_zillow.py --force   # ignore the 72h cache freshness window
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time

import requests

import lib

BATCH_SIZE = 100
FRESHNESS_HOURS = 72
APIFY_API_BASE = "https://api.apify.com/v2"


def load_contacts() -> list[dict]:
    path = lib.CACHE_DIR / "contacts.json"
    if not path.exists():
        raise SystemExit(f"{path} not found — run scripts/fetch_contacts.py first.")
    return json.loads(path.read_text()).get("contacts", [])


def load_zillow_cache() -> dict:
    path = lib.CACHE_DIR / "zillow_status.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_zillow_cache(cache: dict) -> None:
    lib.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (lib.CACHE_DIR / "zillow_status.json").write_text(json.dumps(cache, indent=2))


def is_stale(entry: dict | None, force: bool) -> bool:
    if entry is None:
        return True
    if force:
        return True
    checked_at = entry.get("checkedAt")
    if not checked_at:
        return True
    try:
        checked = dt.datetime.fromisoformat(checked_at)
    except ValueError:
        return True
    age = dt.datetime.now(dt.timezone.utc) - checked
    return age > dt.timedelta(hours=FRESHNESS_HOURS)


def run_apify_batch(token: str, actor_id: str, zpids: list[str], input_template: dict | None) -> list[dict]:
    if input_template:
        body = json.loads(json.dumps(input_template).replace('"__ZPIDS__"', json.dumps(zpids)))
    else:
        body = {"zpids": zpids}

    url = f"{APIFY_API_BASE}/acts/{actor_id}/run-sync-get-dataset-items"
    resp = requests.post(url, params={"token": token}, json=body, timeout=600)
    if resp.status_code != 200:
        print(f"\n!! Apify returned HTTP {resp.status_code} for batch of {len(zpids)} zpids", file=sys.stderr)
        print(resp.text[:4000], file=sys.stderr)
        return []
    try:
        items = resp.json()
    except ValueError:
        print("\n!! Apify response was not JSON — dumping raw text:", file=sys.stderr)
        print(resp.text[:4000], file=sys.stderr)
        return []
    if not isinstance(items, list):
        print("\n!! Unexpected Apify response shape (expected a list of dataset items):", file=sys.stderr)
        print(json.dumps(items, indent=2)[:4000], file=sys.stderr)
        return []
    return items


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="ignore the 72h freshness window")
    parser.add_argument("--yes", action="store_true", help="actually call Apify (otherwise dry-run only)")
    args = parser.parse_args()

    contacts = load_contacts()
    zillow_cache = load_zillow_cache()

    non_zillow_links: list[dict] = []
    no_link: list[dict] = []
    zpid_to_contacts: dict[str, list[str]] = {}

    for c in contacts:
        link = lib.get_custom_field(c, lib.FIELD["listing_link"])
        name = f"{c.get('firstName', '')} {c.get('lastName', '')}".strip() or c.get("id", "unknown")
        if not link:
            no_link.append({"contactId": c.get("id"), "name": name})
            continue
        if not lib.is_zillow_homedetails(link):
            non_zillow_links.append({"contactId": c.get("id"), "name": name, "link": link})
            continue
        zpid = lib.extract_zpid(link)
        if not zpid:
            non_zillow_links.append({"contactId": c.get("id"), "name": name, "link": link,
                                      "note": "zillow.com/homedetails link but no _zpid found"})
            continue
        zpid_to_contacts.setdefault(zpid, []).append(c.get("id"))

    all_zpids = sorted(zpid_to_contacts)
    stale_zpids = [z for z in all_zpids if is_stale(zillow_cache.get(z), args.force)]
    fresh_count = len(all_zpids) - len(stale_zpids)

    print(f"Contacts with a listing_link:      {len(contacts) - len(no_link)}")
    print(f"  -> zillow.com/homedetails links:  {len(all_zpids)} unique zpids ({sum(len(v) for v in zpid_to_contacts.values())} contacts)")
    print(f"  -> non-Zillow links (e.g. LoopNet): {len(non_zillow_links)}")
    print(f"Contacts with no listing_link at all: {len(no_link)}")
    print()
    print(f"zpids already fresh in cache (<{FRESHNESS_HOURS}h old): {fresh_count}")
    print(f"zpids needing an Apify lookup:                    {len(stale_zpids)}")

    if not stale_zpids:
        print("\nNothing to fetch — cache is fresh. Run scripts/build_report.py to render.")
        return

    num_batches = (len(stale_zpids) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"\nWould call Apify in {num_batches} batch(es) of up to {BATCH_SIZE} zpids "
          f"({len(stale_zpids)} lookups total).")

    env = lib.load_env()
    cost_per_1000 = env.get("APIFY_COST_PER_1000_RESULTS")
    if cost_per_1000:
        try:
            est = (len(stale_zpids) / 1000) * float(cost_per_1000)
            print(f"Estimated cost @ ${cost_per_1000}/1000 results (from APIFY_COST_PER_1000_RESULTS): ${est:.2f}")
        except ValueError:
            print("APIFY_COST_PER_1000_RESULTS is set but not a number — can't estimate cost.")
    else:
        print("Cost estimate unavailable: set APIFY_COST_PER_1000_RESULTS in .env (check your actor's "
              "pricing page on Apify) to get a dollar estimate here. Proceeding without a cost figure "
              "means going in blind on spend — not recommended.")

    if not args.yes:
        print("\nDry run only — no Apify calls made. Re-run with --yes to actually fetch, "
              "once you've confirmed the count/cost above.")
        return

    print("\n--yes passed — proceeding with Apify calls...")
    env = lib.require_env(["APIFY_TOKEN", "APIFY_ZILLOW_ACTOR_ID"])
    input_template = None
    if env_template := lib.load_env().get("APIFY_INPUT_TEMPLATE"):
        try:
            input_template = json.loads(env_template)
        except ValueError:
            raise SystemExit("APIFY_INPUT_TEMPLATE is set but is not valid JSON.")

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    for i in range(0, len(stale_zpids), BATCH_SIZE):
        batch = stale_zpids[i:i + BATCH_SIZE]
        print(f"  batch {i // BATCH_SIZE + 1}/{num_batches}: {len(batch)} zpids...")
        items = run_apify_batch(env["APIFY_TOKEN"], env["APIFY_ZILLOW_ACTOR_ID"], batch, input_template)

        by_zpid = {}
        for item in items:
            z = str(item.get("zpid") or item.get("zpId") or "")
            if z:
                by_zpid[z] = item

        for z in batch:
            item = by_zpid.get(z)
            if item is None:
                zillow_cache[z] = {"homeStatus": None, "checkedAt": now_iso, "error": "no result / lookup failed"}
            else:
                zillow_cache[z] = {
                    "homeStatus": item.get("homeStatus"),
                    "checkedAt": now_iso,
                    "raw": item,
                }
        save_zillow_cache(zillow_cache)  # persist incrementally so a crash mid-run doesn't lose progress
        time.sleep(0.5)

    print(f"\nDone. {len(stale_zpids)} zpids updated -> {lib.CACHE_DIR / 'zillow_status.json'}")
    print("Run scripts/build_report.py next.")


if __name__ == "__main__":
    main()
