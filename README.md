# claude-sandbox

Read-only dashboard for the cold-offer real-estate pipeline (GHL + Zillow via Apify).

**This tooling never writes to GHL.** Every GHL call is a search/read call; there is
no code path that updates a field, adds a tag, changes a status, or deletes anything.
If a write would help (e.g. syncing Zillow status back onto the contact), that's a
separate, explicit decision — this tool won't do it silently.

## Setup

```
cp .env.example .env
# fill in GHL_TOKEN, APIFY_TOKEN, APIFY_ZILLOW_ACTOR_ID (GHL_LOCATION_ID is prefilled)
pip install requests
```

`APIFY_ZILLOW_ACTOR_ID` and the input schema it expects are not verified in this
repo — check your chosen actor's Input tab on Apify before your first real run (see
the header comment in `scripts/fetch_zillow.py`).

## Usage (three separate steps, each independently re-runnable)

```
python3 scripts/fetch_contacts.py            # Step 1: pulls contacts from GHL -> cache/contacts.json
python3 scripts/fetch_zillow.py               # Step 2: dry-run — prints lookup count + cost estimate, does nothing else
python3 scripts/fetch_zillow.py --yes         # Step 2: actually calls Apify, after you've confirmed the numbers above
python3 scripts/build_report.py               # Step 3+4: classify, age, render — reads cache only, no network calls
```

- `fetch_contacts.py --force` re-pulls from GHL even if `cache/contacts.json` exists.
- `fetch_zillow.py --force` ignores the 72h freshness window and re-checks every zpid.
- `build_report.py` can be re-run any time to re-render from whatever is in `cache/`
  (e.g. after editing classification logic) without spending another Apify call.

Outputs land in `./reports/pipeline-<date>.html` (open in a browser — sortable,
filterable, color-coded by bucket) and `./reports/pipeline-<date>.csv`.

`cache/` and `reports/` hold real business data and are gitignored — never commit
their contents.
