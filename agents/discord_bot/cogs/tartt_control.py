"""Tartt control cog — operator-only conversational feed/interest management.

Listens in #briefing. When the operator addresses Tartt (by @-mention or the
"Tartt, …" name prefix), it runs the message through `_lib/tartt_control` — one
Haiku call to read intent, then a feed (`sources`) or interest (graph) change —
and replies with what changed. Logic lives in `_lib/tartt_control` so it is
testable without Discord; this is the thin caller (⏳ → ✅/⚠️), like the capture cog.

**Instruction-source boundary (load-bearing):** it acts ONLY on the operator's
own messages (`author.id == OPERATOR_DISCORD_ID`) and never on bot posts. Tartt
posts untrusted newsletter summaries into this same channel, so obeying anyone
but the operator would let ingested content rewrite the feed list. Non-operator
and un-addressed messages are dropped before any LLM call — no cost, no action.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from agents._lib import tartt_control
from agents.discord_bot.config import BRIEFING_CHANNEL_ID, OPERATOR_DISCORD_ID

logger = logging.getLogger(__name__)

OPERATOR_ID = OPERATOR_DISCORD_ID


class TarttControlCog(commands.Cog):
    """Operator-only #briefing listener for managing Tartt's feeds and interests."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _command_text(self, message: discord.Message) -> str | None:
        """The command, if this message addresses Tartt (mention or name prefix);
        else None so the listener ignores it without spending an LLM call."""
        if self.bot.user is not None and self.bot.user in message.mentions:
            stripped = message.content
            for token in (f"<@{self.bot.user.id}>", f"<@!{self.bot.user.id}>"):
                stripped = stripped.replace(token, " ")
            return stripped.strip() or None
        return tartt_control.strip_prefix(message.content)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.channel.id != BRIEFING_CHANNEL_ID:
            return
        if message.author.bot:
            return
        # SAFETY: only the operator drives Tartt. Everything else in this channel
        # (bot summaries included) is data, not a command.
        if OPERATOR_ID and message.author.id != OPERATOR_ID:
            return

        text = self._command_text(message)
        if not text:
            return  # not addressed to Tartt — stay silent, no LLM call

        await message.add_reaction("⏳")
        try:
            reply = await asyncio.to_thread(tartt_control.handle, text)
            await _safe_remove_hourglass(message, self.bot)
            await message.add_reaction("✅")
            await message.reply(reply, mention_author=False)
        except Exception:
            await _safe_remove_hourglass(message, self.bot)
            await message.add_reaction("⚠️")
            await message.reply(
                "Something went wrong with that Tartt command — check the logs / #system.",
                mention_author=False,
            )
            logger.exception("tartt-control failed on msg %s", message.id)


async def _safe_remove_hourglass(message: discord.Message, bot: commands.Bot) -> None:
    """Remove the ⏳ reaction, ignoring errors (it may already be gone)."""
    try:
        await message.remove_reaction("⏳", bot.user)
    except discord.HTTPException:
        pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TarttControlCog(bot))
