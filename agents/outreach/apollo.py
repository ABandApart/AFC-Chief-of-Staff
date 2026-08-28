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
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

APOLLO_ENRICH_URL = "https://api.apollo.io/api/v1/organizations/enrich"
APOLLO_MATCH_URL = "https://api.apollo.io/api/v1/people/match"
# The FREE company-search endpoint (verified free by barry-agent 2026-08-28). Note
# the docs' "Organization Search" page documents mixed_companies/search, which is
# PAID (403 on Free) — this plural-organizations path is the one Free allows.
APOLLO_SEARCH_URL = "https://api.apollo.io/api/v1/organizations/search"


class ApolloPlanError(RuntimeError):
    """Apollo returned 403 API_INACCESSIBLE — the endpoint is not on the account's
    plan (the People endpoints are paid-only; Organization enrich is on Free). The
    gate sits in front of the endpoint, so nothing was measured. Surfaced as a
    legible message rather than an unhandled HTTPError traceback."""

    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        super().__init__(f"Apollo endpoint not available on this plan: {endpoint}")


class ApolloRateLimitError(RuntimeError):
    """Apollo returned 429 — the request rate cap was hit. Carries the endpoint and
    a best-effort `retry_after` (seconds). Finding this cap is the search probe's
    whole point, so it is a measurement outcome, not an error to hide."""

    def __init__(self, endpoint: str, retry_after: str | None = None):
        self.endpoint = endpoint
        self.retry_after = retry_after
        super().__init__(
            f"Apollo rate limit hit on {endpoint}"
            + (f" (retry after {retry_after}s)" if retry_after else "")
        )


def _fetch_guarding_plan(call: Callable[[], bytes], endpoint: str) -> bytes:
    """Run an Apollo fetch, translating 403 API_INACCESSIBLE → ApolloPlanError and
    429 → ApolloRateLimitError so callers report the gate/cap instead of a stack trace."""
    try:
        return call()
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise ApolloRateLimitError(endpoint, exc.headers.get("Retry-After")) from exc
        if exc.code == 403:
            body = exc.read().decode(errors="replace")
            if "API_INACCESSIBLE" in body:
                raise ApolloPlanError(endpoint) from exc
        raise


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


# (url, headers, body) -> raw response bytes. Separate from Fetcher because the
# People match endpoint is a POST with a JSON body.
PostFetcher = Callable[[str, dict[str, str], bytes], bytes]


def _urllib_post(url: str, headers: dict[str, str], body: bytes) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
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
    url = f"{APOLLO_ENRICH_URL}?{query}"
    raw = _fetch_guarding_plan(lambda: fetch(url, {"x-api-key": api_key}), APOLLO_ENRICH_URL)
    return json.loads(raw).get("organization")


def coverage(mapped_rows: list[dict[str, Any]]) -> dict[str, int]:
    """Pure: per spine field, how many rows carry a non-null, non-empty value."""
    counts = dict.fromkeys(SPINE_FIELDS, 0)
    for row in mapped_rows:
        for field in SPINE_FIELDS:
            if row.get(field) not in (None, ""):
                counts[field] += 1
    return counts


# --- People enrichment (contact lane) ----------------------------------------
# The card gap Apollo was chosen for (§3.2a): contact title, email, LinkedIn
# (§0.3 #5/#6/#9). The probe measures COVERAGE only — it never reveals personal
# emails or phone numbers (reveal flags stay False), so it neither spends credits
# nor unmasks a person's contact detail just to count it. The CLI reports statuses
# and counts, not the raw values.

# Contact fields the People probe measures.
PERSON_FIELDS: tuple[str, ...] = (
    "contact_title",
    "contact_email",
    "email_status",
    "contact_linkedin_url",
)


def map_person(person: dict) -> dict[str, Any]:
    """Pure: map a raw Apollo person object onto the contact fields."""
    return {
        "contact_title": person.get("title"),
        "contact_email": person.get("email"),
        "email_status": person.get("email_status"),
        "contact_linkedin_url": person.get("linkedin_url"),
    }


def email_kind_of(email: str | None) -> str:
    """Classify an Apollo email without revealing it.

    `none`     — Apollo returned no email.
    `locked`   — Apollo HAS an email but returns a placeholder (`…not_unlocked@…`)
                 until a credit is spent to reveal it. The datum exists; the value
                 does not, for free. This is the distinction that decides whether
                 Apollo actually closes card field #6 at this tier or only tells us
                 a deliverability status.
    `revealed` — a real address came back.
    """
    if not email:
        return "none"
    if "not_unlocked" in email:
        return "locked"
    return "revealed"


def match_person(
    name: str, domain: str, api_key: str, *, fetch: PostFetcher = _urllib_post
) -> dict | None:
    """Match one person by full name + employer domain. Returns the raw `person`
    dict, or None when Apollo finds no match.

    Reveal flags are pinned False: a coverage probe must not spend a credit or
    unmask a personal email merely to measure that one exists.
    """
    body = json.dumps(
        {
            "name": name,
            "domain": normalize_domain(domain),
            "reveal_personal_emails": False,
            "reveal_phone_number": False,
        }
    ).encode()
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    raw = _fetch_guarding_plan(lambda: fetch(APOLLO_MATCH_URL, headers, body), APOLLO_MATCH_URL)
    return json.loads(raw).get("person")


def person_coverage(mapped_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure: contact-field coverage over matched people — counts and histograms,
    never raw values. `title`/`linkedin` are non-null counts; `email_kinds` splits
    revealed/locked/none; `email_status` is Apollo's deliverability histogram."""
    email_kinds = {"revealed": 0, "locked": 0, "none": 0}
    statuses: dict[str, int] = {}
    title = linkedin = 0
    for row in mapped_rows:
        if row.get("contact_title"):
            title += 1
        if row.get("contact_linkedin_url"):
            linkedin += 1
        email_kinds[email_kind_of(row.get("contact_email"))] += 1
        status = row.get("email_status") or "null"
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "n": len(mapped_rows),
        "title": title,
        "linkedin": linkedin,
        "email_kinds": email_kinds,
        "email_status": statuses,
    }


# --- Organization search (discovery lane, Free) -------------------------------
# barry-agent found organizations/search is on Free and returns firms by ICP
# filter — a candidate-sourcing channel for Part 0. The open question is not
# whether firms MATCH (~33k did) but how many are RETRIEVABLE on Free: per_page,
# the page-depth ceiling, and the rate limit. `search_organizations` is the one
# request; the pagination WALK that measures those caps lives in the probe CLI.


def search_organizations(
    filters: dict[str, Any], *, page: int, per_page: int, api_key: str,
    fetch: PostFetcher = _urllib_post,
) -> dict:
    """One page of company search. Returns the full parsed response (both the
    `organizations` list and the `pagination` block). Raises ApolloPlanError if the
    endpoint is plan-gated and ApolloRateLimitError on a 429 — both are probe findings."""
    body = json.dumps({**filters, "page": page, "per_page": per_page}).encode()
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    raw = _fetch_guarding_plan(
        lambda: fetch(APOLLO_SEARCH_URL, headers, body), APOLLO_SEARCH_URL
    )
    return json.loads(raw)


def search_page_summary(response: dict) -> dict[str, Any]:
    """Pure: reduce one search response to what the pagination walk records — the
    org count, how many carry a usable `primary_domain` (a domainless row cannot be
    enriched or deduped), those domains, and the raw `pagination` block."""
    orgs = response.get("organizations") or []
    domains = [o.get("primary_domain") for o in orgs if o.get("primary_domain")]
    return {
        "returned": len(orgs),
        "with_domain": len(domains),
        "domains": domains,
        "pagination": response.get("pagination") or {},
    }
