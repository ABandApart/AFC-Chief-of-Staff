"""System cog — startup announcements, the bot's liveness ping, and (later)
health/alert forwarding.

Two responsibilities: post a single "online" message to `#system` when the bot
connects, and ping the `cos-bot` dead-man's switch every 5 minutes while
connected (PRD-liveness-alerting).

Discord's `on_ready` event fires on every reconnect (network blip, gateway
reshard, etc.). We deduplicate via `_announced` so a reconnect doesn't
spam #system with duplicate startup messages. The first announcement per
process is what we want.

Future sub-phases (3.3+, Phase 11) extend this cog to:
- accept alert messages from other agents (Ted's health checks, error
  forwarding from launchd jobs)
- maintain a pinned status message edited every 6 hours
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from discord.ext import commands, tasks

from agents._lib import heartbeat
from agents.discord_bot.config import SYSTEM_CHANNEL_ID

logger = logging.getLogger(__name__)

# The bot's dead-man's switch (`cos-bot`, PRD-liveness-alerting). Pinged only
# while the bot is connected: a process that is alive but disconnected from
# Discord is down from the operator's point of view.
BOT_SLUG = "cos-bot"
BOT_BEAT_SECONDS = 300


class SystemCog(commands.Cog):
    """Posts startup announcement; placeholder for later health/alert work."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._announced = False

    async def cog_load(self) -> None:
        self.liveness.start()

    async def cog_unload(self) -> None:
        self.liveness.cancel()

    def is_connected(self) -> bool:
        """Ready and not closed — the condition `cos-bot` reports on."""
        return self.bot.is_ready() and not self.bot.is_closed()

    async def beat(self) -> bool:
        """Ping `cos-bot` if connected. Returns whether a ping was attempted.

        `heartbeat.ping` blocks on the network, so it runs off the event loop;
        it never raises.
        """
        if not self.is_connected():
            return False
        await asyncio.to_thread(heartbeat.ping, BOT_SLUG)
        return True

    @tasks.loop(seconds=BOT_BEAT_SECONDS)
    async def liveness(self) -> None:
        await self.beat()

    @liveness.before_loop
    async def _before_liveness(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        if self._announced:
            logger.info("on_ready fired again (reconnect) — skipping announce.")
            return

        channel = self.bot.get_channel(SYSTEM_CHANNEL_ID)
        if channel is None:
            logger.error(
                "Could not find #system channel (id=%s). "
                "Check config.py and bot permissions on AFC Richmond.",
                SYSTEM_CHANNEL_ID,
            )
            return

        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        await channel.send(f"🟢 AI Adaptive CoS bot online — {ts}")
        self._announced = True
        logger.info(
            "Posted startup message to #%s (id=%s)",
            channel.name,
            SYSTEM_CHANNEL_ID,
        )


async def setup(bot: commands.Bot) -> None:
    """Cog entry point called by discord.py's load_extension()."""
    await bot.add_cog(SystemCog(bot))
