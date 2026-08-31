"""Outreach re-score cog — the O2 stale-signal modal in `#task-tinder` (`35-` §4).

The weekly sweep (`agents/outreach/rescore.py`) raises an `outreach_stale_signal`
`task_candidates` row per target whose S4/S5 leadership judgement is >30 days old.
This cog is that row's **surface**: one card per stale target with a **Re-score**
button that opens a two-field modal (S4 leadership gap, S5 team build below, each
1/3/5). Submitting writes the new judgement, stamps `signals_observed_at` (the
30-day clock reset, so the sweep stops re-raising it), and resolves the candidate.

Same pattern as `outreach_intake` (persistent Views, poll loop, startup re-attach,
operator guard); logic lives in `agents/_lib/outreach_rescore` so it is testable
without a bot. The `RadioGroup.value` (singular) gotcha and the mandatory
`_reattach_views` are both carried over from the Gate 0 cog's hard-won fixes.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands, tasks

from agents._lib import db, outreach_rescore
from agents.discord_bot.config import OPERATOR_DISCORD_ID, TASK_TINDER_CHANNEL_ID

logger = logging.getLogger(__name__)

OPERATOR_ID = OPERATOR_DISCORD_ID

# Stable custom_id prefix so persistent Views re-bind after a restart.
_RESCORE = "outreach:rescore:"

# The S4/S5 rubric (35- §4 / the scoring playbook), 1/3/5 with what each means.
_S4_OPTIONS = [
    discord.SelectOption(label="5 — seat visibly empty (req open 45+ days)", value="5"),
    discord.SelectOption(label="3 — under-led (someone owns it, below the level)", value="3"),
    discord.SelectOption(label="1 — led (the seat is filled at the right level)", value="1"),
]
_S5_OPTIONS = [
    discord.SelectOption(label="5 — hiring below a leader who does not exist", value="5"),
    discord.SelectOption(label="3 — some building below the gap", value="3"),
    discord.SelectOption(label="1 — no team-build below", value="1"),
]


def build_card(row: dict) -> discord.Embed:
    """The stale re-check card — enough to re-judge without a context switch."""
    embed = discord.Embed(
        title=f"Re-check leadership signals — {row['company_name']}",
        description=row.get("evidence_text") or "S4/S5 judgement is over 30 days old.",
        color=discord.Color.gold(),
    )
    current = (f"S4 leadership gap: **{row['s4_leadership_gap'] or '—'}**   ·   "
               f"S5 team build: **{row['s5_team_build_below'] or '—'}**")
    embed.add_field(name="Current", value=current, inline=False)
    embed.add_field(
        name="Score", value=f"{row.get('score') or '—'}/25 · {row.get('treatment') or 'unscored'}",
        inline=False,
    )
    observed = row.get("signals_observed_at")
    embed.set_footer(text=f"target #{row['target_id']} · last set {observed or 'never'}")
    return embed


class RescoreModal(discord.ui.Modal):
    """S4/S5 re-entry. Modals cannot hold buttons; the two RadioGroups are it."""

    def __init__(self, cog: OutreachRescoreCog, row: dict) -> None:
        super().__init__(title=f"Re-score — {row['company_name']}"[:45])
        self.cog = cog
        self.candidate_id = row["candidate_id"]
        self.target_id = row["target_id"]
        # A modal-submit interaction does not carry its originating message, so the
        # card id is stored to disable the card by id after the write (Gate 0 lesson).
        self.card_message_id = row.get("discord_message_id")

        self.s4 = discord.ui.Label(
            text="S4 — Leadership gap",
            component=discord.ui.RadioGroup(options=_S4_OPTIONS),
        )
        self.s5 = discord.ui.Label(
            text="S5 — Team build below",
            component=discord.ui.RadioGroup(options=_S5_OPTIONS),
        )
        self.add_item(self.s4)
        self.add_item(self.s5)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        s4, s5 = _selected(self.s4), _selected(self.s5)
        if s4 is None or s5 is None:
            await interaction.response.send_message(
                "❌ Pick a value for both S4 and S5.", ephemeral=True)
            return
        try:
            result = await asyncio.to_thread(
                self.cog._apply, self.candidate_id, self.target_id, int(s4), int(s5))
        except Exception:
            logger.exception("rescore: apply failed for candidate %s", self.candidate_id)
            await interaction.response.send_message(
                "❌ Could not record that — it is logged; nothing changed.",
                ephemeral=True)
            return

        if result is None:
            await interaction.response.send_message(
                "Already re-scored — nothing to do.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"✅ Re-scored **{result['company_name']}** → "
                f"{result.get('score') or '—'}/25 · {result.get('treatment') or 'unscored'}. "
                f"The 30-day clock is reset.",
                ephemeral=True)
        await self.cog._disable_card(self.card_message_id)


def _selected(label: discord.ui.Label) -> str | None:
    """Chosen value of a Label-wrapped RadioGroup, or None. `RadioGroup` exposes
    `value` (singular) — reading only `.values` returned None for every submit in
    the Gate 0 cog and silently recorded nothing (`outreach_discovery._selected`)."""
    component = label.component
    value = getattr(component, "value", None)
    if value:
        return str(value)
    values = getattr(component, "values", None) or []
    return str(values[0]) if values else None


class RescoreView(discord.ui.View):
    """Persistent (timeout=None) one-button view; the button opens the modal."""

    def __init__(self, cog: OutreachRescoreCog, candidate_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.candidate_id = candidate_id
        button = discord.ui.Button(
            label="Re-score", style=discord.ButtonStyle.primary,
            custom_id=f"{_RESCORE}{candidate_id}",
        )
        button.callback = self._open
        self.add_item(button)

    async def _open(self, interaction: discord.Interaction) -> None:
        if not await self.cog._authorized(interaction):
            return
        row = await asyncio.to_thread(self.cog._get, self.candidate_id)
        if row is None:
            await self.cog._reply(interaction, "Already handled — nothing pending.")
            return
        await interaction.response.send_modal(RescoreModal(self.cog, row))


class OutreachRescoreCog(commands.Cog):
    """Posts stale-signal re-check cards and records the re-scored S4/S5."""

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
            return
        try:
            rows = await asyncio.to_thread(self._fetch_undelivered)
        except Exception:
            logger.exception("rescore poll: failed to list stale re-checks")
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
            rows = await asyncio.to_thread(self._fetch_posted)
        except Exception:
            logger.exception("rescore: failed to list posted re-checks")
            return
        for row in rows:
            if msg_id := row.get("discord_message_id"):
                self.bot.add_view(RescoreView(self, row["candidate_id"]),
                                  message_id=int(msg_id))
        logger.info("rescore: re-attached %d persistent view(s)", len(rows))

    async def _post_card(self, channel: discord.abc.Messageable, row: dict) -> None:
        cid = row["candidate_id"]
        try:
            message = await channel.send(
                embed=build_card(row), view=RescoreView(self, cid))
            await asyncio.to_thread(self._mark_posted, cid, message.id)
            logger.info("rescore: posted re-check card for target #%s", row["target_id"])
        except Exception:
            logger.exception("rescore: failed to post card for candidate %s", cid)

    # --- DB thunks (run off the event loop) ------------------------------

    @staticmethod
    def _fetch_undelivered() -> list[dict]:
        with db.connection() as conn:
            return outreach_rescore.list_undelivered(conn)

    @staticmethod
    def _fetch_posted() -> list[dict]:
        with db.connection() as conn:
            return outreach_rescore.list_posted_undecided(conn)

    @staticmethod
    def _get(candidate_id: int) -> dict | None:
        with db.connection() as conn:
            return outreach_rescore.get_recheck(conn, candidate_id)

    @staticmethod
    def _mark_posted(candidate_id: int, message_id: int) -> None:
        with db.connection() as conn:
            outreach_rescore.mark_posted(conn, candidate_id, message_id)

    @staticmethod
    def _apply(candidate_id: int, target_id: int, s4: int, s5: int) -> dict | None:
        with db.connection() as conn:
            return outreach_rescore.apply_rescore(conn, candidate_id, target_id, s4, s5)

    # --- interaction plumbing --------------------------------------------

    async def _authorized(self, interaction: discord.Interaction) -> bool:
        if OPERATOR_ID != 0 and interaction.user.id == OPERATOR_ID:
            return True
        logger.warning("rescore_denied user=%s", interaction.user.id)
        await self._reply(interaction, "⛔ Not authorized to re-score outreach targets.")
        return False

    async def _disable_card(self, card_message_id: str | int | None) -> None:
        """Grey out the card's button by message id (a modal submit carries no
        message). A failure here is cosmetic — the write already committed."""
        if not card_message_id:
            return
        channel = self.bot.get_channel(TASK_TINDER_CHANNEL_ID)
        if channel is None:
            return
        view = discord.ui.View(timeout=None)
        button = discord.ui.Button(label="Re-scored", style=discord.ButtonStyle.secondary,
                                   disabled=True, custom_id=f"{_RESCORE}done")
        view.add_item(button)
        try:
            await channel.get_partial_message(int(card_message_id)).edit(view=view)
        except discord.HTTPException:
            logger.info("rescore: could not disable card %s (write already committed)",
                        card_message_id)

    async def _reply(self, interaction: discord.Interaction, content: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)
        except discord.HTTPException:
            logger.exception("rescore: failed to reply to interaction")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OutreachRescoreCog(bot))
