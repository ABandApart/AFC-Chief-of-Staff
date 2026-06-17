"""Discord bot entry point.

Run as barry-agent (the runtime account where `discord-bot-token` lives in
keychain):

    cd ~/agents
    uv run python -m agents.discord_bot.run

Sub-phase 3.1 scope: connect, announce in #system, stay running.
- No message listeners
- No slash commands
- No DB writes
- No launchd (manual run only; launchd plist arrives in 3.5)

Press Ctrl+C to exit cleanly.
"""

from __future__ import annotations

import logging
import subprocess
import sys

import discord
from discord.ext import commands

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def keychain_get(item_name: str) -> str:
    """Fetch a credential from the current user's macOS Keychain."""
    result = subprocess.run(
        ["security", "find-generic-password", "-w", "-s", item_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Keychain item '{item_name}' not found. "
            f"Run scripts/keychain_setup.sh."
        )
    return result.stdout.strip()


class CosBot(commands.Bot):
    """AFC Richmond Chief-of-Staff bot.

    Loads cogs via setup_hook (the discord.py 2.x pattern for async init).
    """

    async def setup_hook(self) -> None:
        await self.load_extension("agents.discord_bot.cogs.system")
        await self.load_extension("agents.discord_bot.cogs.capture")
        logger.info("Cogs loaded (system, capture); connecting to Discord...")


def main() -> int:
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

    try:
        # bot.run() handles asyncio.run() + signal handling internally.
        bot.run(keychain_get("discord-bot-token"))
    except KeyboardInterrupt:
        logger.info("Shutdown via Ctrl+C")

    logger.info("Bot shutting down...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
