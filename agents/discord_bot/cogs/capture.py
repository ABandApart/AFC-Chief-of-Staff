"""Capture cog — #capture listener → graph memory.

A thin Discord caller for the channel-agnostic ingest core
(`agents/_lib/ingest.ingest_note`): react ⏳, ingest, react ✅/⚠️.

(W5: the ingest logic moved to `_lib/ingest` so the primary API ingestion path
shares it — operator 2026-07-28: API + tools are the primary ingestion channel;
Discord is one caller. `configure_cognee()` runs at bot startup, see `run.py`.)
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from agents._lib import ingest
from agents.discord_bot.config import CAPTURE_CHANNEL_ID

logger = logging.getLogger(__name__)


class CaptureCog(commands.Cog):
    """Listens to #capture and ingests notes into the graph."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.channel.id != CAPTURE_CHANNEL_ID:
            return
        if message.author.bot:
            return

        text = message.content.strip()
        if not text:
            await message.reply(
                "I need text to capture — send a sentence (a link by itself isn't enough).",
                mention_author=False,
            )
            return

        await message.add_reaction("⏳")
        try:
            result = await ingest.ingest_note(
                text, source_ref=str(message.id), source_type="discord"
            )
            await _safe_remove_hourglass(message, self.bot)
            await message.add_reaction("✅")
            if result == "repost":
                await message.reply(
                    "Already captured — I've seen this exact note before; nothing new written.",
                    mention_author=False,
                )
            else:
                await message.reply(
                    "Captured to memory — use `/recall` to find it later.",
                    mention_author=False,
                )
        except Exception:
            await _safe_remove_hourglass(message, self.bot)
            await message.add_reaction("⚠️")
            await message.reply(
                "Something went wrong capturing that — check the logs / #system.",
                mention_author=False,
            )
            logger.exception("capture failed on msg %s", message.id)


async def _safe_remove_hourglass(message: discord.Message, bot: commands.Bot) -> None:
    """Remove the ⏳ reaction, ignoring errors (it may already be gone)."""
    try:
        await message.remove_reaction("⏳", bot.user)
    except discord.HTTPException:
        pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CaptureCog(bot))
