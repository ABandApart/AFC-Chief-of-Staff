"""Unit tests for the #tasks checklist core (PRD Increment 3).

DB reads/writes are mocked (no live Postgres). Cover the query shape (join +
recent-completed window + ordering + cap), the idempotent check/uncheck writes,
and the pure line formatting. The Discord surface is in `test_tasks_checklist_cog`.
"""
from __future__ import annotations

from agents._lib import tasks_checklist as tc


def _mock_db(mocker, *, fetchone=None, fetchall=None):
    """Mock `tasks_checklist.db.connection()` → conn → cursor; return the cursor."""
    cur = mocker.MagicMock()
    cur.fetchone.return_value = fetchone
    cur.fetchall.return_value = fetchall if fetchall is not None else []
    conn = mocker.MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cm = mocker.MagicMock()
    cm.__enter__.return_value = conn
    mocker.patch.object(tc.db, "connection", return_value=cm)
    return cur


def test_list_checklist_joins_follow_ups_and_orders_open_first(mocker):
    cur = _mock_db(mocker, fetchall=[])
    tc.list_checklist()
    sql, params = cur.execute.call_args.args
    assert "LEFT JOIN follow_ups fu ON fu.id = t.follow_up_id" in sql
    # Open tasks OR ones completed inside the recent window (archive-after).
    assert "t.status = 'open'" in sql
    assert "now() - %s::interval" in sql
    # Ordered: open first, most-escalated, then soonest-due.
    assert "(t.status = 'open') DESC" in sql
    assert "fu.escalation_level DESC" in sql
    assert "t.due_date ASC" in sql
    assert "LIMIT %s" in sql
    assert params == (tc.ARCHIVE_AFTER, tc.MAX_ROWS)


def test_list_checklist_passes_an_explicit_limit(mocker):
    cur = _mock_db(mocker, fetchall=[])
    tc.list_checklist(limit=5)
    _, params = cur.execute.call_args.args
    assert params == (tc.ARCHIVE_AFTER, 5)


def test_count_open_counts_only_open(mocker):
    cur = _mock_db(mocker, fetchone=(14,))
    assert tc.count_open() == 14
    sql = cur.execute.call_args.args[0]
    assert "count(*)" in sql and "status = 'open'" in sql


def test_complete_task_is_guarded_and_returns_id(mocker):
    cur = _mock_db(mocker, fetchone=(7,))
    assert tc.complete_task(7) == 7
    sql, params = cur.execute.call_args.args
    assert "SET status = 'completed'" in sql
    assert "completed_at = now()" in sql
    assert "WHERE id = %s AND status = 'open'" in sql  # the idempotency gate
    assert params == (7,)


def test_complete_task_noop_returns_none(mocker):
    _mock_db(mocker, fetchone=None)  # already completed → zero rows updated
    assert tc.complete_task(7) is None


def test_reopen_task_clears_completed_at_and_is_guarded(mocker):
    cur = _mock_db(mocker, fetchone=(7,))
    assert tc.reopen_task(7) == 7
    sql, params = cur.execute.call_args.args
    assert "SET status = 'open'" in sql
    assert "completed_at = NULL" in sql
    assert "WHERE id = %s AND status = 'completed'" in sql
    assert params == (7,)


def test_reopen_task_noop_returns_none(mocker):
    _mock_db(mocker, fetchone=None)
    assert tc.reopen_task(7) is None


def test_format_line_open_shows_checkbox_due_and_escalation():
    line = tc.format_line(
        {"id": 1, "title": "Email Alex", "status": "open",
         "due_date": "2026-09-12", "escalation_level": 2}
    )
    assert line == "☐ Email Alex  · due 2026-09-12 · esc 2"


def test_format_line_open_without_meta_is_just_the_title():
    assert tc.format_line({"id": 1, "title": "Ping Sam", "status": "open"}) == "☐ Ping Sam"


def test_format_line_completed_is_struck_through():
    line = tc.format_line({"id": 2, "title": "Done thing", "status": "completed"})
    assert line == "☑ ~~Done thing~~"
    assert tc.is_done({"status": "completed"}) is True
    assert tc.is_done({"status": "open"}) is False


def test_format_line_tolerates_a_missing_title():
    assert tc.format_line({"id": 9, "status": "open"}) == "☐ task #9"
