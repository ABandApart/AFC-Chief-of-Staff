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


def test_process_source_is_a_skeleton_stub():
    # Task-1 placeholder: reports no items until the Task 2-5 pipeline lands.
    status, n = run.process_source({"name": "X", "source_kind": "rss", "url": "http://x"})
    assert status == "skeleton" and n == 0
