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

So this module speaks to the **ATS JSON APIs directly**. Each supported platform
returns a public, unauthenticated job list carrying a stable per-posting id.
Deterministic, free, no key, no scraping.

**Seven platforms (increment 1b, 2026-08-14).** The original three —
Greenhouse, Lever, Ashby — are where venture-backed startups host boards, which
is the population `35-` was written around. The first real target list was
mostly *not* that population, and ten of fourteen targets were unreachable. Four
more platforms were added after probing every host in that list:

| Platform | Feed | Board id from |
|----------|------|---------------|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/<t>/jobs` | path |
| Lever | `api.lever.co/v0/postings/<t>?mode=json` | path |
| Ashby | `api.ashbyhq.com/posting-api/job-board/<t>` | path |
| Workable | `apply.workable.com/api/v1/widget/accounts/<t>?details=true` | path |
| BambooHR | `<t>.bamboohr.com/careers/list` | **host** |
| TeamTailor | `<t>.teamtailor.com/jobs.json` (JSON Feed 1.1) | **host** |
| Rippling | `ats.rippling.com/api/v1/board/<t>/jobs` | path |

**Deliberately still unsupported**, because probing found no public JSON feed:
Hireology (HTML only), BreatheHR (its `.json` route returns 401 — authenticated),
and SaaSHR/UKG (HTML only). A careers URL on any unrecognised host is **reported
as unsupported, not silently skipped** — an operator who pastes a marketing
careers page should find out that no evidence will accrue for that target while
there is still time to find its real board, rather than discovering an empty
packet weeks later.

**Posting dates are captured but never trusted as `first_seen_at`.** Workable and
TeamTailor both expose the board's own published date, which is close to the
backfill `35-` §6 suggests *buying* from TheirStack. It rides along in the
payload as `posted_at`, and `first_seen_at` remains strictly our own observation
— a provider's date can silently reset when a posting is edited or reposted, and
mixing the two would make "open 56 days" mean different things on different rows.
Whether the packet's arithmetic should prefer `posted_at` when present is a
question for the packet spec, not something to decide silently in a poller.

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
    if host.endswith("workable.com"):
        # apply.workable.com/<account>. `/j/<shortcode>` is a single posting, not
        # a board — skipping "j" would silently turn one job into a whole board.
        if "j" in segments[:1]:
            return None
        token = _first_segment(skip=("api", "v1", "widget", "accounts"))
        return ("workable", token) if token else None
    # The next two identify the board by SUBDOMAIN, not by a path segment.
    if host.endswith(".bamboohr.com"):
        # <account>.bamboohr.com/careers
        token = host.removesuffix(".bamboohr.com")
        return ("bamboohr", token) if token else None
    if host.endswith(".teamtailor.com"):
        # <account>.teamtailor.com — the account part may itself contain dots
        # (e.g. "salesgravy-1748472865.na"), so keep the whole prefix.
        token = host.removesuffix(".teamtailor.com")
        return ("teamtailor", token) if token else None
    if host.endswith("rippling.com"):
        # ats.rippling.com/<company>/jobs
        token = _first_segment(skip=("api", "v1", "board"))
        return ("rippling", token) if token else None
    return None


def board_api_url(provider: str, token: str) -> str:
    """The public JSON endpoint for a detected board (pure)."""
    if provider == "greenhouse":
        return f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    if provider == "lever":
        return f"https://api.lever.co/v0/postings/{token}?mode=json"
    if provider == "ashby":
        return f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    if provider == "workable":
        return f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true"
    if provider == "bamboohr":
        return f"https://{token}.bamboohr.com/careers/list"
    if provider == "teamtailor":
        return f"https://{token}.teamtailor.com/jobs.json"
    if provider == "rippling":
        return f"https://ats.rippling.com/api/v1/board/{token}/jobs"
    raise ValueError(f"unknown board provider: {provider!r}")


# --- response parsing (pure) --------------------------------------------------
# Each parser returns role dicts in ONE shape, so the poller never branches on
# provider. `external_id` is the ATS's own posting id — the stable identity that
# makes `first_seen_at` meaningful across polls.


def _role(external_id: object, title: object, url: object,
          location: object = None, team: object = None,
          posted_at: object = None) -> dict[str, Any] | None:
    """One normalized role, or None when the posting has no usable identity.

    `posted_at` is the board's own claimed publish date where it offers one. It
    is carried for display, never used as `first_seen_at` — see the module
    docstring.
    """
    if external_id in (None, "") or not str(title or "").strip():
        return None
    return {
        "external_id": str(external_id),
        "title": str(title).strip(),
        "url": str(url).strip() if url else None,
        "location": str(location).strip() if location else None,
        "team": str(team).strip() if team else None,
        "posted_at": str(posted_at).strip()[:10] if posted_at else None,
    }


def _join(*parts: object) -> str | None:
    """Join present location parts into one short string (pure)."""
    present = [str(p).strip() for p in parts if p and str(p).strip()]
    return ", ".join(present) or None


def parse_greenhouse(payload: dict[str, Any], token: str = "") -> list[dict[str, Any]]:
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


def parse_lever(payload: list[dict[str, Any]], token: str = "") -> list[dict[str, Any]]:
    roles = []
    for job in payload or []:
        categories = job.get("categories") or {}
        if role := _role(job.get("id"), job.get("text"), job.get("hostedUrl"),
                         categories.get("location"), categories.get("team")):
            roles.append(role)
    return roles


def parse_ashby(payload: dict[str, Any], token: str = "") -> list[dict[str, Any]]:
    roles = []
    for job in payload.get("jobs") or []:
        if role := _role(job.get("id"), job.get("title"), job.get("jobUrl"),
                         job.get("location"), job.get("department")):
            roles.append(role)
    return roles


def parse_workable(payload: dict[str, Any], token: str = "") -> list[dict[str, Any]]:
    """Workable's widget feed. `shortcode` is the stable posting id."""
    roles = []
    for job in payload.get("jobs") or []:
        if role := _role(
            job.get("shortcode"), job.get("title"), job.get("url"),
            _join(job.get("city"), job.get("state"), job.get("country")),
            job.get("department"), job.get("published_on"),
        ):
            roles.append(role)
    return roles


def parse_bamboohr(payload: dict[str, Any], token: str = "") -> list[dict[str, Any]]:
    """BambooHR's careers list.

    The payload carries **no job URL**, so it is reconstructed from the board
    token and the posting id — which is why every parser takes `token`.
    """
    roles = []
    for job in payload.get("result") or []:
        job_id = job.get("id")
        location = job.get("location") or {}
        url = f"https://{token}.bamboohr.com/careers/{job_id}" if token and job_id else None
        if role := _role(
            job_id, job.get("jobOpeningName"), url,
            _join(location.get("city"), location.get("state")),
            job.get("departmentLabel"),
        ):
            roles.append(role)
    return roles


def parse_teamtailor(payload: dict[str, Any], token: str = "") -> list[dict[str, Any]]:
    """TeamTailor publishes JSON Feed 1.1.

    `content_html` carries the entire job description and is deliberately NOT
    read: H1 keeps evidence to short bounded fields, and a page-sized blob in a
    payload is exactly what that control exists to prevent.
    """
    roles = []
    for item in payload.get("items") or []:
        if role := _role(item.get("id"), item.get("title"), item.get("url"),
                         posted_at=item.get("date_published")):
            roles.append(role)
    return roles


def parse_rippling(payload: list[dict[str, Any]], token: str = "") -> list[dict[str, Any]]:
    roles = []
    for job in payload or []:
        department = job.get("department") or {}
        work_location = job.get("workLocation") or {}
        if role := _role(job.get("uuid"), job.get("name"), job.get("url"),
                         work_location.get("label"), department.get("label")):
            roles.append(role)
    return roles


_PARSERS = {
    "greenhouse": parse_greenhouse,
    "lever": parse_lever,
    "ashby": parse_ashby,
    "workable": parse_workable,
    "bamboohr": parse_bamboohr,
    "teamtailor": parse_teamtailor,
    "rippling": parse_rippling,
}


def parse_board(provider: str, payload: object, token: str = "") -> list[dict[str, Any]]:
    """Dispatch to the provider's parser (pure).

    `token` is only consumed by BambooHR (to rebuild job URLs its payload omits),
    but every parser accepts it so the dispatch stays uniform.
    """
    parser = _PARSERS.get(provider)
    if parser is None:
        raise ValueError(f"unknown board provider: {provider!r}")
    return parser(payload, token)  # type: ignore[arg-type]


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
            # The board's own claimed publish date, where it offers one. Display
            # only — `first_seen_at` stays our observation (module docstring).
            "posted_at": role.get("posted_at"),
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
        roles = parse_board(provider, payload, token)
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
