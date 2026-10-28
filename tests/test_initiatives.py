"""Tests for initiatives on cards and tasks (N3): labels, type-ahead choices, parsing,
and the guarded writes (DB mocked)."""

from __future__ import annotations

from datetime import date

import pytest

from agents._lib import initiatives as ini

# --- labels --------------------------------------------------------------------------


def test_label_cases():
    base = {"initiative_name": "Proposal template", "initiative_status": "Active",
            "initiative_gone": False}
    assert ini.label({}) == "Unassigned"
    assert ini.label(base) == "Proposal template"
    assert ini.label({**base, "initiative_status": "Paused"}) == "Proposal template (paused)"
    assert ini.label({**base, "initiative_status": "Done"}) == "Proposal template (done)"
    assert ini.label({**base, "initiative_gone": True}) == "Proposal template (retired)"


# --- type-ahead ----------------------------------------------------------------------


ROWS = [{"id": 1, "name": "Proposal template", "key_result": "Proposals sent"},
        {"id": 2, "name": "Webinar series", "key_result": "Conversations opened"}]


def test_initiative_choices_put_unassigned_first_and_show_the_kr():
    assert ini.initiative_choices("", ROWS) == [
        ("Unassigned", "none"),
        ("Proposal template · Proposals sent", "1"),
        ("Webinar series · Conversations opened", "2"),
    ]


def test_initiative_choices_filter_on_initiative_or_kr_text():
    assert ini.initiative_choices("conversations", ROWS) == [
        ("Webinar series · Conversations opened", "2")]
    assert ini.initiative_choices("unass", ROWS) == [("Unassigned", "none")]


def test_initiative_choices_cap_and_truncate():
    many = [{"id": n, "name": "x" * 120, "key_result": "KR"} for n in range(40)]
    got = ini.initiative_choices("", many)
    assert len(got) == ini.MAX_CHOICES
    assert all(len(name) <= ini.MAX_CHOICE_CHARS for name, _ in got)
    assert got[1][0].endswith("…")


def test_item_choices_distinguish_cards_from_tasks():
    items = [{"kind": "card", "id": 7, "title": "Write the post"},
             {"kind": "task", "id": 7, "title": "Email Sam"}]
    assert ini.item_choices("", items) == [("Card #7 · Write the post", "c:7"),
                                           ("Task #7 · Email Sam", "t:7")]
    assert ini.item_choices("#7 · email", items) == [("Task #7 · Email Sam", "t:7")]


def test_item_choices_cap_and_truncate():
    items = [{"kind": "task", "id": n, "title": "y" * 150} for n in range(30)]
    got = ini.item_choices("", items)
    assert len(got) == ini.MAX_CHOICES
    assert all(len(name) <= ini.MAX_CHOICE_CHARS for name, _ in got)


# --- parsing -------------------------------------------------------------------------


def test_parse_item():
    assert ini.parse_item("c:12") == ("card", 12)
    assert ini.parse_item("t:5") == ("task", 5)
    for bad in ("12", "x:1", "c:", "Write the post"):
        with pytest.raises(ValueError):
            ini.parse_item(bad)


def test_parse_initiative():
    assert ini.parse_initiative(None) is None
    assert ini.parse_initiative("none") is None
    assert ini.parse_initiative("4") == 4
    with pytest.raises(ValueError):
        ini.parse_initiative("Proposal template")


def test_parse_due():
    assert ini.parse_due(None) is None
    assert ini.parse_due("  ") is None
    assert ini.parse_due("2026-11-15") == date(2026, 11, 15)
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        ini.parse_due("next Friday")


# --- guarded writes (DB mocked) ------------------------------------------------------


def _mock_db(mocker, fetchone_values):
    cur = mocker.MagicMock()
    cur.fetchone.side_effect = list(fetchone_values)
    conn = mocker.MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    cm = mocker.MagicMock()
    cm.__enter__.return_value = conn
    mocker.patch.object(ini.db, "connection", return_value=cm)
    return cur


def test_assign_checks_the_initiative_then_guards_the_item(mocker):
    cur = _mock_db(mocker, [(1,), (9,)])
    assert ini.assign("card", 9, 3) is True
    check_sql, check_params = cur.execute.call_args_list[0].args
    assert "i.status = 'Active'" in check_sql and check_params == (3,)
    sql, params = cur.execute.call_args_list[1].args
    assert "UPDATE task_candidates SET okr_initiative_id" in sql
    assert "status = %s" in sql and params == (3, 9, "pending")


def test_assign_to_a_task_requires_it_open_and_unassigned_skips_the_check(mocker):
    cur = _mock_db(mocker, [None])
    assert ini.assign("task", 4, None) is False  # done since the list was shown
    sql, params = cur.execute.call_args.args
    assert "UPDATE tasks SET okr_initiative_id" in sql
    assert params == (None, 4, "open")
    assert cur.execute.call_count == 1


def test_assign_refuses_an_initiative_that_is_no_longer_active(mocker):
    _mock_db(mocker, [None])
    with pytest.raises(ValueError, match="isn't active"):
        ini.assign("card", 9, 3)


def test_add_todo_writes_an_operator_card_through_task_tinder(mocker):
    cur = _mock_db(mocker, [(1,), (55,)])
    assert ini.add_todo("Call the bank", 3, date(2026, 11, 1)) == 55
    sql, params = cur.execute.call_args.args
    assert "INSERT INTO task_candidates" in sql
    assert "'operator'" in sql and "1.0" in sql
    assert params == ("Call the bank", 3, date(2026, 11, 1))
