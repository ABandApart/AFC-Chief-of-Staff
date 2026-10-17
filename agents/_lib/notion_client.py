"""Notion REST API client (`PRD-notion-integration.md`, increment N1).

Thin, dependency-free client over the Notion API (`https://api.notion.com/v1`),
following `granola_client.py`: stdlib `urllib`, the token passed in by the
caller (read once from keychain `notion-token`), and the retry/throttle logic
injectable so it unit-tests without network.

Pinned to **`Notion-Version: 2026-03-11`** (confirmed latest against the docs
2026-10-16). That version uses `in_trash` (not `archived`), a `position` object
for block placement (supports `start`), and the 2025-09-03 split of databases
(containers) from data sources (tables) — search filters on `page` or
`data_source`. A version bump is a code change, never a header someone forgets.

Limits (docs, 2026-10-16): 180 requests/minute (about 3/s) per connection on
non-Business plans; 429 *and* 529 carry `Retry-After`; 100 elements per array;
2000 characters per rich-text item. `MIN_INTERVAL` keeps one process under the
rate on its own; the retry handles the rest.

The integration is internal: it sees only pages shared with it (the
"AFC Richmond Chief-of-Staff" page and its children).
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

logger = logging.getLogger(__name__)

BASE_URL = "https://api.notion.com/v1"
NOTION_VERSION = "2026-03-11"
KEYCHAIN_ITEM = "notion-token"

MIN_INTERVAL = 0.35  # seconds between requests: ~2.9/s, under the 3/s average
MAX_RETRIES = 5
PAGE_SIZE = 100  # search/query page size; also the API's array cap
RETRYABLE = (429, 529)


class NotionError(Exception):
    """A Notion request failed (non-2xx after retries, or transport error).

    `status` and `code` carry Notion's HTTP status and error code (for example
    401 `unauthorized`, 404 `object_not_found`) when the API returned them.
    """

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


def build_request(
    method: str, path: str, *, token: str, body: dict[str, Any] | None = None
) -> urllib.request.Request:
    """The urllib request for one call (pure — no network)."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Accept": "application/json",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    return urllib.request.Request(f"{BASE_URL}{path}", data=data, headers=headers, method=method)


def retry_delay(
    retry_after: str | None, attempt: int, *, _jitter: Callable[[], float] = random.random
) -> float:
    """Seconds to wait before retry `attempt` (0-based).

    Honors `Retry-After` as the floor, then backs off exponentially with jitter on
    repeats — the Notion docs' instruction for 429/529.
    """
    try:
        floor = float(retry_after) if retry_after is not None else 0.0
    except ValueError:
        floor = 0.0
    backoff = (2 ** attempt) * (1.0 + _jitter())
    return max(floor, 1.0) if attempt == 0 else max(floor, backoff)


def _error_from(exc: urllib.error.HTTPError, method: str, path: str) -> NotionError:
    code = None
    detail = exc.reason
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        code = payload.get("code")
        detail = payload.get("message") or detail
    except (ValueError, OSError):
        pass
    return NotionError(f"{method} {path} → {exc.code} {code or ''}: {detail}".strip(),
                       status=exc.code, code=code)


class NotionClient:
    """One client per process. Thread-safe throttle, so the Discord bot's
    executor threads share one rate budget."""

    def __init__(
        self,
        token: str,
        *,
        _opener: Callable[..., Any] = urllib.request.urlopen,
        _sleep: Callable[[float], None] = time.sleep,
        _clock: Callable[[], float] = time.monotonic,
    ):
        self._token = token
        self._open = _opener
        self._sleep = _sleep
        self._clock = _clock
        self._lock = threading.Lock()
        self._last = float("-inf")

    @classmethod
    def from_keychain(cls) -> NotionClient:
        from agents._lib import creds

        return cls(creds.keychain_get(KEYCHAIN_ITEM))

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last + MIN_INTERVAL - self._clock()
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """One API call with throttle and 429/529 retry. Returns the JSON body."""
        for attempt in range(MAX_RETRIES):
            self._throttle()
            req = build_request(method, path, token=self._token, body=body)
            try:
                with self._open(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in RETRYABLE and attempt < MAX_RETRIES - 1:
                    delay = retry_delay(e.headers.get("Retry-After"), attempt)
                    logger.warning(
                        "notion %s on %s %s — backing off %.1fs", e.code, method, path, delay
                    )
                    self._sleep(delay)
                    continue
                raise _error_from(e, method, path) from e
            except urllib.error.URLError as e:
                raise NotionError(f"{method} {path} failed: {e.reason}") from e
        raise NotionError(f"{method} {path} still rate-limited after {MAX_RETRIES} attempts",
                          status=429, code="rate_limited")

    # --- endpoints used by N1; later increments add pages/blocks/data sources ---

    def me(self) -> dict[str, Any]:
        """The integration's bot user — the cheapest authenticated call."""
        return self.request("GET", "/users/me")

    def iter_search(
        self, *, query: str = "", object_type: str | None = None
    ) -> Iterator[dict[str, Any]]:
        """Every page or data source shared with the integration, following cursors.

        `object_type`: None for both, or "page" / "data_source".
        """
        body: dict[str, Any] = {"page_size": PAGE_SIZE}
        if query:
            body["query"] = query
        if object_type:
            body["filter"] = {"property": "object", "value": object_type}
        while True:
            resp = self.request("POST", "/search", body)
            yield from resp.get("results", [])
            if not resp.get("has_more") or not resp.get("next_cursor"):
                return
            body["start_cursor"] = resp["next_cursor"]


def title_of(obj: dict[str, Any]) -> str:
    """Plain-text title of a page or data source (pure).

    A page's title lives in whichever property has type `title`; a data source
    carries a top-level `title` rich-text array.
    """
    if obj.get("object") == "data_source":
        parts = obj.get("title") or []
    else:
        parts = next(
            (p.get("title") or [] for p in (obj.get("properties") or {}).values()
             if p.get("type") == "title"),
            [],
        )
    return "".join(t.get("plain_text", "") for t in parts).strip() or "(untitled)"


def content_hash(payload: Any) -> str:
    """Stable hash of a projection's content, for `notion_sync.content_hash`.

    Key order and whitespace never change the hash, so an unchanged projection
    is skipped instead of re-pushed.
    """
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
