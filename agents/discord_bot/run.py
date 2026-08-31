"""Discord bot entry point.

Run as barry-agent (the runtime account where `discord-bot-token` lives in
keychain):

    cd ~/agents
    uv run python -m agents.discord_bot.run

Handles SIGTERM cleanly (required for launchd supervision in 3.5): the
handler closes the Discord client, in-flight work drains, and the pool is
shut down. Ctrl+C (SIGINT) exits the same way via KeyboardInterrupt.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

import discord
from discord.ext import commands

from agents._lib import db
from agents._lib.creds import keychain_get
from agents.discord_bot.config import GUILD_ID

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


class CosBot(commands.Bot):
    """AFC Richmond Chief-of-Staff bot.

    Loads cogs via setup_hook (the discord.py 2.x pattern for async init).
    """

    async def setup_hook(self) -> None:
        # Configure cognee (dedicated DB, M1 routing) + install the litellm
        # telemetry callback BEFORE any cog can call cognee (capture ingests
        # into the graph). W4.
        from agents._lib import cognee_setup
        cognee_setup.configure_cognee()

        await self.load_extension("agents.discord_bot.cogs.system")
        await self.load_extension("agents.discord_bot.cogs.capture")
        await self.load_extension("agents.discord_bot.cogs.outcomes")
        await self.load_extension("agents.discord_bot.cogs.recall")
        await self.load_extension("agents.discord_bot.cogs.approvals")
        await self.load_extension("agents.discord_bot.cogs.task_tinder")
        await self.load_extension("agents.discord_bot.cogs.outreach_intake")
        await self.load_extension("agents.discord_bot.cogs.outreach_discovery")
        await self.load_extension("agents.discord_bot.cogs.outreach_rescore")
        # Sync app (slash) commands to our single guild for instant
        # availability (global sync lags ~1h). copy_global_to moves the
        # cog-registered commands into the guild scope, then sync registers.
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        logger.info(
            "Cogs loaded (system, capture, outcomes, recall, approvals, task_tinder, "
            "outreach_intake, outreach_discovery, outreach_rescore); synced "
            "%d app command(s) to guild %s; connecting to Discord...",
            len(synced),
            GUILD_ID,
        )


async def _amain() -> None:
    intents = discord.Intents.default()
    # message_content: required for #capture (3.2)
    # members:         useful for future multi-user features; harmless now
    intents.message_content = True
    intents.members = True

    bot = CosBot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready() -> None:
        logger.info(
            "Logged in as %s (id: %s); connected to %d guild(s): %s",
            bot.user,
            bot.user.id if bot.user else "<unknown>",
            len(bot.guilds),
            [g.name for g in bot.guilds],
        )

    loop = asyncio.get_running_loop()

    def _on_sigterm() -> None:
        logger.info("SIGTERM received — closing bot...")
        asyncio.ensure_future(bot.close())

    loop.add_signal_handler(signal.SIGTERM, _on_sigterm)

    async with bot:
        await bot.start(keychain_get("discord-bot-token"))


def main() -> int:
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        logger.info("Shutdown via Ctrl+C")
    finally:
        db.close_pool()

    logger.info("Bot shutting down...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
