"""External dead-man's switch pings (PERF-4 / `80-telemetry-layer.md`).

Every monitor in this system lives on the box it monitors, so nothing can report
a power cut, a full disk, a reboot-loop, or a wedged daemon — the box being off
is not observable from the box. The fix is push-based: each critical loop pings
an external check **on success**, and the *absence* of a ping is the alert. That
is what makes it fire when the box is dead, the network is down, or the process
is wedged — a service that polled the box would see `/health` answer fine while
nothing actually ran.

Alerting lives off-box (healthchecks.io → email/push), never Discord: if the bot
is down, `#system` is exactly where an alert would not appear.

Configuration is a single keychain secret, `healthchecks-ping-key` (the
healthchecks.io project ping key). Each loop pings by its slug — `cos-briefing`,
`cos-backup`, … (see the table in 80-). **Until the key is provisioned this is a
no-op**, so it is safe to wire in ahead of the operator setting it up: the switch
is un-armed, not broken.

Rules baked in (from the spec, and load-bearing):
  - ping() only on the SUCCESS path — never in a `finally:`. A switch that fires
    on a crashed run manufactures false confidence.
  - ping_fail() on a caught exception so a broken run alerts immediately instead
    of waiting out the grace window.
  - a ping NEVER raises: monitoring must not be able to break the work it
    reports on. All errors are swallowed and logged.
"""

from __future__ import annotations

import logging
import urllib.request

from agents._lib import creds

logger = logging.getLogger(__name__)

PING_KEY_ITEM = "healthchecks-ping-key"
_BASE = "https://hc-ping.com"
_TIMEOUT = 5


def _ping_key() -> str | None:
    """The healthchecks.io project ping key, or None if not configured."""
    try:
        return creds.keychain_get(PING_KEY_ITEM)
    except RuntimeError:
        return None


def ping(slug: str, *, fail: bool = False, timeout: int = _TIMEOUT) -> bool:
    """Best-effort dead-man's-switch ping for `slug`. Returns True iff sent.

    A no-op returning False when the ping key isn't configured. Never raises —
    every network/URL error is swallowed and logged, because a monitoring ping
    must not be able to break the loop it reports on.
    """
    key = _ping_key()
    if not key:
        logger.debug(
            "heartbeat: no %s configured — skipping ping for %s", PING_KEY_ITEM, slug
        )
        return False
    url = f"{_BASE}/{key}/{slug}"
    if fail:
        url += "/fail"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            resp.read()  # drain so the connection can be reused/closed cleanly
        return True
    except Exception:
        # Absence of a ping is itself the alert, so a lost ping degrades safely.
        logger.warning("heartbeat: ping failed for slug=%s (fail=%s)", slug, fail)
        return False


def ping_fail(slug: str, *, timeout: int = _TIMEOUT) -> bool:
    """Signal a failed run for `slug` (the /fail endpoint) — alerts immediately."""
    return ping(slug, fail=True, timeout=timeout)
