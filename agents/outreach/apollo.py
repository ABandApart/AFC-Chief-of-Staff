"""Apollo.io organization enrichment — the firmographic-spine lane of Part 3.

This is the engine of the **V2 coverage probe**
(`PRD-outreach-company-profile.md` §3.3): given a company domain, it fetches
Apollo's Organization Enrichment and maps the response onto the nine §3.1
firmographic-spine fields, so a probe can COUNT how many come back non-null on
real targets *before any storing adapter is built*. Per §3.3 that measurement
comes first; this module therefore **writes nothing** and knows nothing about the
`outreach_targets` columns beyond their names.

R21 (Apollo terms, §3.2a) is GREEN for retention — but this slice stores nothing
regardless. It measures.

Apollo contract (docs read 2026-08-28):
  GET https://api.apollo.io/api/v1/organizations/enrich?domain=<domain>
  auth: header `x-api-key: <key>`
  body: {"organization": { industry, estimated_num_employees, founded_year,
         total_funding, latest_funding_round_date, latest_funding_stage,
         funding_events:[{investors,...}], city, state, country, raw_address, ... }}
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

APOLLO_ENRICH_URL = "https://api.apollo.io/api/v1/organizations/enrich"
APOLLO_KEY_ITEM = "apollo-api-key"

# The nine §3.1 spine fields, in card order. `sector` predates Part 3 (0013) but
# is measured here like the rest because Apollo populates it.
SPINE_FIELDS: tuple[str, ...] = (
    "sector",
    "headcount",
    "headcount_asof",
    "ownership_type",
    "total_raised_usd",
    "last_round_at",
    "last_round_type",
    "lead_investor",
    "founded_year",
    "hq_location",
)


def normalize_domain(domain: str) -> str:
    """Apollo wants a bare domain — no scheme, no `www.`, no `@` (docs §domain)."""
    d = domain.strip().lower()
    d = d.removeprefix("https://").removeprefix("http://")
    d = d.removeprefix("www.")
    return d.split("/")[0].lstrip("@")


def _hq_location(org: dict) -> str | None:
    """Prefer Apollo's `raw_address`; fall back to city/state/country joined."""
    raw = org.get("raw_address")
    if raw:
        return raw
    parts = [org.get("city"), org.get("state"), org.get("country")]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def _lead_investor(org: dict) -> str | None:
    """First investor of the first funding event that names any.

    Apollo returns `investors` as either a list or a comma-joined string
    depending on the record, so handle both rather than assume one shape.
    """
    for event in org.get("funding_events") or []:
        investors = event.get("investors")
        if not investors:
            continue
        if isinstance(investors, list):
            return investors[0] or None
        return str(investors).split(",")[0].strip() or None
    return None


def _ownership_type(org: dict) -> str | None:
    """Apollo has no direct `ownership_type`. Only `public` is honestly derivable
    (a publicly traded symbol). vc_backed/pe_backed is NOT inferred from a funding
    stage — a seed round is not proof of a VC cap table — so this returns None
    there rather than guessing. The probe will therefore show ownership_type as
    largely Apollo-uncovered, which is the true finding, not a mapping gap."""
    if org.get("publicly_traded_symbol"):
        return "public"
    return None


def map_organization(org: dict) -> dict[str, Any]:
    """Pure: map a raw Apollo organization object onto the nine spine fields.

    A field is None wherever Apollo does not supply it — that None is exactly the
    datum V2 counts. `headcount_asof` is *always* None: Apollo returns no as-of
    date, so the observation date is ours to stamp at storage time (a later
    slice), never Apollo's to report.
    """
    return {
        "sector": org.get("industry"),
        "headcount": org.get("estimated_num_employees"),
        "headcount_asof": None,
        "ownership_type": _ownership_type(org),
        "total_raised_usd": org.get("total_funding"),
        "last_round_at": org.get("latest_funding_round_date"),
        "last_round_type": org.get("latest_funding_stage"),
        "lead_investor": _lead_investor(org),
        "founded_year": org.get("founded_year"),
        "hq_location": _hq_location(org),
    }


# (url, headers) -> raw response bytes. Injectable so mapping and coverage are
# unit-testable with no network and no API key.
Fetcher = Callable[[str, dict[str, str]], bytes]


def _urllib_fetch(url: str, headers: dict[str, str]) -> bytes:
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (https-only literal)
        return resp.read()


def enrich_organization(
    domain: str, api_key: str, *, fetch: Fetcher = _urllib_fetch
) -> dict | None:
    """Fetch Apollo Organization Enrichment for a domain.

    Returns the raw `organization` dict (so callers can both map it and inspect
    the true key set), or None when Apollo returns no organization for the domain.
    """
    query = urllib.parse.urlencode({"domain": normalize_domain(domain)})
    raw = fetch(f"{APOLLO_ENRICH_URL}?{query}", {"x-api-key": api_key})
    return json.loads(raw).get("organization")


def coverage(mapped_rows: list[dict[str, Any]]) -> dict[str, int]:
    """Pure: per spine field, how many rows carry a non-null, non-empty value."""
    counts = dict.fromkeys(SPINE_FIELDS, 0)
    for row in mapped_rows:
        for field in SPINE_FIELDS:
            if row.get(field) not in (None, ""):
                counts[field] += 1
    return counts
