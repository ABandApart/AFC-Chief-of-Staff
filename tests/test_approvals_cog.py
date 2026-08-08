"""Cog-level tests for the #approvals identity guard (PRD-b2 Amendment 1).

The pure decision logic (is_authorized / requires_typed_confirm / confirmation_ok)
is tested in `test_approvals.py`. Here we check the thin Discord wiring: the
`_authorized` gate allows only the configured operator, denies everyone else
*loudly* (ephemeral reply + `approval_denied` log), and fails closed while the
operator id is unset — without needing a live bot or gateway.

Guarded by `importorskip('discord')` so an env without discord.py skips cleanly.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("discord")

from agents.discord_bot.cogs import approvals as cog  # noqa: E402


def _interaction(user_id: int) -> MagicMock:
    ix = MagicMock()
    ix.user.id = user_id
    ix.response.is_done.return_value = False
    ix.response.send_message = AsyncMock()
    ix.followup.send = AsyncMock()
    return ix


def _cog() -> cog.ApprovalsCog:
    return cog.ApprovalsCog(MagicMock())


def test_authorized_allows_the_operator(mocker):
    mocker.patch.object(cog, "OPERATOR_ID", 999)
    ix = _interaction(999)
    assert asyncio.run(_cog()._authorized(ix, row_id=1)) is True
    ix.response.send_message.assert_not_called()  # no denial reply


def test_authorized_denies_a_foreigner_loudly(mocker):
    mocker.patch.object(cog, "OPERATOR_ID", 999)
    warn = mocker.patch.object(cog.logger, "warning")
    ix = _interaction(12345)
    assert asyncio.run(_cog()._authorized(ix, row_id=7)) is False
    ix.response.send_message.assert_called_once()  # ephemeral "not authorized"
    warn.assert_called_once()  # security event logged (→ #system)
    assert warn.call_args.args[0] == "approval_denied user=%s row=%s"


def test_authorized_fails_closed_when_operator_unset(mocker):
    # Even a plausible user id is denied while the allowlist is unconfigured.
    mocker.patch.object(cog, "OPERATOR_ID", 0)
    ix = _interaction(999)
    assert asyncio.run(_cog()._authorized(ix, row_id=1)) is False
    ix.response.send_message.assert_called_once()
