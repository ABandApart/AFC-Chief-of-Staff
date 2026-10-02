"""Unit tests for the bot's `cos-bot` liveness ping (PRD-liveness-alerting §2).
No live bot: the bot is a mock. Guarded by importorskip('discord')."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

pytest.importorskip("discord")

from agents.discord_bot.cogs import system  # noqa: E402


def _cog(*, ready: bool, closed: bool) -> system.SystemCog:
    bot = MagicMock()
    bot.is_ready.return_value = ready
    bot.is_closed.return_value = closed
    return system.SystemCog(bot)


def test_beat_pings_when_connected(mocker):
    ping = mocker.patch("agents.discord_bot.cogs.system.heartbeat.ping")
    assert asyncio.run(_cog(ready=True, closed=False).beat()) is True
    ping.assert_called_once_with(system.BOT_SLUG)


@pytest.mark.parametrize("ready,closed", [(False, False), (True, True), (False, True)])
def test_beat_silent_when_not_connected(mocker, ready, closed):
    ping = mocker.patch("agents.discord_bot.cogs.system.heartbeat.ping")
    assert asyncio.run(_cog(ready=ready, closed=closed).beat()) is False
    ping.assert_not_called()


def test_liveness_loop_interval():
    assert system.SystemCog.liveness.seconds == system.BOT_BEAT_SECONDS == 300
