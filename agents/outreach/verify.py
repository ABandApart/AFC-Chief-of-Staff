"""Verification for discovered firms (Track O, Part 0 · R0.5).

A firm is surfaced to the operator only once at least two independent kinds of
evidence say it is real and operational. `35-` §3's discipline, applied one step
earlier than the packet: **a firm shown as verified that is not produces
confident, checkable, wrong outreach**, which is the failure the whole staleness
model exists to prevent.

Every check returns rather than raises, and a check that could not run returns
False with a reason. The asymmetry matters for the same reason it does in the
evidence poller: "could not look" must never read as "nothing there", because
here that would mean silently surfacing an unverified firm.

**Three corrections to R0.5 as written, made because the code cannot honestly do
what the prose claimed.** Each is narrower than the spec, not wider:

  * *`live_site` does not verify recency.* R0.5 said "a reachable site with
    content dated inside 12 months". There is no generic way to date an arbitrary
    homepage - no feed, no reliable metadata, no consistent copyright line - so
    this check verifies **reachability only** and says so. Recency on the imported
    workbook rows is the operator's own judgement, recorded in his verification
    note, not something this module re-derives.
  * *LinkedIn is never fetched.* R0.5 said "an active company LinkedIn URL that
    resolves". R14 is Policy and LinkedIn blocks automated requests; fetching one
    to test it would be both a policy breach and unreliable. The kind is therefore
    `linkedin_url_present` - we record that a URL is **on file**, from the
    company's own site or the operator's list. It is the weakest of the four and
    is named so nobody reads it as more.
  * *`third_party_dated` is not machine-checked.* An award listing or a dated
    press mention is supplied by whoever sourced the firm. Parsing it out of free
    text by keyword would be guessing.

`open_req` is the strongest kind here: it is a live fetch against a structured
API, and it also yields the `careers_url` the evidence poller needs after
promotion.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from agents.outreach import adapters

logger = logging.getLogger(__name__)

USER_AGENT = "aiadaptive-cos-outreach (Track O discovery verifier)"
TIMEOUT_SECONDS = 15

# Kinds, weakest to strongest. Mirrors `_lib.outreach_discovery.VERIFICATION_KINDS`.
LIVE_SITE = "live_site"
LINKEDIN_URL_PRESENT = "linkedin_url_present"
THIRD_PARTY_DATED = "third_party_dated"
OPEN_REQ = "open_req"


@dataclass
class Verification:
    """What held, and what to display for it."""

    kinds: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    careers_url: str | None = None
    provider: str | None = None

    @property
    def note(self) -> str:
        return " · ".join(self.notes)

    def passes(self, minimum: int = 2) -> bool:
        return len(self.kinds) >= minimum


def _reachable(url: str) -> tuple[bool, str]:
    """GET a URL and report whether it answered. Never raises.

    A HEAD would be cheaper, but enough small-business sites answer HEAD with 405
    while serving GET fine that HEAD produces false negatives - and a false
    negative here means a real firm is never surfaced.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            code = getattr(response, "status", None) or response.getcode()
            if 200 <= int(code) < 400:
                return (True, f"site responded {code}")
            return (False, f"site responded {code}")
    except urllib.error.HTTPError as exc:
        return (False, f"site returned HTTP {exc.code}")
    except Exception as exc:  # noqa: BLE001 - a failed probe is data, not a crash
        return (False, f"site unreachable: {type(exc).__name__}")


def check_live_site(company_url: str | None) -> tuple[bool, str]:
    """Reachability only - see the module docstring on why not recency."""
    if not company_url:
        return (False, "no company URL on file")
    ok, detail = _reachable(company_url)
    return (ok, detail)


def check_open_reqs(careers_url: str | None) -> tuple[bool, str, str | None]:
    """A supported ATS board that actually returns at least one role.

    Reuses `adapters.fetch_open_roles` rather than reimplementing detection, so
    the seven providers stay defined in one place. A board that is detected but
    returns zero roles is **not** evidence the firm is operational - an empty
    board is exactly AIIR's situation - so it does not count.
    """
    if not careers_url:
        return (False, "no careers URL on file", None)
    result = adapters.fetch_open_roles(careers_url)
    if not result.ok:
        return (False, f"board not readable: {result.reason}", None)
    if not result.roles:
        return (False, f"{result.provider} board is live but empty", result.provider)
    return (
        True,
        f"{len(result.roles)} open role(s) on a {result.provider} board",
        result.provider,
    )


def check_linkedin_url(url: str | None) -> tuple[bool, str]:
    """Records that a URL is on file. **Never fetches it** - R14 is Policy."""
    if not url:
        return (False, "no company LinkedIn URL on file")
    if "linkedin.com/company/" not in url.lower():
        return (False, "not a company LinkedIn URL")
    return (True, "company LinkedIn URL on file (not fetched)")


def verify(candidate: dict[str, Any], *, fetch: bool = True) -> Verification:
    """Run every check a candidate's fields allow.

    `fetch=False` runs only the offline checks, which is what `--dry-run` uses and
    what keeps the unit tests network-free.
    """
    result = Verification()

    present, detail = check_linkedin_url(candidate.get("company_linkedin_url"))
    if present:
        result.kinds.append(LINKEDIN_URL_PRESENT)
    result.notes.append(detail)

    if candidate.get("third_party_citation"):
        result.kinds.append(THIRD_PARTY_DATED)
        result.notes.append(str(candidate["third_party_citation"])[:200])

    if not fetch:
        return result

    live, detail = check_live_site(candidate.get("company_url"))
    if live:
        result.kinds.append(LIVE_SITE)
    result.notes.append(detail)

    has_reqs, detail, provider = check_open_reqs(candidate.get("careers_url"))
    if has_reqs:
        result.kinds.append(OPEN_REQ)
        result.careers_url = candidate.get("careers_url")
        result.provider = provider
    result.notes.append(detail)

    return result
