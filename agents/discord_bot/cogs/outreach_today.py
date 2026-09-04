"""Outreach daily surface cog — the #outreach contact worklist (Track O).

See `architecture/PRD-outreach-daily-surface.md`.

Posts one card per **due touch** (R1 — no worklist backfill, no decision cards:
those stay in `#task-tinder`). Each card renders the assembled packet and carries
exactly two actions (R2):

  * **Contact** — one tap. Records `marked_working_at` ("working this today"). The
    06:00 loop drafts the email in Gmail once the packet is ready; this is an
    intent flag, not a send. B2 is untouched: nothing here calls Gmail.
  * **Defer** — opens a modal with a **required** note (R3), then snoozes the
    touch past today (bounded by its window). No Skip button: a touch whose
    window closes unsent is left to the drain (§8).

Same pattern as `cogs/outreach_intake.py`: persistent Views re-bound after a
restart, the SEC-2 operator guard, idempotency owned by the DB row. The rules and
writes live in `agents/_lib/outreach_daily_surface`; this file owns only Discord.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

import discord
from discord.ext import commands, tasks

from agents._lib import db, packet
from agents._lib import outreach_daily_surface as ds
from agents.discord_bot.config import OPERATOR_DISCORD_ID, OUTREACH_CHANNEL_ID

logger = logging.getLogger(__name__)

OPERATOR_ID = OPERATOR_DISCORD_ID

# custom_id prefixes — stable across restarts so persistent Views re-bind.
_CONTACT = "outreach_today:contact:"
_DEFER = "outreach_today:defer:"

_FAILURE_MODE_CAP = 300


def _truncate(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[: cap - 1].rstrip() + "…"


def build_card(data: dict) -> discord.Embed:
    """One due-touch contact card from its packet (`35-` §7 layout: arithmetic
    first, then the driving facts, then the draft, failure mode, BCC)."""
    touch, target, evidence, pkt = (
        data["touch"], data["target"], data["evidence"], data.get("packet"),
    )
    company = target["company_name"]
    slot = touch["slot"]
    ready = bool(pkt and pkt["ready"])

    embed = discord.Embed(
        title=f"Contact — {company} · slot {slot}/5 · due {touch['due_date']}",
        description=(pkt["subject_line"] if pkt else "_packet not assembled yet_"),
        color=discord.Color.green() if ready else discord.Color.orange(),
    )

    if pkt:
        embed.add_field(
            name="Why now",
            value=_truncate(packet.render_arithmetic(pkt["arithmetic"], None), 1000),
            inline=False,
        )
    embed.add_field(name="Driving facts", value=ds.format_driving_facts(evidence), inline=False)

    if pkt:
        unresolved = pkt.get("unresolved_slots") or []
        embed.add_field(
            name="You write" if unresolved else "Ready",
            value=(", ".join(unresolved) if unresolved
                   else "no operator placeholders open — ready to send"),
            inline=False,
        )
        embed.add_field(
            name="Failure mode", value=_truncate(pkt["failure_mode"], _FAILURE_MODE_CAP),
            inline=False,
        )

    # The 06:00 loop writes gmail_thread_id and gmail_draft_id together, so a
    # missing thread link means there is NO draft yet — say so rather than
    # asserting one is in Drafts (a due touch is carded before it is drafted, and
    # while packet assembly is off no draft is ever created).
    if link := ds.gmail_link(touch):
        draft = f"[open the Gmail draft]({link})"
    elif touch.get("gmail_draft_id"):
        draft = "in Gmail Drafts (barry@aiadaptive.co)"
    else:
        draft = "_not drafted yet — the 06:00 loop drafts once the packet is ready_"
    embed.add_field(name="Draft", value=draft, inline=False)
    embed.add_field(name="BCC", value=ds.bcc_address(touch), inline=False)

    footer = f"touch #{touch['id']}"
    if touch.get("marked_working_at"):
        footer += " · ✓ working"
    embed.set_footer(text=footer)
    return embed


class DeferModal(discord.ui.Modal, title="Defer this touch"):
    """The required-note prompt (R3). A snooze without a reason is refused."""

    note = discord.ui.TextInput(
        label="Why defer? (required)",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=400,
        placeholder="e.g. waiting on their board meeting next week",
    )

    def __init__(self, cog: OutreachTodayCog, touch_id: int):
        super().__init__()
        self.cog = cog
        self.touch_id = touch_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.finish_defer(interaction, self.touch_id, str(self.note))


class OutreachTodayView(discord.ui.View):
    """Persistent (timeout=None) Contact/Defer view for one due touch."""

    def __init__(self, cog: OutreachTodayCog, touch_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.touch_id = touch_id

        contact = discord.ui.Button(
            label="Contact", style=discord.ButtonStyle.success,
            custom_id=f"{_CONTACT}{touch_id}",
        )
        defer = discord.ui.Button(
            label="Defer", style=discord.ButtonStyle.secondary,
            custom_id=f"{_DEFER}{touch_id}",
        )
        contact.callback = self._contact
        defer.callback = self._defer
        self.add_item(contact)
        self.add_item(defer)

    async def _contact(self, interaction: discord.Interaction) -> None:
        if await self.cog._authorized(interaction, self.touch_id):
            await self.cog.finish_contact(interaction, self.touch_id)

    async def _defer(self, interaction: discord.Interaction) -> None:
        if await self.cog._authorized(interaction, self.touch_id):
            # The required note is collected in the modal; the write happens on submit.
            await interaction.response.send_modal(DeferModal(self.cog, self.touch_id))


class OutreachTodayCog(commands.Cog):
    """Posts due-touch contact cards and records Contact / Defer."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._reattached = False

    async def cog_load(self) -> None:
        self._poll.start()

    async def cog_unload(self) -> None:
        self._poll.cancel()

    # --- posting / re-attaching ------------------------------------------

    @tasks.loop(seconds=300)
    async def _poll(self) -> None:
        channel = self.bot.get_channel(OUTREACH_CHANNEL_ID)
        if channel is None:
            return  # channel unset (fail-closed) or not connected yet
        try:
            rows = await asyncio.to_thread(self._fetch_due_uncarded)
        except Exception:
            logger.exception("outreach daily: failed to list due touches")
            return
        for row in rows:
            await self._post_card(channel, row["id"])

    @staticmethod
    def _fetch_due_uncarded() -> list[dict]:
        with db.connection() as conn:
            return ds.list_due_uncarded(conn, today=date.today())

    @_poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()
        if not self._reattached:
            await self._reattach_views()
            self._reattached = True

    async def _reattach_views(self) -> None:
        try:
            live = await asyncio.to_thread(self._fetch_carded_live)
        except Exception:
            logger.exception("outreach daily: failed to list carded-live touches")
            return
        for row in live:
            if msg_id := row["daily_message_id"]:
                self.bot.add_view(OutreachTodayView(self, row["id"]), message_id=int(msg_id))
        logger.info("outreach daily: re-attached %d persistent view(s)", len(live))

    @staticmethod
    def _fetch_carded_live() -> list[dict]:
        with db.connection() as conn:
            return ds.list_carded_live(conn)

    async def _post_card(self, channel: discord.abc.Messageable, touch_id: int) -> None:
        try:
            data = await asyncio.to_thread(self._fetch_card, touch_id)
            if data is None:
                return  # touch vanished between listing and rendering
            message = await channel.send(
                embed=build_card(data), view=OutreachTodayView(self, touch_id),
            )
            await asyncio.to_thread(self._mark_carded, touch_id, message.id)
            logger.info("outreach daily: posted card for touch #%s", touch_id)
        except Exception:
            logger.exception("outreach daily: failed to post card for touch #%s", touch_id)

    @staticmethod
    def _fetch_card(touch_id: int) -> dict | None:
        with db.connection() as conn:
            return ds.card_inputs(conn, touch_id)

    @staticmethod
    def _mark_carded(touch_id: int, message_id: int) -> None:
        with db.connection() as conn:
            ds.mark_carded(conn, touch_id, message_id)

    # --- actions ---------------------------------------------------------

    async def _authorized(self, interaction: discord.Interaction, touch_id: int) -> bool:
        """True iff the clicking user is the configured operator (denies loudly)."""
        if OPERATOR_ID != 0 and interaction.user.id == OPERATOR_ID:
            return True
        logger.warning(
            "outreach_daily_denied user=%s touch=%s", interaction.user.id, touch_id
        )
        await self._reply(interaction, "⛔ Not authorized to action outreach.")
        return False

    async def finish_contact(self, interaction: discord.Interaction, touch_id: int) -> None:
        """Record the Contact intent flag and mark the card working."""
        try:
            stamp = await asyncio.to_thread(self._do_contact, touch_id)
        except Exception as e:
            logger.exception("outreach daily: contact failed for touch #%s", touch_id)
            await self._reply(interaction, f"⚠️ Contact failed: `{e}` — check #system.")
            return
        if stamp is None:
            await self._reply(interaction, f"Touch #{touch_id} is already sent or skipped.")
            return
        await self._mark_working_footer(interaction)
        await self._reply(
            interaction,
            "✍️ Marked as working today. When the draft is ready in Gmail, "
            "write the observation and send.",
        )

    async def finish_defer(
        self, interaction: discord.Interaction, touch_id: int, note: str
    ) -> None:
        """Snooze the touch with the required note; close the card."""
        try:
            until = await asyncio.to_thread(self._do_defer, touch_id, note)
        except ds.DeferWindowClosedError as e:
            await self._reply(interaction, f"🚧 {e}")
            return
        except ValueError as e:
            await self._reply(interaction, f"⚠️ {e}")
            return
        except Exception as e:
            logger.exception("outreach daily: defer failed for touch #%s", touch_id)
            await self._reply(interaction, f"⚠️ Defer failed: `{e}` — check #system.")
            return
        if until is None:
            await self._reply(interaction, f"Touch #{touch_id} is already sent or skipped.")
            return
        await self._disable_card(interaction, note=f"⏰ Deferred to {until} — {note}")
        await self._reply(interaction, f"⏰ Deferred to **{until}**.")

    @staticmethod
    def _do_contact(touch_id: int):
        with db.connection() as conn:
            return ds.mark_working(conn, touch_id)

    @staticmethod
    def _do_defer(touch_id: int, note: str):
        with db.connection() as conn:
            return ds.defer(conn, touch_id, note, today=date.today())

    # --- card edits ------------------------------------------------------

    async def _mark_working_footer(self, interaction: discord.Interaction) -> None:
        message = interaction.message
        if message is None or not message.embeds:
            return
        embed = message.embeds[0]
        base = (embed.footer.text or "").split(" · ✓")[0]
        embed.set_footer(text=f"{base} · ✓ working")
        try:
            await message.edit(embed=embed)
        except discord.HTTPException:
            pass

    async def _disable_card(self, interaction: discord.Interaction, *, note: str) -> None:
        message = interaction.message
        if message is None:
            return
        view = discord.ui.View.from_message(message)
        for child in view.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        embed = message.embeds[0] if message.embeds else discord.Embed()
        embed.color = discord.Color.dark_grey()
        embed.set_footer(text=note)
        try:
            await message.edit(embed=embed, view=view)
        except discord.HTTPException:
            pass

    async def _reply(self, interaction: discord.Interaction, content: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)
        except discord.HTTPException:
            logger.exception("outreach daily: failed to reply to interaction")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OutreachTodayCog(bot))
