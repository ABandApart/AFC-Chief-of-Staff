"""Unit tests for the Tartt poller skeleton (Phase 4, Task 1).

The due-check and selection are pure; the DB fetch + per-source pipeline (Tasks
2–5) are not exercised here. What matters now: a never-polled source is due, the
interval boundary is inclusive, and `filter_due` selects exactly the due subset.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agents.tartt import run

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


def test_is_due_when_never_polled():
    assert run.is_due(None, 12, NOW) is True


def test_is_due_false_before_interval_elapses():
    assert run.is_due(NOW - timedelta(hours=6), 12, NOW) is False


def test_is_due_true_after_interval_elapses():
    assert run.is_due(NOW - timedelta(hours=13), 12, NOW) is True


def test_is_due_true_at_exact_boundary():
    assert run.is_due(NOW - timedelta(hours=12), 12, NOW) is True


def test_filter_due_selects_only_due_sources():
    sources = [
        {"id": 1, "last_polled_at": None, "poll_interval_hours": 12},                    # never
        {"id": 2, "last_polled_at": NOW - timedelta(hours=1), "poll_interval_hours": 12},   # recent
        {"id": 3, "last_polled_at": NOW - timedelta(hours=24), "poll_interval_hours": 12},  # stale
    ]
    assert [s["id"] for s in run.filter_due(sources, NOW)] == [1, 3]


def test_unseen_items_filters_seen_and_caps():
    items = [{"url": f"u{i}"} for i in range(5)]
    out = run.unseen_items(items, seen={"u1", "u3"}, cap=2)
    # u1/u3 already tracked → dropped; remaining capped to 2, order preserved.
    assert [i["url"] for i in out] == ["u0", "u2"]


def test_unseen_items_empty_when_all_seen():
    items = [{"url": "u0"}, {"url": "u1"}]
    assert run.unseen_items(items, seen={"u0", "u1"}, cap=5) == []


def test_task_candidate_fields_populates_every_field():
    item = {"url": "https://ex.com/x", "title": "  A Great Read  "}
    f = run.task_candidate_fields(item, "why it matters", "node-123", 0.71)
    assert f["proposed_action"] == "Share or write about: A Great Read"
    assert f["source_type"] == "discovery"
    assert f["source_ref"] == "node-123"
    assert f["evidence_text"] == "why it matters"
    assert f["confidence"] == 0.71


def test_task_candidate_fields_falls_back_to_url_when_untitled():
    f = run.task_candidate_fields({"url": "https://ex.com/x", "title": ""}, "s", "n", 0.6)
    assert f["proposed_action"] == "Share or write about: https://ex.com/x"


def test_process_source_skips_a_failing_item_not_the_batch(mocker):
    # A transient per-item failure (e.g. Gemini 503 mid-summarize) must skip that
    # item and continue — barry-agent found a 503 was aborting a whole source.
    import asyncio as _asyncio
    from unittest.mock import AsyncMock

    mocker.patch.object(
        run.fetch, "list_source_items",
        return_value=[{"url": "u1", "title": "A"}, {"url": "u2", "title": "B"}],
    )
    mocker.patch.object(run, "seen_urls", return_value=set())

    def _fetch(url):
        if url == "u1":
            raise RuntimeError("transient upstream (e.g. Gemini 503)")
        return "article text"

    mocker.patch.object(run.fetch, "fetch_article_text", side_effect=_fetch)
    mocker.patch.object(run.summarize, "summarize", return_value="a summary")
    mocker.patch.object(run.content_graph, "add_content_item", new=AsyncMock(return_value="node-2"))
    mocker.patch.object(run.scoring, "score_content_item", return_value=0.1)  # below both gates
    mocker.patch.object(run, "record_content")

    result = _asyncio.run(run.process_source(mocker.MagicMock(), {"id": 1, "url": "feed"}))
    assert result == ("processed", 1)  # u2 processed despite u1 raising
