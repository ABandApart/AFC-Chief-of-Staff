"""Cog-object tests for the O2 re-score surface — drive the REAL objects.

Every Gate 0 bug was a "constructed but never driven" gap, so these build the
actual modal/view/card and drive the submit path: the `RadioGroup.value` (not
`.values`) read, a stable custom_id that re-binds after a restart, and a submit
that writes S4/S5 and confirms — plus the refusal when a signal is left unset.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

from agents.discord_bot.cogs import outreach_rescore as cog

ROW = {
    "candidate_id": 9, "target_id": 5, "company_name": "AIIR Consulting",
    "discord_message_id": "123", "s4_leadership_gap": 3, "s5_team_build_below": 1,
    "score": 17, "treatment": "watch", "signals_observed_at": None,
    "evidence_text": "AIIR: leadership judgement last set never — over 30 days",
}


def test_selected_reads_value_not_values():
    # The Gate 0 bug: reading only `.values` returned None for every submit.
    assert cog._selected(SimpleNamespace(component=SimpleNamespace(value="5"))) == "5"
    only_values = SimpleNamespace(component=SimpleNamespace(value=None, values=["3"]))
    assert cog._selected(only_values) == "3"
    neither = SimpleNamespace(component=SimpleNamespace(value=None, values=[]))
    assert cog._selected(neither) is None


def test_modal_has_two_radiogroups_and_no_button():
    modal = cog.RescoreModal(MagicMock(), ROW)
    assert len(modal.children) == 2                       # S4 + S5, under the 5-child cap
    assert not any(isinstance(c, discord.ui.Button) for c in modal.children)


def test_view_button_custom_id_is_stable_per_candidate():
    button = cog.RescoreView(MagicMock(), 9).children[0]
    assert button.custom_id == "outreach:rescore:9"      # re-binds after a restart


def test_build_card_shows_company_current_and_target():
    embed = cog.build_card(ROW)
    body = embed.title + " " + " ".join(f.value for f in embed.fields)
    assert "AIIR Consulting" in embed.title
    assert "17/25" in body and "watch" in body
    assert "target #5" in embed.footer.text


def _interaction():
    ix = MagicMock()
    ix.response.is_done.return_value = False
    ix.response.send_message = AsyncMock()
    return ix


def test_modal_submit_writes_s4_s5_and_confirms(mocker):
    mocker.patch.object(cog, "_selected", side_effect=["5", "3"])
    modal = cog.RescoreModal(MagicMock(), ROW)
    modal.cog._apply = MagicMock(return_value={
        "company_name": "AIIR Consulting", "score": 21, "treatment": "work"})
    modal.cog._disable_card = AsyncMock()
    ix = _interaction()

    asyncio.run(modal.on_submit(ix))

    modal.cog._apply.assert_called_once_with(9, 5, 5, 3)   # candidate, target, s4, s5
    msg = ix.response.send_message.call_args.args[0]
    assert "Re-scored" in msg and "work" in msg
    modal.cog._disable_card.assert_awaited_once_with("123")


def test_modal_submit_refuses_when_a_signal_is_unset(mocker):
    mocker.patch.object(cog, "_selected", side_effect=["5", None])
    modal = cog.RescoreModal(MagicMock(), ROW)
    modal.cog._apply = MagicMock()
    ix = _interaction()

    asyncio.run(modal.on_submit(ix))

    modal.cog._apply.assert_not_called()                  # nothing written on a partial
    assert "both S4 and S5" in ix.response.send_message.call_args.args[0]


def test_modal_submit_reports_already_rescored(mocker):
    mocker.patch.object(cog, "_selected", side_effect=["1", "1"])
    modal = cog.RescoreModal(MagicMock(), ROW)
    modal.cog._apply = MagicMock(return_value=None)       # candidate already decided
    modal.cog._disable_card = AsyncMock()
    ix = _interaction()

    asyncio.run(modal.on_submit(ix))

    assert "Already re-scored" in ix.response.send_message.call_args.args[0]
