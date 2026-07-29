"""Unit tests for the Granola client's pure logic (Track C).

The HTTP calls are exercised by runtime validation; here we pin the text-blob
assembly (what actually reaches the graph), the 429 backoff computation, and the
cursor-pagination/ordering (the watermark-correctness surface).
"""

from __future__ import annotations

from agents._lib import granola_client as gc

FULL_NOTE = {
    "id": "not_abc123def45678",
    "title": "Northwind discovery call",
    "created_at": "2026-07-20T15:00:00Z",
    "calendar_event": {
        "event_title": "Northwind <> AI Adaptive",
        "scheduled_start_time": "2026-07-20T16:00:00Z",
    },
    "attendees": [
        {"name": "Priya Shah", "email": "priya@northwind.com"},
        {"name": None, "email": "barry@aiadaptive.co"},
    ],
    "summary_markdown": "- Wants Tuesday check-ins\n- Budget approved",
    "summary_text": "Wants Tuesday check-ins. Budget approved.",
    "transcript": [
        {"speaker": {"name": "Priya"}, "text": "Tuesdays work best for us."},
        {"speaker": {"name": "Barry"}, "text": "Great, I'll set that up."},
        {"speaker": {"name": "Priya"}, "text": "   "},  # blank → dropped
    ],
}


# --- assemble_note_text -----------------------------------------------------


def test_assemble_has_header_summary_and_transcript():
    text = gc.assemble_note_text(FULL_NOTE)
    assert text.startswith("Meeting: Northwind discovery call")
    assert "Date: 2026-07-20T16:00:00Z" in text          # calendar start wins over created_at
    assert "Attendees: Priya Shah, barry@aiadaptive.co" in text  # email fallback for null name
    assert "## Summary" in text and "Tuesday check-ins" in text
    assert "## Transcript" in text
    assert "Priya: Tuesdays work best for us." in text
    assert "Barry: Great, I'll set that up." in text


def test_assemble_drops_blank_transcript_segments():
    text = gc.assemble_note_text(FULL_NOTE)
    # the whitespace-only 3rd segment must not add a dangling "Priya: " line
    assert text.count("Priya:") == 1


def test_assemble_prefers_markdown_summary():
    text = gc.assemble_note_text(FULL_NOTE)
    assert "- Wants Tuesday check-ins" in text  # markdown, not the plain summary_text


def test_assemble_minimal_note_untitled_no_transcript():
    text = gc.assemble_note_text({"id": "not_x", "created_at": "2026-07-01T09:00:00Z"})
    assert text.startswith("Meeting: (untitled meeting)")
    assert "Date: 2026-07-01T09:00:00Z" in text
    assert "## Transcript" not in text
    assert "## Summary" not in text


def test_assemble_falls_back_to_calendar_event_title():
    note = {"id": "not_y", "title": None,
            "calendar_event": {"event_title": "Weekly sync"}}
    assert gc.assemble_note_text(note).startswith("Meeting: Weekly sync")


# --- backoff ----------------------------------------------------------------


def test_retry_after_honors_header():
    assert gc._retry_after_seconds({"Retry-After": "7"}, attempt=0) == 7.0


def test_retry_after_falls_back_to_exponential():
    assert gc._retry_after_seconds({}, attempt=0) == 1.0
    assert gc._retry_after_seconds({}, attempt=3) == 8.0
    # a non-numeric header is ignored → exponential
    assert gc._retry_after_seconds({"Retry-After": "soon"}, attempt=2) == 4.0


# --- pagination / ordering --------------------------------------------------


def test_iter_note_summaries_follows_cursor_and_sorts(monkeypatch):
    pages = [
        {"notes": [{"id": "b", "updated_at": "2026-07-20T00:00:00Z"}],
         "hasMore": True, "cursor": "c1"},
        {"notes": [{"id": "a", "updated_at": "2026-07-19T00:00:00Z"},
                   {"id": "c", "updated_at": "2026-07-21T00:00:00Z"}],
         "hasMore": False, "cursor": None},
    ]
    calls: list[str | None] = []

    def fake_list_notes(token, *, updated_after=None, cursor=None):
        calls.append(cursor)
        return pages.pop(0)

    monkeypatch.setattr(gc, "list_notes", fake_list_notes)
    out = gc.iter_note_summaries("tok", updated_after="2026-07-18T00:00:00Z")

    assert [n["id"] for n in out] == ["a", "b", "c"]   # oldest-first by updated_at
    assert calls == [None, "c1"]                        # started null, followed the cursor


def test_iter_note_summaries_single_page_no_cursor(monkeypatch):
    monkeypatch.setattr(
        gc, "list_notes",
        lambda token, **_: {"notes": [{"id": "solo", "updated_at": "2026-07-20T00:00:00Z"}],
                            "hasMore": False, "cursor": None},
    )
    out = gc.iter_note_summaries("tok")
    assert [n["id"] for n in out] == ["solo"]
