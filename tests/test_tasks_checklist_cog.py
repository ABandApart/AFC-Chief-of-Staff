"""Cog-level tests for the #tasks checklist (PRD Increment 3).

The list/write core is in `test_tasks_checklist.py`. Here: the Components-v2 view
wires a check button to open tasks and an undo button to completed ones with
stable custom_ids, the operator guard, and that a toggle writes then updates the
message — no live bot or DB. Guarded by importorskip('discord').
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("discord")

import discord  # noqa: E402

from agents.discord_bot.cogs import tasks_checklist as cog  # noqa: E402

_ROWS = [
    {"id": 1, "title": "Open one", "status": "open", "due_date": None, "escalation_level": 3},
    {"id": 2, "title": "Done one", "status": "completed", "due_date": None, "escalation_level": 0},
]


def _button_ids(view: discord.ui.LayoutView) -> list[str]:
    return [i.custom_id for i in view.walk_children() if isinstance(i, discord.ui.Button)]


def _interaction(user_id: int) -> MagicMock:
    ix = MagicMock()
    ix.user.id = user_id
    ix.response.is_done.return_value = False
    ix.response.send_message = AsyncMock()
    ix.response.edit_message = AsyncMock()
    ix.followup.send = AsyncMock()
    return ix


def test_view_wires_check_for_open_and_undo_for_completed():
    view = cog.ChecklistView(MagicMock(), _ROWS, open_count=1)
    # Open task → check control; completed task → uncheck control; ids are stable.
    assert _button_ids(view) == ["tasks:check:1", "tasks:uncheck:2"]


def test_view_with_rows_is_persistent():
    # Persistence (timeout=None + custom_ids) is what lets it re-attach on restart.
    assert cog.ChecklistView(MagicMock(), _ROWS, open_count=1).is_persistent() is True


def test_empty_view_shows_the_placeholder_and_no_buttons():
    view = cog.ChecklistView(MagicMock(), [], open_count=0)
    assert _button_ids(view) == []
    texts = [i.content for i in view.walk_children() if isinstance(i, discord.ui.TextDisplay)]
    assert any("Nothing open" in t for t in texts)


def test_authorized_allows_operator_and_denies_others(mocker):
    mocker.patch.object(cog, "OPERATOR_ID", 999)
    c = cog.TasksChecklistCog(MagicMock())
    assert asyncio.run(c._authorized(_interaction(999), 1)) is True

    ix = _interaction(123)
    assert asyncio.run(c._authorized(ix, 1)) is False
    ix.response.send_message.assert_awaited_once()  # denied loudly


def test_toggle_completes_the_task_and_updates_the_message(mocker):
    mocker.patch.object(cog, "OPERATOR_ID", 999)
    complete = mocker.patch.object(cog.tc, "complete_task", return_value=1)
    reopen = mocker.patch.object(cog.tc, "reopen_task", return_value=1)
    mocker.patch.object(cog.tc, "list_checklist", return_value=[])
    mocker.patch.object(cog.tc, "count_open", return_value=0)

    c = cog.TasksChecklistCog(MagicMock())
    ix = _interaction(999)
    asyncio.run(c.handle_toggle(ix, 1, reopen=False))

    complete.assert_called_once_with(1)
    reopen.assert_not_called()
    # The pinned message is rebuilt in place with a fresh view.
    ix.response.edit_message.assert_awaited_once()
    assert isinstance(ix.response.edit_message.await_args.kwargs["view"], cog.ChecklistView)


def test_toggle_reopen_calls_reopen_not_complete(mocker):
    mocker.patch.object(cog, "OPERATOR_ID", 999)
    complete = mocker.patch.object(cog.tc, "complete_task", return_value=1)
    reopen = mocker.patch.object(cog.tc, "reopen_task", return_value=1)
    mocker.patch.object(cog.tc, "list_checklist", return_value=[])
    mocker.patch.object(cog.tc, "count_open", return_value=1)

    c = cog.TasksChecklistCog(MagicMock())
    asyncio.run(c.handle_toggle(_interaction(999), 2, reopen=True))

    reopen.assert_called_once_with(2)
    complete.assert_not_called()


def test_toggle_denied_for_non_operator_does_not_write(mocker):
    mocker.patch.object(cog, "OPERATOR_ID", 999)
    complete = mocker.patch.object(cog.tc, "complete_task", return_value=1)

    c = cog.TasksChecklistCog(MagicMock())
    asyncio.run(c.handle_toggle(_interaction(123), 1, reopen=False))

    complete.assert_not_called()
