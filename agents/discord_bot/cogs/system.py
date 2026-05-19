"""System cog — startup announcements and (later) health/alert forwarding.

In sub-phase 3.1 this cog has exactly one responsibility: post a single
"online" message to `#system` when the bot connects.

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

import logging
from datetime import datetime, timezone

from discord.ext import commands

from agents.discord_bot.config import SYSTEM_CHANNEL_ID

logger = logging.getLogger(__name__)


class SystemCog(commands.Cog):
    """Posts startup announcement; placeholder for later health/alert work."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._announced = False

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
