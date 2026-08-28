"""Apollo organizations/search channel — ICP-refined company discovery (Part 0).

A discovery channel (`find(segment) -> [candidate]`, the package contract). Where
`seed_list` is curated by hand and `news_query` mines events, this one pulls firms
by ICP filter from Apollo's Free `organizations/search` and REFINES them to on-ICP
candidates before they become claims.

The refinement (see `config/outreach/discovery/apollo_search.yaml`), grounded in a
live taxonomy probe (2026-08-28):

  * **Query-side gates** — US + the 10–250 headcount band go INTO the query, which
    the probe showed removes essentially all geo/headcount noise at the source.
  * **Industry allowlist** — the residual noise is industry mismatch (recruiters,
    generic IT using our keywords). Apollo's controlled `industry` field is the
    precision lever: a returned firm is kept only if its industry is on the
    segment's allowlist, and `staffing & recruiting` is dropped globally.
  * **Domain required** (R0.10) — a domainless row has no identity to verify or
    dedup on, so it is dropped, exactly as the other channels do.

Precision over recall on purpose: verify.py (two evidence kinds, R0.5) and the
operator at Gate 0 are downstream, so a false drop here is cheaper than a noisy
card. R21 is GREEN for Apollo (§3.2a). This channel is **not registered** in the
package `_CHANNELS` registry yet — the segment→industry mapping is pending operator
confirmation, and enabling a paid-key-less spend-free channel is still a deliberate
act.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from agents._lib import creds
from agents.outreach import apollo

logger = logging.getLogger(__name__)

NAME = "apollo_search"

CONFIG_PATH = (
    Path(__file__).resolve().parents[3]
    / "config" / "outreach" / "discovery" / "apollo_search.yaml"
)

# Bounded by default: this is discovery, not a bulk dump (R21 API-only), and the
# search key shares a 600/day budget. A few pages per tag is plenty to feed the
# daily review window; raise deliberately.
DEFAULT_MAX_PAGES = 3
PER_PAGE = 100


def load_config(path: Path | None = None) -> dict[str, Any]:
    source = path or CONFIG_PATH
    if not source.exists():
        logger.info("discovery: no apollo_search config at %s", source)
        return {}
    loaded = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


def classify(org: dict, segment: str, gates: dict, industries: list[str]) -> dict | None:
    """The refinement gate (pure): an Apollo org row → a candidate for `segment`,
    or None when it fails a gate. Order is cheapest-first.

    Gates: has a domain (R0.10) · country == gates.country · headcount within the
    ICP band · industry not excluded and on the segment allowlist.
    """
    domain = (org.get("primary_domain") or "").strip().lower()
    if not domain:
        return None
    if org.get("country") != gates.get("country"):
        return None

    headcount = org.get("estimated_num_employees")
    lo, hi = gates.get("headcount_min"), gates.get("headcount_max")
    if headcount and lo is not None and hi is not None and not (lo <= headcount <= hi):
        return None

    industry = org.get("industry")
    if industry in (gates.get("exclude_industries") or []):
        return None
    if industries and industry not in industries:
        return None

    name = (org.get("name") or "").strip()
    if not name:
        return None
    return {
        "company_name": name,
        "company_url": f"https://{domain.removeprefix('www.')}",
        "segment": segment,
        "country": "US",
        # A point estimate string; icp.parse_headcount reads it into the band.
        "headcount_band": str(headcount) if headcount else None,
        "hq_location": apollo._hq_location(org),
        "discovered_via": NAME,
        "discovery_query": ", ".join(org.get("keyword_tags_source") or []) or industry or "",
        # Apollo has no per-firm source URL; the org's own site is the citation.
        "source_url": org.get("website_url") or f"https://{domain.removeprefix('www.')}",
    }


def _filters(segment_cfg: dict, gates: dict, tag: str) -> dict[str, Any]:
    filters: dict[str, Any] = {"q_organization_keyword_tags": [tag]}
    lo, hi = gates.get("headcount_min"), gates.get("headcount_max")
    if lo is not None and hi is not None:
        filters["organization_num_employees_ranges"] = [f"{lo},{hi}"]
    if gates.get("country"):
        filters["organization_locations"] = [gates["country"]]
    return filters


def find(segment: str, *, max_pages: int = DEFAULT_MAX_PAGES,
         config: dict | None = None, api_key: str | None = None) -> list[dict[str, Any]]:
    """Candidates for one segment: query each keyword tag, refine, dedup by domain.

    A tag whose request errors (plan gate, rate cap, transport) is logged and
    skipped — one bad tag never fails the segment. Returns [] when the channel is
    unconfigured or the key is absent, so a missing key degrades to no-op rather
    than raising (matching the package's fail-open channel contract).
    """
    cfg = config if config is not None else load_config()
    # Registered but inert until the operator flips `enabled: true` — enabling a
    # channel is a deliberate act (it feeds Gate 0 volume and draws the shared
    # Apollo 600/day budget). Flipping the flag off disables it without touching
    # the registry.
    if not cfg.get("enabled"):
        return []
    seg = (cfg.get("segments") or {}).get(segment)
    if not seg:
        return []
    gates = cfg.get("gates") or {}
    industries = seg.get("industries") or []

    if api_key is None:
        try:
            api_key = creds.keychain_get(apollo.APOLLO_KEY_ITEM)
        except RuntimeError:
            logger.warning("discovery: apollo_search has no '%s'; skipping",
                           apollo.APOLLO_KEY_ITEM)
            return []

    by_domain: dict[str, dict[str, Any]] = {}
    for tag in seg.get("keyword_tags") or []:
        filters = _filters(seg, gates, tag)
        for page in range(1, max_pages + 1):
            try:
                resp = apollo.search_organizations(
                    filters, page=page, per_page=PER_PAGE, api_key=api_key)
            except (apollo.ApolloPlanError, apollo.ApolloRateLimitError) as exc:
                logger.warning("discovery: apollo_search tag %r stopped: %s", tag, exc)
                break
            except OSError as exc:
                logger.warning("discovery: apollo_search tag %r transport error: %s", tag, exc)
                break
            orgs = resp.get("organizations") or []
            if not orgs:
                break
            for org in orgs:
                org["keyword_tags_source"] = [tag]
                candidate = classify(org, segment, gates, industries)
                if candidate:
                    by_domain.setdefault(candidate["company_url"], candidate)
            pagination = resp.get("pagination") or {}
            if pagination.get("total_pages") and page >= pagination["total_pages"]:
                break

    logger.info("discovery: apollo_search %s → %d refined candidate(s)",
                segment, len(by_domain))
    return list(by_domain.values())
