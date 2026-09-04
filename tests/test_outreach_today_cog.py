"""Cog-level tests for the #outreach daily-surface card (Track O).

The rules and writes are tested in `test_outreach_daily_surface.py`. Here: the
SEC-2 operator guard, the card's rendering, and that Contact/Defer report
correctly — including that a window-closed defer is a "not possible", not a
silent failure, and an already-resolved touch is a clean no-op.

No live bot or DB. Guarded by importorskip('discord').
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("discord")

from agents._lib import outreach_daily_surface as ds  # noqa: E402
from agents.discord_bot.cogs import outreach_today as cog  # noqa: E402

TOUCH = {
    "id": 42, "slot": 4, "due_date": date(2026, 9, 3), "bcc_token": "tok42",
    "gmail_thread_id": "thread99", "marked_working_at": None,
}
TARGET = {"company_name": "Cadence Health"}
EVIDENCE = [
    {"payload": {"title": "VP Revenue req"}, "closed_at": None,
     "freshness": "fresh", "age_days": 56},
]
PACKET = {
    "subject_line": "Saw you are hiring a VP Revenue",
    "ready": False,
    "unresolved_slots": ["specific outcome with a number"],
    "failure_mode": "Positioning yourself as an alternative to the hire.",
    "arithmetic": {"has_arithmetic": False},
}
DATA = {"touch": TOUCH, "target": TARGET, "evidence": EVIDENCE, "packet": PACKET}


def _interaction(user_id: int) -> MagicMock:
    ix = MagicMock()
    ix.user.id = user_id
    ix.message = None
    ix.response.is_done.return_value = False
    ix.response.send_message = AsyncMock()
    ix.followup.send = AsyncMock()
    return ix


def _cog() -> cog.OutreachTodayCog:
    return cog.OutreachTodayCog(MagicMock())


# --- the card ------------------------------------------------------------------


def test_card_shows_company_slot_due_and_subject():
    embed = cog.build_card(DATA)
    assert "Cadence Health" in embed.title
    assert "slot 4/5" in embed.title
    assert "due 2026-09-03" in embed.title
    assert "Saw you are hiring" in embed.description
    fields = {f.name: f.value for f in embed.fields}
    assert "VP Revenue req" in fields["Driving facts"]
    assert "specific outcome with a number" in fields["You write"]
    assert "thread99" in fields["Draft"]                 # the Gmail thread link
    assert fields["BCC"] == "bcc+tok42@aiadaptive.co"
    assert "touch #42" in embed.footer.text


def test_ready_packet_reads_as_ready_and_greens():
    ready = {**PACKET, "ready": True, "unresolved_slots": []}
    embed = cog.build_card({**DATA, "packet": ready})
    fields = {f.name: f.value for f in embed.fields}
    assert "ready to send" in fields["Ready"]


def test_card_without_a_packet_says_not_assembled():
    embed = cog.build_card({**DATA, "packet": None})
    assert "not assembled" in embed.description


def test_working_touch_shows_the_mark():
    worked = {**TOUCH, "marked_working_at": datetime(2026, 9, 3, 6)}
    embed = cog.build_card({**DATA, "touch": worked})
    assert "✓ working" in embed.footer.text


def test_draft_falls_back_to_the_drafts_folder_without_a_thread():
    embed = cog.build_card({**DATA, "touch": {**TOUCH, "gmail_thread_id": None}})
    fields = {f.name: f.value for f in embed.fields}
    assert "Gmail Drafts" in fields["Draft"]


# --- the identity guard (SEC-2) -----------------------------------------------


def test_operator_is_authorized(monkeypatch):
    monkeypatch.setattr(cog, "OPERATOR_ID", 4242)
    assert asyncio.run(_cog()._authorized(_interaction(4242), 42)) is True


def test_a_foreign_click_is_denied_loudly(monkeypatch):
    monkeypatch.setattr(cog, "OPERATOR_ID", 4242)
    ix = _interaction(9999)
    assert asyncio.run(_cog()._authorized(ix, 42)) is False
    ix.response.send_message.assert_awaited_once()


def test_unset_operator_id_fails_closed(monkeypatch):
    monkeypatch.setattr(cog, "OPERATOR_ID", 0)
    assert asyncio.run(_cog()._authorized(_interaction(4242), 42)) is False


# --- Contact -------------------------------------------------------------------


def test_contact_marks_working_and_confirms(mocker):
    mocker.patch.object(cog.OutreachTodayCog, "_do_contact", return_value=datetime(2026, 9, 3, 6))
    footer = mocker.patch.object(cog.OutreachTodayCog, "_mark_working_footer", new=AsyncMock())
    ix = _interaction(4242)
    asyncio.run(_cog().finish_contact(ix, 42))
    footer.assert_awaited_once()
    assert "working today" in ix.response.send_message.call_args.args[0]


def test_contact_on_a_resolved_touch_is_a_clean_noop(mocker):
    mocker.patch.object(cog.OutreachTodayCog, "_do_contact", return_value=None)
    footer = mocker.patch.object(cog.OutreachTodayCog, "_mark_working_footer", new=AsyncMock())
    ix = _interaction(4242)
    asyncio.run(_cog().finish_contact(ix, 42))
    footer.assert_not_awaited()
    assert "already sent or skipped" in ix.response.send_message.call_args.args[0]


# --- Defer ---------------------------------------------------------------------


def test_defer_snoozes_and_closes_the_card(mocker):
    mocker.patch.object(cog.OutreachTodayCog, "_do_defer", return_value=date(2026, 9, 4))
    disable = mocker.patch.object(cog.OutreachTodayCog, "_disable_card", new=AsyncMock())
    ix = _interaction(4242)
    asyncio.run(_cog().finish_defer(ix, 42, "waiting on their board meeting"))
    disable.assert_awaited_once()
    assert "2026-09-04" in ix.response.send_message.call_args.args[0]


def test_defer_on_a_closing_window_is_refused_not_swallowed(mocker):
    mocker.patch.object(
        cog.OutreachTodayCog, "_do_defer",
        side_effect=ds.DeferWindowClosedError("the window closes today"),
    )
    disable = mocker.patch.object(cog.OutreachTodayCog, "_disable_card", new=AsyncMock())
    ix = _interaction(4242)
    asyncio.run(_cog().finish_defer(ix, 42, "note"))
    disable.assert_not_awaited()
    assert "window closes today" in ix.response.send_message.call_args.args[0]


def test_defer_on_a_resolved_touch_is_a_clean_noop(mocker):
    mocker.patch.object(cog.OutreachTodayCog, "_do_defer", return_value=None)
    ix = _interaction(4242)
    asyncio.run(_cog().finish_defer(ix, 42, "note"))
    assert "already sent or skipped" in ix.response.send_message.call_args.args[0]
