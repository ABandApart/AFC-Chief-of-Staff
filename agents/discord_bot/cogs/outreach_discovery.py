"""Gate 0 discovery cog — the daily review sheet (Track O, Part 0 · R0.15).

The surface the operator asked for: **one row per firm, one button per row**.
Built on Components V2, which discord.py 2.7.1 supports in full.

Two Discord facts decided this design, both confirmed against the reference
before any of it was written:

  * **A message holds 40 components.** A row costs three - `Section` +
    `TextDisplay` + `Button` accessory - and the chrome is a `Container` plus a
    header, so **12 rows fit per message** and the daily 25 posts as three.
    `Separator` between rows would cost a fourth each and drop it to eight, so
    rows are separated by styling instead. `assert_component_budget` enforces the
    ceiling, because a silent truncation by the API would look like missing
    candidates rather than a bug.
  * **Buttons cannot appear in a modal** - Discord restricts them to messages.
    So the row's Review button opens a modal built from `TextDisplay`,
    two `RadioGroup`s and a `TextInput`: four of the five children a modal
    allows, capturing the decision and its reason in one submit.

**The reason-required-on-reject rule cannot be expressed in modal UI** - Discord
has no conditional requirement - so it is enforced on submit and, authoritatively,
by the CHECK constraint from 0018. That ordering is deliberate: an invariant is a
database constraint, because nothing mediates these writes once NocoDB can edit
the rows directly.

Decision logic lives in `agents/_lib/outreach_discovery` so it is testable
without a bot; this file owns only the surface.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands, tasks

from agents._lib import db, outreach_discovery
from agents.discord_bot.config import OPERATOR_DISCORD_ID, TASK_TINDER_CHANNEL_ID

logger = logging.getLogger(__name__)

OPERATOR_ID = OPERATOR_DISCORD_ID

# Discord's Components V2 ceiling. Not a style choice — exceeding it is rejected.
MAX_COMPONENTS_PER_MESSAGE = 40

# Container(1) + header TextDisplay(1). There is deliberately NO footer action
# row: the wireframe carried "Accept all shown", and a one-click bulk accept is
# the fastest possible way to fabricate training labels, which is risk D1 and the
# one this design rates High. Every accept costs one deliberate modal. Dropping
# the footer also buys back four components, so 12 rows fit rather than 11.
_CHROME_COMPONENTS = 2
_COMPONENTS_PER_ROW = 3  # Section + TextDisplay + Button accessory

ROWS_PER_MESSAGE = (MAX_COMPONENTS_PER_MESSAGE - _CHROME_COMPONENTS) // _COMPONENTS_PER_ROW

_REVIEW = "gate0:review:"

_DECISIONS = [
    discord.SelectOption(label="Accept — add to the pool", value="accept"),
    discord.SelectOption(label="Reject", value="reject"),
    discord.SelectOption(label="Defer — decide later", value="defer"),
]

_REASONS = [
    discord.SelectOption(label="Wrong segment", value="wrong_segment"),
    discord.SelectOption(label="Too small", value="too_small"),
    discord.SelectOption(label="Too large", value="too_large"),
    discord.SelectOption(label="No pain signal", value="no_pain_signal"),
    discord.SelectOption(label="Poor contact path", value="poor_contact_path"),
    discord.SelectOption(label="Geography", value="geography"),
    discord.SelectOption(label="Competitor or conflict", value="competitor_or_conflict"),
    discord.SelectOption(label="Already known", value="already_known"),
    discord.SelectOption(label="Other (say why)", value="other"),
]


def assert_component_budget(rows: int) -> None:
    """Refuse to build a message Discord would reject or truncate.

    Loud rather than silent: a truncated sheet is indistinguishable from a day
    that found fewer candidates, and that is exactly the kind of quiet wrongness
    this subsystem keeps being bitten by.
    """
    total = _CHROME_COMPONENTS + rows * _COMPONENTS_PER_ROW
    if total > MAX_COMPONENTS_PER_MESSAGE:
        raise ValueError(
            f"{rows} rows needs {total} components, over Discord's "
            f"{MAX_COMPONENTS_PER_MESSAGE}; cap at {ROWS_PER_MESSAGE} rows"
        )


def row_summary(row: dict) -> str:
    """The five fields that fit a row. The other eight live behind Review.

    Columns will not visually align — Discord renders proportional text and its
    only monospace context is a code block, which cannot contain components. This
    is a readable line, not a table, and that trade was made knowingly.
    """
    parts = [f"**{row['company_name']}**"]
    parts.append(row["segment"].replace("_", " "))
    if row.get("headcount_band"):
        parts.append(str(row["headcount_band"]))
    if row.get("hq_location"):
        parts.append(str(row["hq_location"]))
    if row.get("icp_fit_score") is not None:
        parts.append(f"ICP {row['icp_fit_score']}")
    if row.get("email_confidence"):
        parts.append(row["email_confidence"].replace("_", " "))
    return " · ".join(parts)


def detail_block(row: dict) -> str:
    """All 13 card fields for the modal. `unknown` is shown, never guessed."""
    def value(key: str) -> str:
        raw = row.get(key)
        return str(raw) if raw not in (None, "") else "unknown"

    lines = [
        f"**{row['company_name']}** — {row['segment'].replace('_', ' ')}",
        "",
        f"**Description** {value('description')}",
        f"**ICP fit** {value('icp_fit_score')} (model {value('icp_model_version')})",
        f"**Contact** {value('contact_name')} — {value('contact_title')}",
        f"**Email** {value('contact_email')} · {value('email_confidence')}",
        f"**Company LinkedIn** {value('company_linkedin_url')}",
        f"**Contact LinkedIn** {value('contact_linkedin_url')}",
        f"**Verification** {value('verification_note')}",
        # Part 1's news observation fills this. Until it runs there is nothing
        # to show, and `unknown` would imply we looked.
        f"**Observed signal** {row.get('observed_signal') or 'none observed yet'}",
        f"**Touches** {row.get('touches', 0)} — new",
    ]
    if row.get("pain_hook"):
        lines.append(f"**Suggested hook** _(draft — not for sending)_ {row['pain_hook']}")
    if row.get("source_url"):
        lines.append("")
        lines.append(f"Named from: {row['source_url']}")
    return "\n".join(lines)[:3900]


class ReviewModal(discord.ui.Modal):
    """The decision surface. No buttons — Discord does not allow them here."""

    def __init__(self, cog: OutreachDiscoveryCog, row: dict) -> None:
        super().__init__(title=f"Review — {row['company_name']}"[:45])
        self.cog = cog
        self.discovery_id = row["id"]

        self.detail = discord.ui.TextDisplay(detail_block(row))
        self.decision = discord.ui.Label(
            text="Decision",
            component=discord.ui.RadioGroup(options=_DECISIONS),
        )
        self.reason = discord.ui.Label(
            text="If rejecting, why?",
            description="Routes to a different knob in the feedback loop.",
            component=discord.ui.RadioGroup(options=_REASONS, required=False),
        )
        self.note = discord.ui.Label(
            text="Note",
            description="Required when the reason is Other.",
            component=discord.ui.TextInput(
                style=discord.TextStyle.paragraph, required=False, max_length=500),
        )
        for item in (self.detail, self.decision, self.reason, self.note):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        action = _selected(self.decision)
        reason = _selected(self.reason)
        note = getattr(self.note.component, "value", None)

        try:
            outreach_discovery.validate_decision(action, reason, note)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        try:
            result = await asyncio.to_thread(
                outreach_discovery.decide, self.discovery_id, action,
                reason=reason, note=note,
            )
        except Exception:
            logger.exception("gate 0: decision failed for %s", self.discovery_id)
            await interaction.response.send_message(
                "❌ Could not record that — it is logged; nothing changed.",
                ephemeral=True)
            return

        if result is None:
            await interaction.response.send_message(
                "Already decided.", ephemeral=True)
            return
        if action == "defer":
            await interaction.response.send_message(
                "⏸️ Deferred — no label recorded, it returns tomorrow.",
                ephemeral=True)
            return
        await interaction.response.send_message(
            f"{'✅ Accepted' if action == 'accept' else '❌ Rejected'} "
            f"**{result.get('company_name', '')}**"
            f"{f' — {reason}' if reason else ''}",
            ephemeral=True)


def _selected(label: discord.ui.Label) -> str | None:
    """First selected value of a Label-wrapped RadioGroup, or None."""
    values = getattr(label.component, "values", None) or []
    return values[0] if values else None


class SheetView(discord.ui.LayoutView):
    """One message: a container, a header, up to `ROWS_PER_MESSAGE` rows."""

    def __init__(self, cog: OutreachDiscoveryCog, rows: list[dict],
                 *, page: int, pages: int, total: int) -> None:
        super().__init__(timeout=None)
        assert_component_budget(len(rows))
        self.cog = cog

        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay(
            f"### Gate 0 — daily review\n"
            f"{total} verified candidate(s) · page {page} of {pages}"
        ))
        for row in rows:
            container.add_item(discord.ui.Section(
                discord.ui.TextDisplay(row_summary(row)),
                accessory=_ReviewButton(cog, row),
            ))
        self.add_item(container)


class _ReviewButton(discord.ui.Button):
    """The one accessory a `Section` allows.

    **Never name an attribute `row` on a discord.py Item.** `Item.row` is a
    property for action-row layout whose setter runs `5 > value >= 0`, so
    assigning a dict to it raises `TypeError: '>' not supported between instances
    of 'int' and 'dict'` at construction — before the component is attached to a
    Container, so the v2 early-return in that setter has not kicked in yet. This
    crashed every poll and posted nothing until it was fixed; the discovery dict
    is now `self.discovery`.
    """

    def __init__(self, cog: OutreachDiscoveryCog, discovery: dict) -> None:
        super().__init__(label="Review", style=discord.ButtonStyle.secondary,
                         custom_id=f"{_REVIEW}{discovery['id']}")
        self.cog = cog
        self.discovery = discovery

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != OPERATOR_ID:
            await interaction.response.send_message(
                "Not your decision to make.", ephemeral=True)
            return
        fresh = await asyncio.to_thread(self.cog.fetch_one, self.discovery["id"])
        if fresh is None or fresh.get("reviewed_at") is not None:
            await interaction.response.send_message(
                "Already decided.", ephemeral=True)
            return
        await interaction.response.send_modal(ReviewModal(self.cog, fresh))


class OutreachDiscoveryCog(commands.Cog):
    """Posts the daily window and records what the operator decides."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self._poll.start()

    async def cog_unload(self) -> None:
        self._poll.cancel()

    @staticmethod
    def fetch_one(discovery_id: int) -> dict | None:
        with db.connection() as conn:
            return outreach_discovery.get(conn, discovery_id)

    @staticmethod
    def _fetch_window() -> list[dict]:
        with db.connection() as conn:
            return outreach_discovery.list_for_review(conn)

    @staticmethod
    def _mark(rows: list[dict], message_id: str | None) -> None:
        with db.connection() as conn:
            for row in rows:
                outreach_discovery.mark_surfaced(conn, row["id"], message_id)

    @tasks.loop(minutes=30)
    async def _poll(self) -> None:
        """Post any unsurfaced part of today's window.

        Thirty minutes rather than the intake cog's two: this is a triage queue,
        not a decision that ages. Posting is idempotent because `surfaced_at` is
        stamped once and the window query is ordered deterministically.
        """
        channel = self.bot.get_channel(TASK_TINDER_CHANNEL_ID)
        if channel is None:
            return
        try:
            window = await asyncio.to_thread(self._fetch_window)
        except Exception:
            logger.exception("gate 0: could not read the review window")
            return

        pending = [row for row in window if row.get("surfaced_at") is None]
        if not pending:
            return

        pages = [pending[i:i + ROWS_PER_MESSAGE]
                 for i in range(0, len(pending), ROWS_PER_MESSAGE)]
        for index, page in enumerate(pages, 1):
            view = SheetView(self, page, page=index, pages=len(pages),
                             total=len(pending))
            try:
                message = await channel.send(view=view)
            except discord.HTTPException:
                logger.exception("gate 0: failed to post review page %d", index)
                return
            await asyncio.to_thread(self._mark, page, str(message.id))

    @_poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OutreachDiscoveryCog(bot))
