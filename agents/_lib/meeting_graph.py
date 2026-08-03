"""Structured `Meeting` DataPoints from Granola notes (Track C — hybrid).

Alongside the mode-1 free-text ingest (`ingest.py`), each Granola meeting also
gets a **typed `Meeting` node with `Person` participants**, built from the API's
already-structured fields (title, date, attendees). This makes meetings
entity-addressable ("meetings with X", "who was in the room") — the relational
recall that mode-1 text extraction is weak at. The two representations coexist in
the graph (v1 doesn't link them).

**Entity resolution = deterministic ids.** A `Person`'s id is `uuid5` of their
email (or normalized name when no email); a `Meeting`'s id is `uuid5` of the
Granola note id. So the same person/meeting seen across polls **upserts to one
node** instead of duplicating — the whole point of the typed layer. This assumes
cognee's `add_data_points` keys nodes on the DataPoint `id`; that assumption is
confirmed by `agents/test/ontology_shape.py` before this is wired into the live
poller.

Skips `Organization` (email-domain→org is unreliable) and
`produced_facts`/`produced_decisions` (no structured source — mode-1's cognify
already extracts content from the transcript). `add_data_points` embeds only the
`index_fields` (Meeting: title+summary, Person: name+context) via the local bge
embedder — no LLM, ~free. Runs under the shared `granola` telemetry label.
"""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from agents._lib import granola_client, ontology
from agents._lib.telemetry_context import labeled

# Stable namespaces → ids are reproducible across runs and processes.
_PERSON_NS = uuid5(NAMESPACE_URL, "afc-richmond/person")
_MEETING_NS = uuid5(NAMESPACE_URL, "afc-richmond/meeting")


def person_id(name: str, email: str) -> UUID:
    """Deterministic Person id: by email (lowercased) if present, else by
    normalized name. The entity-resolution key across meetings."""
    key = (email or "").strip().lower() or " ".join((name or "").split()).casefold()
    return uuid5(_PERSON_NS, key)


def meeting_id(note_id: str) -> UUID:
    """Deterministic Meeting id from the Granola note id (re-polls upsert)."""
    return uuid5(_MEETING_NS, note_id)


def _participants(note: dict[str, Any]) -> list[ontology.Person]:
    """One Person per distinct attendee (deduped by resolved id)."""
    people: list[ontology.Person] = []
    seen: set[UUID] = set()
    for a in note.get("attendees") or []:
        name = (a.get("name") or "").strip()
        email = (a.get("email") or "").strip()
        if not name and not email:
            continue
        pid = person_id(name, email)
        if pid in seen:
            continue
        seen.add(pid)
        people.append(ontology.Person(id=pid, name=name or email))
    return people


def build_meeting_datapoint(note: dict[str, Any]) -> ontology.Meeting:
    """Map a Granola note dict → a typed `Meeting` DataPoint (pure)."""
    title = (
        note.get("title")
        or (note.get("calendar_event") or {}).get("event_title")
        or "(untitled meeting)"
    ).strip()
    summary = (note.get("summary_markdown") or note.get("summary_text") or "").strip()
    return ontology.Meeting(
        id=meeting_id(note["id"]),
        title=title,
        summary=summary,
        meeting_date=granola_client._meeting_date(note) or None,
        participants=_participants(note),
    )


async def add_meeting_graph(note: dict[str, Any]) -> None:
    """Insert the typed Meeting (+ participant People) for one note into the graph.

    Local-embed only (no LLM); attributed to the `granola` label. `configure_cognee()`
    must have run first. Raises on failure — the caller decides how to handle it
    (the mode-1 content is already durable, so a failure here is non-fatal).
    """
    from cognee.tasks.storage import add_data_points  # lazy — optional cognee

    meeting = build_meeting_datapoint(note)
    with labeled(
        "granola", "customer_discovery", trigger_kind="event", correlation_id=note["id"]
    ):
        await add_data_points([meeting])
