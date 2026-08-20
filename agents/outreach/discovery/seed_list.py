"""Seed-list channel — curated candidates from git (Track O, Part 0 · R0.4).

The workhorse channel, and deliberately so: see the package docstring on why the
three feed-shaped channels in R0.4 cannot produce a name and a domain by
themselves. This one reads `config/outreach/discovery/seeds.yaml`, which lives in
git and is therefore control-plane (B4, trusted).

It carries no judgement. A seed entry is a *claim* that a firm exists and belongs
to a segment; `verify.py` then decides whether it is real enough to surface, and
the operator decides at Gate 0 whether it is worth pursuing. Adding a name here
does not put it in front of anyone.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

NAME = "seed_list"

SEEDS_PATH = (
    Path(__file__).resolve().parents[3]
    / "config" / "outreach" / "discovery" / "seeds.yaml"
)


def load_seeds(path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Read the seed file. A missing or empty file is normal, not an error."""
    source = path or SEEDS_PATH
    if not source.exists():
        logger.info("discovery: no seed file at %s", source)
        return {}
    loaded = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        logger.warning("discovery: %s is not a mapping of segment -> entries", source)
        return {}
    return {k: v or [] for k, v in loaded.items()}


def to_candidate(entry: dict[str, Any], segment: str) -> dict[str, Any] | None:
    """Map one seed entry to a raw candidate, or None if it lacks the minimum.

    Name and URL are the minimum: without a URL there is nothing to verify and no
    domain to dedup on, and a seed that cannot be verified would never surface
    anyway.
    """
    name = (entry.get("name") or "").strip()
    url = (entry.get("url") or "").strip()
    if not (name and url):
        logger.warning("discovery: seed entry in %s missing name or url: %r",
                       segment, entry)
        return None
    return {
        "company_name": name,
        "company_url": url,
        "careers_url": entry.get("careers_url"),
        "company_linkedin_url": entry.get("linkedin"),
        "contact_linkedin_url": entry.get("contact_linkedin"),
        "contact_name": entry.get("contact_name"),
        "contact_title": entry.get("contact_title"),
        "hq_location": entry.get("hq"),
        "headcount_band": entry.get("headcount"),
        "description": entry.get("description"),
        "country": entry.get("country", "US"),
        # Feeds `third_party_dated` in verify.py — a citation the sourcer
        # supplies, never parsed out of prose by keyword.
        "third_party_citation": entry.get("citation"),
        "segment": segment,
        "discovered_via": NAME,
        "discovery_query": f"seeds.yaml:{segment}",
    }


def find(segment: str, path: Path | None = None) -> list[dict[str, Any]]:
    entries = load_seeds(path).get(segment, [])
    candidates = [to_candidate(entry, segment) for entry in entries]
    return [c for c in candidates if c is not None]
