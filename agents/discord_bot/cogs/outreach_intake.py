"""Outreach intake cog — Gate 1 in `#task-tinder` (Track O, `35-` §5).

A target scored to `treatment='work'` posts **one** card with **Work this /
Watchlist / Drop**. "Work this" admits it to the arc: status → `in_sequence`,
`sequence_started_at` stamped, five touches materialised from the Selector.

**A separate cog from `task_tinder.py`, deliberately.** `50-channel-layer.md`
says outreach "reuses this exact pattern", and it does — same channel, persistent
Views, one-shot decision, buttons removed after the first click, idempotency
owned by the DB row, the SEC-2 operator guard. But it operates on
`outreach_targets`, not `task_candidates`: a different table, a different state
machine, and different button semantics. Branching one cog across two domains
would make every future change to either read as a change to both. The *pattern*
is shared; the module is not.

The decision logic lives in `agents/_lib/outreach_intake` so it is testable
without a bot; this file owns only the Discord surface.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands, tasks

from agents._lib import db, outreach_intake
from agents.discord_bot.config import OPERATOR_DISCORD_ID, TASK_TINDER_CHANNEL_ID

logger = logging.getLogger(__name__)

OPERATOR_ID = OPERATOR_DISCORD_ID

# custom_id prefixes — stable across restarts so persistent Views re-bind.
_WORK = "outreach:work:"
_WATCH = "outreach:watchlist:"
_DROP = "outreach:drop:"

# Evidence lines on the card. The packet is where the full picture lives; the
# card only needs enough to make one decision (§7's "scannable" discipline
# applied a step earlier).
MAX_EVIDENCE_LINES = 4


def format_evidence(evidence: list[dict]) -> str:
    """Evidence as short dated lines, freshness marked (`35-` §3 display rules)."""
    if not evidence:
        return "_no evidence observed yet_"
    marks = {"fresh": "", "ageing": " ⚠️", "stale": " ⛔ stale"}
    lines = []
    for fact in evidence[:MAX_EVIDENCE_LINES]:
        payload = fact.get("payload") or {}
        title = payload.get("title") or fact.get("fact_kind", "fact")
        mark = marks.get(fact.get("freshness", ""), "")
        lines.append(f"• {title} — open {fact['age_days']}d{mark}")
    if len(evidence) > MAX_EVIDENCE_LINES:
        lines.append(f"• _…{len(evidence) - MAX_EVIDENCE_LINES} more_")
    return "\n".join(lines)


def build_card(target: dict, evidence: list[dict], capacity: dict) -> discord.Embed:
    """The Gate 1 card (`35-` §5 mock)."""
    score = target.get("score")
    compound = " ⚡ COMPOUND SIGNAL" if target.get("compound_signal") else ""
    embed = discord.Embed(
        title=f"Outreach candidate — score {score}/25{compound}",
        description=" · ".join(
            p for p in (
                target["company_name"],
                (target.get("stage") or "stage unknown").replace("_", " ").title(),
                (target.get("function_state") or "function state not set").replace("_", " "),
            ) if p
        ),
        color=discord.Color.orange() if compound else discord.Color.blurple(),
    )
    embed.add_field(
        name="Trigger",
        value=f"{target['trigger_kind'].replace('_', ' ')} "
              f"({target['days_since_trigger']}d since {target['trigger_date']})",
        inline=False,
    )
    contact = target.get("contact_name") or "_no contact yet_"
    if role := target.get("contact_role"):
        contact = f"{contact}, {role}"
    embed.add_field(name="Contact", value=contact, inline=False)
    embed.add_field(name="Evidence", value=format_evidence(evidence), inline=False)
    embed.add_field(
        name="Capacity",
        value=f"{capacity['cold_live']} of {capacity['cold_ceiling']} cold live "
              f"· {capacity['reengagement_live']}/{capacity['reengagement_ceiling']} re-engagement",
        inline=False,
    )
    embed.set_footer(text=f"target #{target['id']}")
    return embed


class OutreachIntakeView(discord.ui.View):
    """Persistent (timeout=None) 3-button view for one target."""

    def __init__(self, cog: OutreachIntakeCog, target_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.target_id = target_id

        work = discord.ui.Button(
            label="Work this", style=discord.ButtonStyle.success,
            custom_id=f"{_WORK}{target_id}",
        )
        watch = discord.ui.Button(
            label="Watchlist", style=discord.ButtonStyle.secondary,
            custom_id=f"{_WATCH}{target_id}",
        )
        drop = discord.ui.Button(
            label="Drop", style=discord.ButtonStyle.danger,
            custom_id=f"{_DROP}{target_id}",
        )
        work.callback = self._work
        watch.callback = self._watchlist
        drop.callback = self._drop
        self.add_item(work)
        self.add_item(watch)
        self.add_item(drop)

    async def _work(self, interaction: discord.Interaction) -> None:
        if await self.cog._authorized(interaction, self.target_id):
            await self.cog.finish_decision(interaction, self.target_id, "work")

    async def _watchlist(self, interaction: discord.Interaction) -> None:
        if await self.cog._authorized(interaction, self.target_id):
            await self.cog.finish_decision(interaction, self.target_id, "watchlist")

    async def _drop(self, interaction: discord.Interaction) -> None:
        if await self.cog._authorized(interaction, self.target_id):
            await self.cog.finish_decision(interaction, self.target_id, "drop")


class OutreachIntakeCog(commands.Cog):
    """Posts Gate 1 cards and admits accepted targets into the arc."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._reattached = False

    async def cog_load(self) -> None:
        self._poll.start()

    async def cog_unload(self) -> None:
        self._poll.cancel()

    # --- posting / re-attaching ------------------------------------------

    @tasks.loop(seconds=120)
    async def _poll(self) -> None:
        channel = self.bot.get_channel(TASK_TINDER_CHANNEL_ID)
        if channel is None:
            return  # not connected yet; try again next tick
        try:
            rows, capacity = await asyncio.to_thread(self._fetch_pending)
        except Exception:
            logger.exception("outreach intake poll: failed to list candidates")
            return
        for row in rows:
            await self._post_card(channel, row, capacity)

    @staticmethod
    def _fetch_pending() -> tuple[list[dict], dict]:
        with db.connection() as conn:
            return outreach_intake.list_undelivered(conn), outreach_intake.read_capacity(conn)

    @_poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()
        if not self._reattached:
            await self._reattach_views()
            self._reattached = True

    async def _reattach_views(self) -> None:
        try:
            with_conn = await asyncio.to_thread(self._fetch_posted)
        except Exception:
            logger.exception("outreach intake: failed to list posted-undecided targets")
            return
        for row in with_conn:
            if msg_id := row["intake_message_id"]:
                self.bot.add_view(OutreachIntakeView(self, row["id"]), message_id=int(msg_id))
        logger.info("outreach intake: re-attached %d persistent view(s)", len(with_conn))

    @staticmethod
    def _fetch_posted() -> list[dict]:
        with db.connection() as conn:
            return outreach_intake.list_posted_undecided(conn)

    async def _post_card(
        self, channel: discord.abc.Messageable, row: dict, capacity: dict
    ) -> None:
        tid = row["id"]
        try:
            evidence = await asyncio.to_thread(self._fetch_evidence, tid)
            message = await channel.send(
                embed=build_card(row, evidence, capacity),
                view=OutreachIntakeView(self, tid),
            )
            await asyncio.to_thread(self._mark_posted, tid, message.id)
            logger.info("outreach intake: posted card for target #%s", tid)
        except Exception:
            logger.exception("outreach intake: failed to post card for #%s", tid)

    @staticmethod
    def _fetch_evidence(target_id: int) -> list[dict]:
        with db.connection() as conn:
            return outreach_intake.target_evidence(conn, target_id)

    @staticmethod
    def _mark_posted(target_id: int, message_id: int) -> None:
        with db.connection() as conn:
            outreach_intake.mark_posted(conn, target_id, message_id)

    # --- decision handling -----------------------------------------------

    async def _authorized(self, interaction: discord.Interaction, target_id: int) -> bool:
        """True iff the clicking user is the configured operator (denies loudly)."""
        if OPERATOR_ID != 0 and interaction.user.id == OPERATOR_ID:
            return True
        logger.warning(
            "outreach_intake_denied user=%s target=%s", interaction.user.id, target_id
        )
        await self._reply(interaction, "⛔ Not authorized to decide outreach intake.")
        return False

    async def finish_decision(
        self, interaction: discord.Interaction, target_id: int, action: str
    ) -> None:
        """Apply the decision; report what happened, or why it was refused."""
        try:
            result = await asyncio.to_thread(outreach_intake.decide, target_id, action)
        except outreach_intake.CapacityFullError as e:
            # Not a failure — the capacity discipline working. The card stays
            # live so the target can be admitted once a slot drains (§8, D1's
            # "re-queue" branch).
            await self._reply(interaction, f"🚧 {e}")
            return
        except outreach_intake.NotReadyToWorkError as e:
            # Card stays live too: this is a "do the diagnostic first", not a no.
            await self._reply(interaction, f"⚠️ Cannot work this yet — {e}")
            return
        except Exception as e:
            logger.exception("outreach intake: decide failed for #%s", target_id)
            await self._reply(interaction, f"⚠️ Intake failed: `{e}` — check #system.")
            return

        if result is None:
            await self._reply(interaction, f"Target #{target_id} was already decided.")
            return

        await self._disable_card(interaction)
        name = result["target"]["company_name"]

        if result["status"] == "in_sequence":
            n = len(result["touches"])
            skipped = sum(1 for t in result["touches"] if t.get("skip_reason"))
            note = f" ({skipped} already past their window)" if skipped else ""
            await self._reply(
                interaction,
                f"✅ Working **{name}** — {n} touches scheduled{note}. "
                f"Packets assemble on the next daily run.",
            )
        elif result["status"] == "watchlist":
            await self._reply(
                interaction,
                f"👁 **{name}** on the watchlist — Trent Crimm watches for a trigger.",
            )
        else:
            await self._reply(
                interaction,
                f"❌ Dropped **{name}**. Evidence history is kept; it stops being polled.",
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
            logger.exception("outreach intake: failed to reply to interaction")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OutreachIntakeCog(bot))
