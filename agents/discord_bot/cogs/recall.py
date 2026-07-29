"""Recall cog — the `/recall` slash command.

Graph-native (W5): forwards the query to cognee GRAPH_COMPLETION
(`agents/_lib/graph_recall`) and returns the synthesized answer, ephemerally.
`configure_cognee()` runs at bot startup (`run.py`).
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from agents._lib import graph_recall

logger = logging.getLogger(__name__)

MAX_REPLY_CHARS = 1900  # Discord's 2000 cap, with headroom


class RecallCog(commands.Cog):
    """Provides /recall."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="recall", description="Ask the brain what it knows")
    @app_commands.describe(query="What to look for")
    async def recall(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            answer = await graph_recall.recall(query)
        except Exception:
            logger.exception("recall failed for query %r", query)
            await interaction.followup.send(
                "⚠️ Recall failed — check the logs / #system.", ephemeral=True
            )
            return

        logger.info("recall query %r → %d chars", query, len(answer))
        if len(answer) > MAX_REPLY_CHARS:
            answer = answer[:MAX_REPLY_CHARS] + "…"
        await interaction.followup.send(answer, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RecallCog(bot))
