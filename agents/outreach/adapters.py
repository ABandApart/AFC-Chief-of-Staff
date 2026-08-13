"""Job-board adapters — the observed-evidence source (Track O, `35-` §6).

The evidence poller needs **typed open-role facts with a stable identity across
polls**, because `first_seen_at` is the datum the whole method rests on (T10's
posting-date mechanic, S4's "posted 45+ days" band) and no provider reliably
sells it. That rules out two obvious approaches:

  - **Scraping a rendered careers page.** Generic HTML gives no stable per-role
    id, so `dedup_key` would drift with any layout change and every poll would
    look like a brand-new req — resetting posting age to zero, silently, forever.
  - **Asking an LLM to read the page.** Outreach is deliberately LLM-free outside
    Trent Crimm (`40-action-layer.md`): the loops must not fail from a provider
    outage, and a non-deterministic extractor cannot produce a stable dedup key.

So this module speaks to the **ATS JSON APIs directly** — Greenhouse, Lever, and
Ashby, which is where seed/Series-A companies (exactly the ICP) host their
boards. Each returns a documented, public, unauthenticated job list with a
stable per-posting id. Deterministic, free, no key, no scraping.

A careers URL on an unrecognised host is **reported as unsupported, not silently
skipped** — an operator who pastes a Notion careers page should find out that no
evidence will accrue for that target while there is still time to find its real
board, rather than discovering an empty packet weeks later.

Network calls use stdlib `urllib` (as `_lib/granola_client.py` does) — no new
dependency for three JSON GETs.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

USER_AGENT = "aiadaptive-cos-outreach (Track O evidence poller)"
TIMEOUT_SECONDS = 20

# Bounds one target's contribution per poll. A board with hundreds of reqs is a
# company far outside the ICP (seed / Series A), so the cap costs nothing real
# and stops one outlier dominating a cycle.
MAX_ROLES_PER_TARGET = 60


@dataclass
class BoardResult:
    """One board poll's outcome.

    `ok` is the load-bearing field: `close_absent_evidence` may only run when the
    adapter actually parsed a response. Without it, a 500 or a moved board would
    read as "zero open roles" and close every req the target has.
    """

    ok: bool
    roles: list[dict[str, Any]] = field(default_factory=list)
    provider: str | None = None
    reason: str | None = None          # why ok is False


# --- board detection (pure) ---------------------------------------------------


def detect_board(careers_url: str | None) -> tuple[str, str] | None:
    """Map a careers URL → (provider, board_token), or None if unrecognised.

    Pure — unit-tested. Handles the hosted board URLs and the API hosts alike,
    since operators paste whichever one they happened to be looking at.
    """
    if not careers_url:
        return None
    url = careers_url.strip().lower().split("#", 1)[0]
    for prefix in ("https://", "http://"):
        if url.startswith(prefix):
            url = url[len(prefix):]
    url = url.removeprefix("www.")
    url, _, query = url.partition("?")
    url = url.rstrip("/")
    if not url:
        return None
    host, _, path = url.partition("/")
    segments = [s for s in path.split("/") if s]
    params = dict(
        pair.split("=", 1) for pair in query.split("&") if "=" in pair
    )

    def _first_segment(skip: tuple[str, ...] = ()) -> str | None:
        for seg in segments:
            if seg not in skip:
                return seg
        return None

    if host.endswith("greenhouse.io"):
        # boards.greenhouse.io/<token>, job-boards.greenhouse.io/<token>, and the
        # embed form boards.greenhouse.io/embed/job_board?for=<token> — which
        # carries the token in the QUERY STRING, not the path. Operators paste
        # whichever URL the company's careers page happens to iframe.
        if token := params.get("for"):
            return ("greenhouse", token)
        token = _first_segment(skip=("embed", "job_board"))
        return ("greenhouse", token) if token else None
    if host.endswith("lever.co"):
        # jobs.lever.co/<company>
        token = _first_segment()
        return ("lever", token) if token else None
    if host.endswith("ashbyhq.com"):
        # jobs.ashbyhq.com/<name>
        token = _first_segment(skip=("posting-api", "job-board"))
        return ("ashby", token) if token else None
    return None


def board_api_url(provider: str, token: str) -> str:
    """The public JSON endpoint for a detected board (pure)."""
    if provider == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    if provider == "lever":
        return f"https://api.lever.co/v0/postings/{token}?mode=json"
    if provider == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    raise ValueError(f"unknown board provider: {provider!r}")


# --- response parsing (pure) --------------------------------------------------
# Each parser returns role dicts in ONE shape, so the poller never branches on
# provider. `external_id` is the ATS's own posting id — the stable identity that
# makes `first_seen_at` meaningful across polls.


def _role(external_id: object, title: object, url: object,
          location: object = None, team: object = None) -> dict[str, Any] | None:
    """One normalized role, or None when the posting has no usable identity."""
    if external_id in (None, "") or not str(title or "").strip():
        return None
    return {
        "external_id": str(external_id),
        "title": str(title).strip(),
        "url": str(url).strip() if url else None,
        "location": str(location).strip() if location else None,
        "team": str(team).strip() if team else None,
    }


def parse_greenhouse(payload: dict[str, Any]) -> list[dict[str, Any]]:
    roles = []
    for job in payload.get("jobs") or []:
        raw_location = job.get("location")
        location = raw_location.get("name") if isinstance(raw_location, dict) else raw_location
        departments = job.get("departments") or []
        first_dept = departments[0] if departments else None
        team = first_dept.get("name") if isinstance(first_dept, dict) else None
        if role := _role(job.get("id"), job.get("title"),
                         job.get("absolute_url"), location, team):
            roles.append(role)
    return roles


def parse_lever(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    roles = []
    for job in payload or []:
        categories = job.get("categories") or {}
        if role := _role(job.get("id"), job.get("text"), job.get("hostedUrl"),
                         categories.get("location"), categories.get("team")):
            roles.append(role)
    return roles


def parse_ashby(payload: dict[str, Any]) -> list[dict[str, Any]]:
    roles = []
    for job in payload.get("jobs") or []:
        if role := _role(job.get("id"), job.get("title"), job.get("jobUrl"),
                         job.get("location"), job.get("department")):
            roles.append(role)
    return roles


_PARSERS = {
    "greenhouse": parse_greenhouse,
    "lever": parse_lever,
    "ashby": parse_ashby,
}


def parse_board(provider: str, payload: object) -> list[dict[str, Any]]:
    """Dispatch to the provider's parser (pure)."""
    parser = _PARSERS.get(provider)
    if parser is None:
        raise ValueError(f"unknown board provider: {provider!r}")
    return parser(payload)  # type: ignore[arg-type]


# --- fact mapping (pure) ------------------------------------------------------


def role_to_fact(role: dict[str, Any], provider: str) -> dict[str, Any]:
    """Map a normalized role → the evidence fact the shared core stores (pure).

    `dedup_key` is provider-scoped so a company that migrates ATS starts a fresh
    identity rather than colliding ids with its old board. Payload fields are
    short and typed (H1); the excerpt is the title, which is all a packet needs
    to display and all a hostile posting gets to say.
    """
    return {
        "fact_kind": "open_role",
        "dedup_key": f"{provider}:{role['external_id']}",
        "payload": {
            "title": role["title"],
            "location": role.get("location"),
            "team": role.get("team"),
            "url": role.get("url"),
        },
        "source_kind": "careers_page",
        "source_url": role.get("url"),
        "source_excerpt": role["title"],
    }


# --- network ------------------------------------------------------------------


def _get_json(url: str) -> object:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_open_roles(careers_url: str | None) -> BoardResult:
    """Fetch one target's open roles. Never raises — returns `ok=False` instead.

    Every failure path keeps `ok=False` so the caller cannot mistake "we could not
    look" for "there is nothing there", which is the difference between an
    unchanged evidence table and one that closes every req at once.
    """
    detected = detect_board(careers_url)
    if detected is None:
        return BoardResult(
            ok=False,
            reason=(
                "unsupported careers URL — no Greenhouse/Lever/Ashby board detected; "
                "no posting-age evidence will accrue for this target"
            ),
        )
    provider, token = detected
    try:
        payload = _get_json(board_api_url(provider, token))
        roles = parse_board(provider, payload)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return BoardResult(ok=False, provider=provider, reason=f"fetch failed: {e}")
    except (ValueError, TypeError, KeyError, AttributeError) as e:
        # Malformed/renamed payload. Explicitly NOT ok — see BoardResult.ok.
        return BoardResult(ok=False, provider=provider, reason=f"parse failed: {e}")

    if len(roles) > MAX_ROLES_PER_TARGET:
        logger.warning(
            "outreach: %s board %s returned %d roles; capping at %d",
            provider, token, len(roles), MAX_ROLES_PER_TARGET,
        )
        roles = roles[:MAX_ROLES_PER_TARGET]
    return BoardResult(ok=True, roles=roles, provider=provider)
