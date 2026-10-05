"""Deterministic checks a dossier must pass before it can be surfaced.

PRD-outreach-autonomous-sourcing §6.6. Code, not prompts: a model can be talked
into anything by a web page, so nothing a worker says is trusted until these
pass. A failing candidate is still stored, with its failure codes, and is never
surfaced (`outreach_discovery._eligible` excludes any row with failures).
"""

from __future__ import annotations

import urllib.request
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urlsplit

from agents._lib import outreach

# R14: never fetched, never cited, including through search results.
BLOCKED_DOMAINS = ("linkedin.com", "zoominfo.com", "glassdoor.com")

# Second-level suffixes where the registrable domain is three labels. Discovery is
# US-only (D6), so this short list is enough; a public-suffix dependency is not.
_TWO_LABEL_SUFFIXES = frozenset({"co.uk", "org.uk", "ac.uk", "com.au", "co.nz",
                                 "co.jp", "com.br", "co.in", "com.mx"})

MIN_DISTINCT_EVIDENCE_DOMAINS = 2
ALLOWED_COUNTRIES = frozenset({"US", "USA", "UNITED STATES"})


def registrable_domain(url_or_host: str) -> str:
    """The registrable domain of a URL or host, e.g. `blog.acme.com` -> `acme.com` (pure)."""
    text = (url_or_host or "").strip().lower()
    host = urlsplit(text).hostname if "://" in text else text.split("/", 1)[0]
    host = (host or "").split(":", 1)[0].removeprefix("www.").strip(".")
    labels = [p for p in host.split(".") if p]
    if len(labels) <= 2:
        return ".".join(labels)
    if ".".join(labels[-2:]) in _TWO_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def normalize_url(url: str) -> str:
    """A URL comparison key: scheme-less, no fragment, no trailing slash (pure)."""
    parts = urlsplit((url or "").strip())
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{path}{query}"


def is_blocked(url: str) -> bool:
    return registrable_domain(url) in BLOCKED_DOMAINS


def default_site_is_live(domain: str, *, timeout: int = 10) -> bool:
    """True if the company's home page answers 2xx/3xx over HTTPS. Never raises."""
    try:
        req = urllib.request.Request(f"https://{domain}",
                                     headers={"User-Agent": "Mozilla/5.0 (aiadaptive-cos)"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return 200 <= resp.status < 400
    except Exception:
        return False


def evidence_domains(dossier: dict[str, Any]) -> list[str]:
    """Distinct registrable domains among the dossier's evidence URLs (pure)."""
    seen: list[str] = []
    for item in dossier.get("evidence") or []:
        dom = registrable_domain(item.get("url", ""))
        if dom and dom not in seen:
            seen.append(dom)
    return seen


def run_checks(
    dossier: dict[str, Any],
    *,
    seen_urls: Iterable[str],
    known_domains: set[str],
    in_list_segments: set[str],
    has_hypothesis: bool,
    own_discovery_domain: str | None = None,
    site_is_live: Callable[[str], bool] | None = None,
) -> list[str]:
    """Every failed check, as short codes. An empty list means it may be surfaced.

    `seen_urls` are the URLs that actually came back from a search or fetch in
    this worker's run: a cited URL not among them was written from memory (or
    planted by injected text), and is refused (check 3).
    `own_discovery_domain` is set when the brief was to verify an existing
    discovery, so that row's own domain is not counted as a duplicate.
    """
    site_is_live = site_is_live or default_site_is_live
    failures: list[str] = []
    if dossier.get("no_candidate_reason"):
        return ["no_candidate"]

    domain = outreach.normalize_domain(dossier.get("domain") or "")
    # 1. Required content present.
    for field in ("company_name", "summary", "why_now", "segment_key"):
        if not (dossier.get(field) or "").strip():
            failures.append(f"missing_{field}")
    if not domain or "." not in domain:
        failures.append("bad_domain")

    # 2. The company's site is up.
    if domain and "." in domain and not site_is_live(domain):
        failures.append("site_not_live")

    # 3. Two independent cited sources, each actually returned by a tool.
    seen = {normalize_url(u) for u in seen_urls}
    evidence = dossier.get("evidence") or []
    if any(normalize_url(item.get("url", "")) not in seen for item in evidence):
        failures.append("uncited_url")
    if len(evidence_domains(dossier)) < MIN_DISTINCT_EVIDENCE_DOMAINS:
        failures.append("thin_evidence")

    # 4. R14.
    urls = [item.get("url", "") for item in evidence]
    urls += [(dossier.get("contact") or {}).get("source_url") or "",
             (dossier.get("trigger") or {}).get("source_url") or ""]
    if any(u and is_blocked(u) for u in urls):
        failures.append("blocked_source")

    # 5. Not already known.
    if domain and domain in known_domains and domain != own_discovery_domain:
        failures.append("duplicate")

    # 6. Geography (D6: US only).
    if (dossier.get("country") or "").strip().upper() not in ALLOWED_COUNTRIES:
        failures.append("geography")

    # 7. Outside the current list needs a hypothesis (also enforced by 0029).
    if dossier.get("segment_key") not in in_list_segments and not has_hypothesis:
        failures.append("no_hypothesis")

    return failures


def usable_trigger(dossier: dict[str, Any], seen_urls: Iterable[str]) -> dict[str, Any]:
    """The dossier's trigger if it is dated and its source came back from a tool,
    else an empty trigger (pure).

    An unsourced trigger is dropped rather than failing the candidate: the card
    still shows the worker's "why now", and approval promotes on
    `agent_sourced` / `hypothesis_test` instead of an unverified market event.
    """
    trigger = dossier.get("trigger") or {}
    seen = {normalize_url(u) for u in seen_urls}
    if (trigger.get("kind") and trigger.get("date") and trigger.get("source_url")
            and normalize_url(trigger["source_url"]) in seen
            and not is_blocked(trigger["source_url"])):
        return trigger
    return {"kind": None, "date": None, "source_url": None}
