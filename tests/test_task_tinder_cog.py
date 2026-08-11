"""Cog-level tests for Task Tinder's identity guard + card (Phase 5).

The decision core (state machine / mappings) is tested in `test_task_tinder.py`.
Here: the `_authorized` guard (allow operator, deny foreigner loudly, fail-closed
when unset) and the card renders the candidate — no live bot or DB. Guarded by
importorskip('discord').
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("discord")

from agents.discord_bot.cogs import task_tinder as cog  # noqa: E402


def _interaction(user_id: int) -> MagicMock:
    ix = MagicMock()
    ix.user.id = user_id
    ix.response.is_done.return_value = False
    ix.response.send_message = AsyncMock()
    ix.followup.send = AsyncMock()
    return ix


def _cog() -> cog.TaskTinderCog:
    return cog.TaskTinderCog(MagicMock())


def test_authorized_allows_the_operator(mocker):
    mocker.patch.object(cog, "OPERATOR_ID", 999)
    ix = _interaction(999)
    assert asyncio.run(_cog()._authorized(ix, candidate_id=1)) is True
    ix.response.send_message.assert_not_called()


def test_authorized_denies_a_foreigner_loudly(mocker):
    mocker.patch.object(cog, "OPERATOR_ID", 999)
    warn = mocker.patch.object(cog.logger, "warning")
    ix = _interaction(12345)
    assert asyncio.run(_cog()._authorized(ix, candidate_id=7)) is False
    ix.response.send_message.assert_called_once()
    warn.assert_called_once()


def test_authorized_fails_closed_when_operator_unset(mocker):
    mocker.patch.object(cog, "OPERATOR_ID", 0)
    ix = _interaction(999)
    assert asyncio.run(_cog()._authorized(ix, candidate_id=1)) is False


def test_build_card_shows_action_and_candidate_id():
    card = cog.build_card(
        {"id": 5, "proposed_action": "Share X", "evidence_text": "why",
         "source_type": "discovery", "confidence": 0.7}
    )
    assert card.description == "Share X"
    assert "candidate #5" in card.footer.text
