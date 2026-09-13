#!/usr/bin/env python3
"""Step 1 — pull the cold-offer contact population from GHL.

READ-ONLY. This script only ever calls POST /contacts/search, which is a
search/read endpoint in GHL's API — nothing here updates a field, adds a
tag, changes a status, or deletes anything.

Usage:
    python3 scripts/fetch_contacts.py [--force]

--force re-fetches from the API even if ./cache/contacts.json already
exists. Without it, an existing cache file is left alone (per the "reruns
shouldn't re-hit the API" requirement) — delete the cache file yourself if
you want a clean pull.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time

import requests

import lib

FILTER_FIELD_KEY = f"customFields.{lib.FIELD['listing_link']}"
PAGE_LIMIT = 100
EXPECTED_APPROX = 513


def search_page(session: requests.Session, location_id: str, search_after: list | None) -> dict:
    body = {
        "locationId": location_id,
        "pageLimit": PAGE_LIMIT,
        "filters": [
            {"field": FILTER_FIELD_KEY, "operator": "exists"},
        ],
    }
    if search_after is not None:
        body["searchAfter"] = search_after

    resp = session.post(f"{lib.GHL_API_BASE}/contacts/search", json=body, timeout=30)
    if resp.status_code != 200:
        print(f"\n!! GHL returned HTTP {resp.status_code} for /contacts/search", file=sys.stderr)
        print(resp.text[:4000], file=sys.stderr)
        resp.raise_for_status()
    data = resp.json()
    if "contacts" not in data:
        print("\n!! Unexpected response shape from /contacts/search — expected a 'contacts' key.", file=sys.stderr)
        print(json.dumps(data, indent=2)[:4000], file=sys.stderr)
        raise SystemExit(1)
    return data


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="re-fetch even if cache exists")
    args = parser.parse_args()

    cache_path = lib.CACHE_DIR / "contacts.json"
    if cache_path.exists() and not args.force:
        cached = json.loads(cache_path.read_text())
        print(f"Cache already exists at {cache_path} ({len(cached.get('contacts', []))} contacts, "
              f"fetched {cached.get('fetchedAt')}). Pass --force to re-fetch.")
        return

    env = lib.require_env(["GHL_TOKEN", "GHL_LOCATION_ID"])
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {env['GHL_TOKEN']}",
        "Version": lib.GHL_API_VERSION,
        "Content-Type": "application/json",
        "Accept": "application/json",
    })

    all_contacts: list[dict] = []
    search_after = None
    page = 0
    raw_pages: list[dict] = []

    while True:
        page += 1
        data = search_page(session, env["GHL_LOCATION_ID"], search_after)
        raw_pages.append(data)
        contacts = data.get("contacts", [])
        all_contacts.extend(contacts)
        print(f"  page {page}: +{len(contacts)} contacts (running total {len(all_contacts)})")

        next_search_after = data.get("searchAfter")
        if not contacts or not next_search_after or len(contacts) < PAGE_LIMIT:
            break
        if next_search_after == search_after:
            print("!! searchAfter cursor did not advance — stopping to avoid an infinite loop.", file=sys.stderr)
            break
        search_after = next_search_after
        time.sleep(0.2)  # be polite to the API

    lib.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetchedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
        "locationId": env["GHL_LOCATION_ID"],
        "filter": FILTER_FIELD_KEY,
        "count": len(all_contacts),
        "contacts": all_contacts,
    }
    cache_path.write_text(json.dumps(payload, indent=2))

    print(f"\nFetched {len(all_contacts)} contacts -> {cache_path}")
    if abs(len(all_contacts) - EXPECTED_APPROX) > 50:
        print(f"!! Expected roughly {EXPECTED_APPROX} records — got {len(all_contacts)}. "
              f"Worth a sanity check before trusting downstream numbers.")

    multi_opp = [c for c in all_contacts if len(c.get("opportunities") or []) > 1]
    if multi_opp:
        print(f"Note: {len(multi_opp)} contacts carry more than one opportunity "
              f"(this is expected — one row per contact, flagged in the report's data-quality block).")


if __name__ == "__main__":
    main()
