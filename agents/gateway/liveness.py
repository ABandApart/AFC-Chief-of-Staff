"""Gateway and tunnel dead-man's switches (`cos-gateway`, `cos-tunnel`).

PRD-liveness-alerting §2. A background thread in the gateway process that, every
5 minutes:

  - calls the gateway's own `/health` over local HTTP and pings `cos-gateway` on
    a 200. Going over HTTP (not pinging from inside a handler) proves the server
    is accepting connections, not only that the process is alive;
  - calls `/health` on the public hostname, through Cloudflare and back, and pings
    `cos-tunnel` on a 200. This is the only check that sees a broken tunnel — the
    failure that went unnoticed from about 2026-09-10.

If the gateway is down, both checks go silent; if only the tunnel is down, only
`cos-tunnel` does. The pair names the failure.

The public URL is the optional keychain item `gateway-public-url` (for example
`https://<hostname>`). It is kept out of git like the hostname in PRD-b3. While it
is absent, the tunnel probe is skipped and says so once — un-armed, not broken.

Started from `app.main()`, never at import, so tests that import the app do not
start a thread.
"""

from __future__ import annotations

import logging
import threading
import urllib.request

from agents._lib import creds, heartbeat

logger = logging.getLogger(__name__)

GATEWAY_SLUG = "cos-gateway"
TUNNEL_SLUG = "cos-tunnel"
PUBLIC_URL_ITEM = "gateway-public-url"
BEAT_SECONDS = 300
PROBE_TIMEOUT = 10


def health_ok(base_url: str, *, timeout: int = PROBE_TIMEOUT) -> bool:
    """True iff `GET <base_url>/health` returns 200. Never raises."""
    url = f"{base_url.rstrip('/')}/health"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "aiadaptive-cos-liveness"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            resp.read()
            return resp.status == 200
    except Exception:
        logger.warning("liveness: %s did not answer 200", url)
        return False


def public_url() -> str | None:
    """The tunnel's public base URL, or None while it is not configured."""
    try:
        return creds.keychain_get(PUBLIC_URL_ITEM)
    except RuntimeError:
        return None


class Prober:
    """One probe cycle per `beat()`; state only for the one-time un-armed log."""

    def __init__(self, local_url: str):
        self.local_url = local_url
        self._warned_no_public_url = False

    def beat(self) -> dict[str, bool]:
        """Probe both paths and ping each check that passed.

        Returns {slug: passed}. A failed probe sends nothing: the silence is the
        alert. Never raises.
        """
        results: dict[str, bool] = {}

        results[GATEWAY_SLUG] = health_ok(self.local_url)
        if results[GATEWAY_SLUG]:
            heartbeat.ping(GATEWAY_SLUG)

        url = public_url()
        if url is None:
            if not self._warned_no_public_url:
                logger.warning(
                    "liveness: no %s in keychain — %s probe skipped (un-armed)",
                    PUBLIC_URL_ITEM, TUNNEL_SLUG,
                )
                self._warned_no_public_url = True
            results[TUNNEL_SLUG] = False
        else:
            results[TUNNEL_SLUG] = health_ok(url)
            if results[TUNNEL_SLUG]:
                heartbeat.ping(TUNNEL_SLUG)

        return results


def start(local_url: str, *, interval: int = BEAT_SECONDS) -> threading.Event:
    """Start the probe thread. Returns an Event that stops it when set.

    The first probe waits one short interval so uvicorn is listening before the
    gateway probes itself.
    """
    stop = threading.Event()
    prober = Prober(local_url)

    def _run() -> None:
        if stop.wait(timeout=15):
            return
        while True:
            try:
                prober.beat()
            except Exception:  # belt and braces: the thread must not die
                logger.exception("liveness: probe cycle failed")
            if stop.wait(timeout=interval):
                return

    threading.Thread(target=_run, name="gateway-liveness", daemon=True).start()
    return stop
