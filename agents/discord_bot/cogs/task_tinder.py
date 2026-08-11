"""Task Tinder cog — surface `task_candidates` in `#task-tinder` (Phase 5).

Producers (Tartt discovery today; capture/meetings later) write **pending**
`task_candidates`. This cog posts each as a card with **Accept / Decline / Defer**
buttons; an **Accept promotes** it into a `follow_up` + a linked `tasks` row
(`agents/_lib/task_tinder`). That closes the round-trip Phase 4 exists to trial.

Mirrors the approvals cog: persistent Views (`timeout=None`, stable `custom_id`s)
re-attached on startup so cards survive a restart, a poller that posts rows any
agent enqueues, and DB-guarded idempotency (`decide` updates `WHERE status='pending'`,
so a double-click promotes exactly once). The operator-identity guard (SEC-2)
applies here too — these write state rather than acting outbound, so lower
consequence, but the guard is the same line.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands, tasks

from agents._lib import task_tinder
from agents.discord_bot.config import OPERATOR_DISCORD_ID, TASK_TINDER_CHANNEL_ID

logger = logging.getLogger(__name__)

# The only account allowed to decide (PRD-b2 A1, applied to Task Tinder too).
OPERATOR_ID = OPERATOR_DISCORD_ID
# owner for promoted tasks/follow_ups (the operator's own queue).
DEFAULT_OWNER = "operator"

# custom_id prefixes — stable across restarts so persistent Views re-bind.
_ACCEPT = "tinder:accept:"
_DECLINE = "tinder:decline:"
_DEFER = "tinder:defer:"


def build_card(candidate: dict) -> discord.Embed:
    """The #task-tinder card for one pending candidate."""
    embed = discord.Embed(
        title="Task candidate",
        description=candidate["proposed_action"],
        color=discord.Color.blurple(),
    )
    if candidate.get("evidence_text"):
        embed.add_field(name="Why", value=str(candidate["evidence_text"])[:1024], inline=False)
    src = candidate.get("source_type", "?")
    conf = candidate.get("confidence", 0) or 0
    embed.add_field(name="Source", value=f"{src} · confidence {conf:.2f}", inline=False)
    embed.set_footer(text=f"candidate #{candidate['id']}")
    return embed


class TaskTinderView(discord.ui.View):
    """Persistent (timeout=None) 3-button view for one candidate."""

    def __init__(self, cog: TaskTinderCog, candidate_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.candidate_id = candidate_id

        accept = discord.ui.Button(
            label="Accept", style=discord.ButtonStyle.success,
            custom_id=f"{_ACCEPT}{candidate_id}",
        )
        decline = discord.ui.Button(
            label="Decline", style=discord.ButtonStyle.danger,
            custom_id=f"{_DECLINE}{candidate_id}",
        )
        defer = discord.ui.Button(
            label="Defer", style=discord.ButtonStyle.secondary,
            custom_id=f"{_DEFER}{candidate_id}",
        )
        accept.callback = self._accept
        decline.callback = self._decline
        defer.callback = self._defer
        self.add_item(accept)
        self.add_item(decline)
        self.add_item(defer)

    async def _accept(self, interaction: discord.Interaction) -> None:
        if await self.cog._authorized(interaction, self.candidate_id):
            await self.cog.finish_decision(interaction, self.candidate_id, "accept")

    async def _decline(self, interaction: discord.Interaction) -> None:
        if await self.cog._authorized(interaction, self.candidate_id):
            await self.cog.finish_decision(interaction, self.candidate_id, "decline")

    async def _defer(self, interaction: discord.Interaction) -> None:
        if await self.cog._authorized(interaction, self.candidate_id):
            await self.cog.finish_decision(interaction, self.candidate_id, "defer")


class TaskTinderCog(commands.Cog):
    """Posts candidate cards and promotes accepted ones to tasks + follow_ups."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._reattached = False

    async def cog_load(self) -> None:
        self._poll.start()

    async def cog_unload(self) -> None:
        self._poll.cancel()

    # --- posting / re-attaching ------------------------------------------

    @tasks.loop(seconds=60)
    async def _poll(self) -> None:
        channel = self.bot.get_channel(TASK_TINDER_CHANNEL_ID)
        if channel is None:
            return  # not connected yet; try again next tick
        try:
            rows = await asyncio.to_thread(task_tinder.list_undelivered)
        except Exception:
            logger.exception("task_tinder poll: failed to list undelivered candidates")
            return
        for row in rows:
            await self._post_card(channel, row)

    @_poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()
        if not self._reattached:
            await self._reattach_views()
            self._reattached = True

    async def _reattach_views(self) -> None:
        try:
            rows = await asyncio.to_thread(task_tinder.list_pending_posted)
        except Exception:
            logger.exception("task_tinder: failed to list pending-posted candidates")
            return
        for row in rows:
            msg_id = row["discord_message_id"]
            if not msg_id:
                continue
            self.bot.add_view(TaskTinderView(self, row["id"]), message_id=int(msg_id))
        logger.info("task_tinder: re-attached %d persistent view(s)", len(rows))

    async def _post_card(self, channel: discord.abc.Messageable, row: dict) -> None:
        cid = row["id"]
        try:
            message = await channel.send(embed=build_card(row), view=TaskTinderView(self, cid))
            await asyncio.to_thread(task_tinder.mark_posted, cid, message.id)
            logger.info("task_tinder: posted card for candidate #%s", cid)
        except Exception:
            logger.exception("task_tinder: failed to post card for #%s", cid)

    # --- decision handling -----------------------------------------------

    async def _authorized(self, interaction: discord.Interaction, candidate_id: int) -> bool:
        """True iff the clicking user is the configured operator (denies loudly)."""
        if OPERATOR_ID != 0 and interaction.user.id == OPERATOR_ID:
            return True
        logger.warning(
            "task_tinder_denied user=%s candidate=%s", interaction.user.id, candidate_id
        )
        await self._reply(interaction, "⛔ Not authorized to decide task candidates.")
        return False

    async def finish_decision(
        self, interaction: discord.Interaction, candidate_id: int, action: str
    ) -> None:
        """Transition the candidate (guarded), then promote it on accept."""
        result = await asyncio.to_thread(task_tinder.decide, candidate_id, action)
        if result is None:
            await interaction.response.send_message(
                f"Candidate #{candidate_id} was already decided.", ephemeral=True
            )
            return

        await self._disable_card(interaction)

        if result["status"] not in task_tinder.PROMOTE_STATUSES:
            verb = "🚫 Declined" if action == "decline" else "⏰ Deferred"
            await self._reply(interaction, f"{verb} candidate #{candidate_id}.")
            return

        try:
            ids = await asyncio.to_thread(
                task_tinder.promote, result["candidate"], owner=DEFAULT_OWNER
            )
        except Exception as e:
            logger.exception("task_tinder: promote failed for #%s", candidate_id)
            await self._reply(
                interaction,
                f"⚠️ Accepted #{candidate_id} but promotion failed: `{e}` — check #system.",
            )
            return

        await self._reply(
            interaction,
            f"✅ Accepted #{candidate_id} → task #{ids['task_id']} "
            f"(follow-up #{ids['follow_up_id']}).",
        )

    async def _disable_card(self, interaction: discord.Interaction) -> None:
        message = interaction.message
        if message is None:
            return
        view = discord.ui.View.from_message(message)
        for child in view.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        try:
            await message.edit(view=view)
        except discord.HTTPException:
            pass

    async def _reply(self, interaction: discord.Interaction, content: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)
        except discord.HTTPException:
            logger.exception("task_tinder: failed to reply to interaction")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TaskTinderCog(bot))
