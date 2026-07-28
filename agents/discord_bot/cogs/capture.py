"""Capture cog — #capture listener → cognee graph memory (W4 pivot).

Flow:
  1. Operator posts a thought in #capture. Bot reacts ⏳.
  2. Message-level dedup: the normalized raw text is hashed and checked against
     `capture_messages`. An exact re-post short-circuits here, before any
     cognee/LLM spend.
  3. `cognee.add(text)` + `cognify()` build the note into the graph — cognee's
     LLM extracts the entities and relationships (people, orgs, workflows, …)
     and resolves them against what's already there. Run under a `labeled()`
     block so the spend lands in `agent_runs` (agent `fact-extraction`, via the
     M1 litellm callback).
  4. Record the message hash; ⏳ → ✅.

W4 replaced the forced-tool fact extraction + `facts`-table write with cognee
ingestion. Extraction/resolution is now cognee's, so capture no longer produces
a discrete fact list — the reply is a simple confirmation (the note is
recallable via `/recall`). The typed ontology (`agents/_lib/ontology`) is used
by the structured agents (meetings, content), not free-text capture.

`configure_cognee()` must have run at bot startup (see `run.py`) before the
first capture. cognee is imported lazily so this module imports without it.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

import discord
from discord.ext import commands

from agents._lib.telemetry_context import labeled
from agents.discord_bot import brain
from agents.discord_bot.config import CAPTURE_CHANNEL_ID

logger = logging.getLogger(__name__)

CAPTURE_DATASET = "capture"


def message_hash(text: str) -> str:
    """sha256 hex of the normalized message text (message-level dedup key).

    Normalization: collapse all whitespace runs and casefold, so re-posts that
    differ only in spacing/case still count as the same message.
    """
    normalized = " ".join(text.split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class CaptureCog(commands.Cog):
    """Listens to #capture and ingests notes into the graph."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _ingest(self, text: str, message_id: str) -> str:
        """Ingest one note. Returns 'repost' (seen before, nothing done) or
        'captured'. Raises on failure (recorded/handled by the caller)."""
        msg_hash = message_hash(text)
        if await asyncio.to_thread(brain.capture_message_seen, msg_hash):
            logger.info(
                "capture msg %s: exact re-post (hash %s…) — skipped before cognify",
                message_id, msg_hash[:12],
            )
            return "repost"

        import cognee  # lazy — optional `cognee` dependency group

        with labeled(
            "fact-extraction", "customer_discovery",
            trigger_kind="event", correlation_id=message_id,
        ):
            await cognee.add(text, dataset_name=CAPTURE_DATASET)
            await cognee.cognify(datasets=[CAPTURE_DATASET])

        # Record the hash only after a successful cognify, so a failed capture
        # can be retried verbatim.
        await asyncio.to_thread(brain.record_capture_message, msg_hash, message_id)
        logger.info("capture msg %s: cognified into the graph", message_id)
        return "captured"

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
            result = await self._ingest(text, str(message.id))
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
