"""Cog-level tests for the Gate 1 intake card (Track O).

The decision core is tested in `test_outreach_intake.py`. Here: the SEC-2
identity guard, the card's rendering, and — the part that matters most — that a
refusal leaves the card **live** rather than consuming the decision. A capacity
refusal that greyed out the buttons would silently drop the target: it is not a
"no", it is a "not yet".

No live bot or DB. Guarded by importorskip('discord').
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("discord")

from agents._lib import outreach_intake as intake  # noqa: E402
from agents.discord_bot.cogs import outreach_intake as cog  # noqa: E402

TARGET = {
    "id": 7, "company_name": "Cadence Health", "stage": "series_a",
    "function_state": "vacant_seat", "trigger_kind": "request_open_past_45_days",
    "days_since_trigger": 56, "trigger_date": "2026-06-19", "score": 23,
    "compound_signal": True, "contact_name": "Marcus Oyelaran",
    "contact_role": "Founder", "is_reengagement": False,
}
CAPACITY = {"cold_live": 13, "cold_ceiling": 15,
            "reengagement_live": 0, "reengagement_ceiling": 3}
EVIDENCE = [
    {"fact_kind": "open_role", "payload": {"title": "VP Revenue"},
     "age_days": 56, "freshness": "fresh"},
]


def _interaction(user_id: int) -> MagicMock:
    ix = MagicMock()
    ix.user.id = user_id
    ix.message = None
    ix.response.is_done.return_value = False
    ix.response.send_message = AsyncMock()
    ix.followup.send = AsyncMock()
    return ix


def _cog() -> cog.OutreachIntakeCog:
    return cog.OutreachIntakeCog(MagicMock())


# --- the identity guard (SEC-2) -----------------------------------------------


def test_operator_is_authorized(monkeypatch):
    monkeypatch.setattr(cog, "OPERATOR_ID", 4242)
    assert asyncio.run(_cog()._authorized(_interaction(4242), 7)) is True


def test_a_foreign_click_is_denied_loudly(monkeypatch):
    monkeypatch.setattr(cog, "OPERATOR_ID", 4242)
    ix = _interaction(9999)
    assert asyncio.run(_cog()._authorized(ix, 7)) is False
    ix.response.send_message.assert_awaited_once()


def test_unset_operator_id_fails_closed(monkeypatch):
    # 0 means unconfigured; every click is denied rather than every click
    # allowed. Same posture as the approvals gate.
    monkeypatch.setattr(cog, "OPERATOR_ID", 0)
    assert asyncio.run(_cog()._authorized(_interaction(4242), 7)) is False


# --- the card ------------------------------------------------------------------


def test_card_matches_the_specs_shape():
    embed = cog.build_card(TARGET, EVIDENCE, CAPACITY)
    assert "score 23/25" in embed.title
    assert "COMPOUND SIGNAL" in embed.title       # s4 and s5 both at the top band
    assert "Cadence Health" in embed.description
    assert "vacant seat" in embed.description
    fields = {f.name: f.value for f in embed.fields}
    assert "56d since" in fields["Trigger"]
    assert "Marcus Oyelaran, Founder" == fields["Contact"]
    assert "VP Revenue" in fields["Evidence"]
    assert "13 of 15 cold live" in fields["Capacity"]
    assert "target #7" in embed.footer.text


def test_card_without_a_compound_signal_is_not_marked():
    embed = cog.build_card({**TARGET, "compound_signal": False}, EVIDENCE, CAPACITY)
    assert "COMPOUND" not in embed.title


def test_card_names_missing_judgement_rather_than_hiding_it():
    embed = cog.build_card({**TARGET, "function_state": None}, EVIDENCE, CAPACITY)
    assert "function state not set" in embed.description


def test_card_handles_a_target_with_no_contact_yet():
    embed = cog.build_card({**TARGET, "contact_name": None, "contact_role": None},
                           EVIDENCE, CAPACITY)
    fields = {f.name: f.value for f in embed.fields}
    assert "no contact yet" in fields["Contact"]


# --- evidence rendering (§3 display rules) ------------------------------------


def test_evidence_marks_ageing_and_stale_facts():
    rendered = cog.format_evidence([
        {"fact_kind": "open_role", "payload": {"title": "A"}, "age_days": 5,
         "freshness": "fresh"},
        {"fact_kind": "open_role", "payload": {"title": "B"}, "age_days": 40,
         "freshness": "ageing"},
        {"fact_kind": "open_role", "payload": {"title": "C"}, "age_days": 60,
         "freshness": "stale"},
    ])
    assert "A — open 5d" in rendered
    assert "⚠️" in rendered and "stale" in rendered


def test_evidence_is_capped_and_says_how_many_more():
    facts = [
        {"fact_kind": "open_role", "payload": {"title": f"R{i}"}, "age_days": i,
         "freshness": "fresh"}
        for i in range(10)
    ]
    rendered = cog.format_evidence(facts)
    assert rendered.count("• ") == cog.MAX_EVIDENCE_LINES + 1     # +1 for the "…more" line
    assert "6 more" in rendered


def test_no_evidence_reads_as_absent_not_empty():
    assert "no evidence" in cog.format_evidence([])


# --- refusals leave the card live ---------------------------------------------


def _decide_raises(mocker, exc):
    mocker.patch.object(cog.outreach_intake, "decide", side_effect=exc)
    return mocker.patch.object(cog.OutreachIntakeCog, "_disable_card", new=AsyncMock())


def test_capacity_refusal_does_not_consume_the_decision(mocker):
    # THE behaviour that matters. Capacity full is "not yet" — greying the
    # buttons would silently drop a target that should be admitted once a slot
    # drains (§8 / D1's re-queue branch).
    disable = _decide_raises(mocker, intake.CapacityFullError("cold capacity is full (15/15)"))
    ix = _interaction(4242)
    asyncio.run(_cog().finish_decision(ix, 7, "work"))
    disable.assert_not_awaited()
    assert "15/15" in ix.response.send_message.call_args.args[0]


def test_not_ready_refusal_does_not_consume_the_decision(mocker):
    disable = _decide_raises(mocker, intake.NotReadyToWorkError("function_state not set"))
    ix = _interaction(4242)
    asyncio.run(_cog().finish_decision(ix, 7, "work"))
    disable.assert_not_awaited()
    assert "Cannot work this yet" in ix.response.send_message.call_args.args[0]


def test_an_unexpected_error_reports_rather_than_silently_failing(mocker):
    disable = _decide_raises(mocker, RuntimeError("boom"))
    ix = _interaction(4242)
    asyncio.run(_cog().finish_decision(ix, 7, "work"))
    disable.assert_not_awaited()
    assert "check #system" in ix.response.send_message.call_args.args[0]


def test_a_second_click_reports_already_decided(mocker):
    mocker.patch.object(cog.outreach_intake, "decide", return_value=None)
    disable = mocker.patch.object(cog.OutreachIntakeCog, "_disable_card", new=AsyncMock())
    ix = _interaction(4242)
    asyncio.run(_cog().finish_decision(ix, 7, "work"))
    disable.assert_not_awaited()
    assert "already decided" in ix.response.send_message.call_args.args[0]


# --- successful decisions ------------------------------------------------------


def _decide_returns(mocker, status, touches=()):
    mocker.patch.object(
        cog.outreach_intake, "decide",
        return_value={"status": status, "target": TARGET, "touches": list(touches)},
    )
    return mocker.patch.object(cog.OutreachIntakeCog, "_disable_card", new=AsyncMock())


def test_working_a_target_reports_the_touch_count_and_closes_the_card(mocker):
    disable = _decide_returns(
        mocker, "in_sequence", [{"id": i, "skip_reason": None} for i in range(5)]
    )
    ix = _interaction(4242)
    asyncio.run(_cog().finish_decision(ix, 7, "work"))
    disable.assert_awaited_once()
    msg = ix.response.send_message.call_args.args[0]
    assert "Cadence Health" in msg and "5 touches" in msg


def test_late_admission_says_how_many_windows_were_already_past(mocker):
    # Admitted on day 40, slots 1-3 are created pre-skipped. Saying so avoids
    # the operator wondering why only two touches are due.
    _decide_returns(mocker, "in_sequence", [
        {"id": 1, "skip_reason": "admitted_after_window"},
        {"id": 2, "skip_reason": "admitted_after_window"},
        {"id": 3, "skip_reason": None},
    ])
    ix = _interaction(4242)
    asyncio.run(_cog().finish_decision(ix, 7, "work"))
    assert "2 already past their window" in ix.response.send_message.call_args.args[0]


def test_dropping_says_the_evidence_history_is_kept(mocker):
    _decide_returns(mocker, "dropped")
    ix = _interaction(4242)
    asyncio.run(_cog().finish_decision(ix, 7, "drop"))
    assert "history is kept" in ix.response.send_message.call_args.args[0]


def test_watchlisting_names_who_watches_it(mocker):
    _decide_returns(mocker, "watchlist")
    ix = _interaction(4242)
    asyncio.run(_cog().finish_decision(ix, 7, "watchlist"))
    assert "Trent Crimm" in ix.response.send_message.call_args.args[0]
