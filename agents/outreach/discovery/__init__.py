"""Discovery channels — how a candidate firm is found (Track O, Part 0 · R0.4).

Each channel exposes one function:

    find(segment: str) -> list[dict]

returning raw candidates with at minimum `company_name` and `company_url`, plus
whatever else it can establish. Every candidate carries `discovered_via` and
`discovery_query` so no pool row exists without provenance (Part 0 outcome 2).
Adding a channel is adding a file and one registry entry.

WHAT THE SPEC ASSUMED, AND WHAT THE APIS ACTUALLY DO
----------------------------------------------------
R0.4 names four channels. Building them established that **three of the four
cannot produce a firm's name and domain on their own**, which is narrower than
the spec implied and is recorded here rather than papered over with parsers that
would quietly emit garbage:

  * **ATS board enumeration does not exist.** R0.4 says "a firm with an open req
    on a supported board is discoverable". It is not. Greenhouse, Lever, Ashby
    and the rest expose a board *given a token*; none publishes an index of
    boards, so there is no query that returns companies. The seven adapters
    **verify** a firm you already have - which is real value, and is what
    `verify.check_open_reqs` uses them for - but they cannot find one.
  * **Google News by segment returns stories, not firms.** The RSS feed gives a
    headline, a link and a date. Turning "boutique L&D firm raises Series A" into
    a company name and a domain is entity extraction and resolution. Without it,
    a news channel emits publisher domains instead of subject companies.
  * **Award lists and directories are real but bespoke.** Each is an HTML page
    needing its own parser, published annually, and each carries its own terms
    that have to be read before it is fetched. High precision, low volume, and
    not a generic channel.

So the honest position: **finding boutique firms at this size is a research
problem, not a fetch problem** - which is also how the operator's own 100 rows
came to exist. `seed_list` is therefore the workhorse, and it is where a research
pass (human or LLM-assisted) deposits its results. The news and award channels
become buildable the moment an entity-extraction step is sanctioned; the
interface below does not change when they are.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

from agents.outreach.discovery import seed_list

logger = logging.getLogger(__name__)


class Channel(Protocol):
    NAME: str

    def find(self, segment: str) -> list[dict[str, Any]]:
        ...


# Registry. Order is stable so a run's output is reproducible.
CHANNELS: dict[str, Callable[[str], list[dict[str, Any]]]] = {
    seed_list.NAME: seed_list.find,
}


def find_all(segment: str) -> list[dict[str, Any]]:
    """Every channel's candidates for one segment, deduped on company_url.

    A channel that raises is logged and skipped rather than failing the run: one
    broken source must not stop the others, the same posture `evidence.poll`
    takes per target.
    """
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, find in CHANNELS.items():
        try:
            candidates = find(segment)
        except Exception:
            logger.exception("discovery channel %s failed for segment %s", name, segment)
            continue
        for candidate in candidates:
            key = (candidate.get("company_url") or "").strip().lower().rstrip("/")
            if not key or key in seen:
                continue
            seen.add(key)
            found.append(candidate)
    return found
