"""Shared helpers for the cold-offer pipeline dashboard.

Read-only tooling. Nothing in this module (or anything that imports it)
issues a write/update/delete call to GHL.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = REPO_ROOT / "cache"
REPORTS_DIR = REPO_ROOT / "reports"

GHL_API_BASE = "https://services.leadconnectorhq.com"
GHL_API_VERSION = "2021-07-28"

# Known custom field IDs (already verified against the GHL schema — do not
# re-discover / re-guess these).
FIELD = {
    "listing_link": "9X4OGkXd5dacf5tgbXKD",
    "offer_sent": "Ac18Ejak4sXVJkvUVuh0",
    "date_added_to_cold_offer": "ETlTjKGE4voYUpl8GggV",
    "listed_on": "uPPYMXEXhWU8pwTyf96R",
    "zillow_price": "HNo4BcDJ7iaHmvB913ws",
    "asking_price": "u52aFuaJJcQw2SWraKDh",
    "offer_price": "DZ7di1qBMPUYVwSc8R9f",
    "offer_made": "2e9FPNpCb59RWL94wuGL",
    "mls_status": "oiuSVg3KlxYE40J2s6wj",
    "property_note": "6HKSB3ZRi1p9UY09qKHf",
    "property_description": "fuOtvzt3LU3KrLKJiLvd",
    "mls_agent_name": "yMPevrwsohbZaSIYwATs",
}

ZPID_RE = re.compile(r"/(\d+)_zpid")

BUCKETS = {
    "ACTIVE": {"FOR_SALE", "FOR_SALE_BY_OWNER", "FOR_SALE_BY_AGENT"},
    "PENDING": {"PENDING", "CONTINGENT", "ACCEPTING_BACKUP_OFFERS"},
    "EXITED": {"SOLD", "RECENTLY_SOLD", "OFF_MARKET", "OTHER"},
}


def load_env(env_path: Path | None = None) -> dict[str, str]:
    """Minimal .env loader (no external deps). Does not print values."""
    path = env_path or (REPO_ROOT / ".env")
    values: dict[str, str] = dict(os.environ)
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            values.setdefault(key, val)
            os.environ.setdefault(key, val)
    return values


def require_env(keys: list[str]) -> dict[str, str]:
    values = load_env()
    missing = [k for k in keys if not values.get(k)]
    if missing:
        raise SystemExit(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + f"\nCopy .env.example to .env at {REPO_ROOT} and fill them in."
        )
    return {k: values[k] for k in keys}


def get_custom_field(contact: dict[str, Any], field_id: str) -> Any:
    """Pull a custom field value off a GHL contact by field id.

    GHL v2 contacts typically carry customFields as a list of
    {"id": ..., "value": ...} — but this defends against a flat dict shape
    too, since we haven't seen every possible response variant.
    """
    cfs = contact.get("customFields") or contact.get("customField") or []
    if isinstance(cfs, dict):
        return cfs.get(field_id)
    for cf in cfs:
        if isinstance(cf, dict) and cf.get("id") == field_id:
            return cf.get("value")
    return None


def extract_zpid(listing_link: str | None) -> str | None:
    if not listing_link:
        return None
    m = ZPID_RE.search(listing_link)
    return m.group(1) if m else None


def is_zillow_homedetails(listing_link: str | None) -> bool:
    if not listing_link:
        return False
    return "zillow.com/homedetails" in listing_link


def parse_date_loose(value: Any) -> dt.date | None:
    """Parse dates from GHL custom fields / standard fields.

    GHL dates show up as millis-since-epoch, ISO strings, or plain
    YYYY-MM-DD depending on field type and endpoint — handle all three
    rather than assuming one.
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        # heuristic: treat as millis if it's a large number
        ts = value / 1000 if value > 10_000_000_000 else value
        try:
            return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).date()
        except (ValueError, OSError, OverflowError):
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # strip trailing Z / offset for fromisoformat compatibility
        try:
            if s.endswith("Z"):
                s2 = s[:-1] + "+00:00"
            else:
                s2 = s
            return dt.datetime.fromisoformat(s2).date()
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
            try:
                return dt.datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    return None


def to_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = re.sub(r"[^0-9.\-]", "", value)
        if cleaned in ("", "-", "."):
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def classify_status(home_status: str | None) -> str:
    """Bucket a Zillow homeStatus value.

    Anything present but not one of the known enum values comes back
    prefixed "UNRECOGNIZED:" rather than being silently folded into
    UNKNOWN — UNKNOWN per spec means "no zpid / failed lookup / 404",
    which is a different situation from "Zillow returned a status we
    haven't seen before" and the report should call that out separately.
    """
    if not home_status:
        return "UNKNOWN"
    hs = home_status.upper()
    for bucket, statuses in BUCKETS.items():
        if hs in statuses:
            return bucket
    return f"UNRECOGNIZED:{home_status}"
