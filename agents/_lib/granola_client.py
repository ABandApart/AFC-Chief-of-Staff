"""Granola public REST API client (Track C — meeting ingest).

Thin, dependency-free client over Granola's public API
(`https://public-api.granola.ai/v1`, confirmed against the docs 2026-07-29):

  - `GET /v1/notes` (List Notes) — paginated by `cursor`/`hasMore`, filterable by
    `updated_after`. Returns note *summaries* (id, title, timestamps, owner) —
    NOT the body/transcript. List only surfaces notes that already have a
    generated summary + transcript.
  - `GET /v1/notes/{id}?include=transcript` (Get Note) — the full note:
    `summary_markdown`/`summary_text`, `attendees`, `calendar_event`, and a
    `transcript` array of `{speaker{name,…}, text, start_time, end_time}`.

Auth is a personal API key (`Authorization: Bearer grn_…`), minted in the Granola
desktop app and stored in the keychain as `granola-api-key`. The token is passed
in by the caller (the poller reads it once) so these functions stay pure/testable.

HTTP via stdlib `urllib` (same as `agents/briefing/run.py`) — no new dependency.
Rate limits are ~25 req/5s burst, 5 req/s sustained; `_get` backs off on 429.

The text-assembly (`assemble_note_text`) and pagination logic are pure so they
unit-test without network. ⚠️ Field names are taken from the published schema;
confirm against one real response at runtime (dump a note's JSON) before trusting
edge cases — same posture we took confirming the cognee API.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

BASE_URL = "https://public-api.granola.ai/v1"
PAGE_SIZE = 30  # API max (1–30)


class GranolaError(Exception):
    """A Granola API request failed (non-2xx after retries, or transport error)."""


def _get(
    path: str,
    *,
    token: str,
    params: dict[str, Any] | None = None,
    max_retries: int = 4,
    _sleep: Any = time.sleep,
) -> dict[str, Any]:
    """GET a JSON endpoint with Bearer auth. Retries 429 with backoff.

    `_sleep` is injectable so tests can exercise the backoff path without waiting.
    """
    url = f"{BASE_URL}{path}"
    if params:
        query = {k: v for k, v in params.items() if v is not None}
        if query:
            url = f"{url}?{urlencode(query)}"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
    )
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                retry_after = _retry_after_seconds(e.headers, attempt)
                logger.warning("granola 429 on %s — backing off %.1fs", path, retry_after)
                _sleep(retry_after)
                continue
            raise GranolaError(f"GET {path} → {e.code} {e.reason}") from e
        except urllib.error.URLError as e:
            raise GranolaError(f"GET {path} failed: {e.reason}") from e
    raise GranolaError(f"GET {path} still rate-limited after {max_retries} attempts")


def _retry_after_seconds(headers: Any, attempt: int) -> float:
    """Honor a `Retry-After` header if present; else exponential backoff (pure)."""
    raw = headers.get("Retry-After") if headers else None
    if raw:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    return float(2**attempt)


def list_notes(
    token: str, *, updated_after: str | None = None, cursor: str | None = None
) -> dict[str, Any]:
    """One page of List Notes. Returns the raw `{notes, hasMore, cursor}` dict."""
    return _get(
        "/notes",
        token=token,
        params={"updated_after": updated_after, "cursor": cursor, "page_size": PAGE_SIZE},
    )


def iter_note_summaries(token: str, *, updated_after: str | None = None) -> list[dict[str, Any]]:
    """All note summaries updated after `updated_after`, following the cursor.

    Returned oldest-first (by `updated_at`) so a caller can advance a watermark
    monotonically and re-run safely after an interruption.
    """
    collected: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        page = list_notes(token, updated_after=updated_after, cursor=cursor)
        collected.extend(page.get("notes") or [])
        cursor = page.get("cursor")
        if not page.get("hasMore") or not cursor:
            break
    collected.sort(key=lambda n: n.get("updated_at") or "")
    return collected


def get_note(token: str, note_id: str, *, include_transcript: bool = True) -> dict[str, Any]:
    """Fetch one full note (with transcript by default)."""
    params = {"include": "transcript"} if include_transcript else None
    return _get(f"/notes/{note_id}", token=token, params=params)


# --- pure text assembly (unit-tested) ---------------------------------------


def _attendee_names(note: dict[str, Any]) -> list[str]:
    """Best-effort attendee display names (fall back to email)."""
    names: list[str] = []
    for a in note.get("attendees") or []:
        label = (a.get("name") or a.get("email") or "").strip()
        if label:
            names.append(label)
    return names


def _meeting_date(note: dict[str, Any]) -> str:
    """The meeting's date string: calendar start if present, else created_at."""
    cal = note.get("calendar_event") or {}
    return (cal.get("scheduled_start_time") or note.get("created_at") or "").strip()


def _transcript_text(note: dict[str, Any]) -> str:
    """Flatten the transcript array to `Speaker: text` lines (empty if none)."""
    lines: list[str] = []
    for seg in note.get("transcript") or []:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        speaker = ((seg.get("speaker") or {}).get("name") or "").strip()
        lines.append(f"{speaker}: {text}" if speaker else text)
    return "\n".join(lines)


def assemble_note_text(note: dict[str, Any]) -> str:
    """Build the mode-1 ingest blob from a full note (pure).

    A small structured header (title / date / attendees) precedes the summary and
    transcript, so cognee's extraction sees the meeting's participants and date
    in-text even before the typed `Meeting` node (the hybrid next-step) exists.
    """
    title = (note.get("title") or (note.get("calendar_event") or {}).get("event_title")
             or "(untitled meeting)").strip()
    parts = [f"Meeting: {title}"]
    date = _meeting_date(note)
    if date:
        parts.append(f"Date: {date}")
    attendees = _attendee_names(note)
    if attendees:
        parts.append(f"Attendees: {', '.join(attendees)}")

    summary = (note.get("summary_markdown") or note.get("summary_text") or "").strip()
    if summary:
        parts.append(f"\n## Summary\n{summary}")

    transcript = _transcript_text(note)
    if transcript:
        parts.append(f"\n## Transcript\n{transcript}")

    return "\n".join(parts).strip()
