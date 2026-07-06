"""Recall cog — the `/recall` slash command.

Capture lives in Discord; until now recall required a shell on the Mac mini.
This cog closes the loop: same hybrid search as `cli/recall.py` (shared core
in `agents/_lib/search.py`), surfaced where capture happens.

Costs one query embedding per invocation (recall agent, ~$0.000002); results
are ephemeral so the channel isn't cluttered.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from agents._lib import db, search

logger = logging.getLogger(__name__)

# Discord message hard limit is 2000 chars; leave headroom.
MAX_REPLY_CHARS = 1900


class RecallCog(commands.Cog):
    """Provides /recall."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _search(self, query: str, limit: int) -> list[dict[str, Any]]:
        """Blocking pipeline: embed the query, run hybrid search."""
        qvec = search.embed_query(query, trigger_kind="event")
        with db.connection() as conn:
            return search.run_query(conn, query, qvec, limit=limit)

    @app_commands.command(name="recall", description="Search captured facts")
    @app_commands.describe(
        query="What to look for",
        limit="Max results (default 5)",
    )
    async def recall(
        self,
        interaction: discord.Interaction,
        query: str,
        limit: app_commands.Range[int, 1, 20] = 5,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            rows = await asyncio.to_thread(self._search, query, limit)
        except Exception:
            logger.exception("recall failed for query %r", query)
            await interaction.followup.send(
                "⚠️ Recall failed — check the logs / #system.", ephemeral=True
            )
            return

        logger.info("recall query %r → %d result(s)", query, len(rows))
        text = search.format_results(query, rows)
        if len(text) > MAX_REPLY_CHARS:
            text = text[:MAX_REPLY_CHARS] + "…"
        await interaction.followup.send(text, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RecallCog(bot))
