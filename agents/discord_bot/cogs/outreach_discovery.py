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
from discord import app_commands
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
_EDIT = "gate0:edit:"

_CONFIDENCE = [
    discord.SelectOption(label="Verified by me", value="operator_verified"),
    discord.SelectOption(label="Published by the company", value="public"),
    discord.SelectOption(label="Inferred from a pattern", value="inferred_pattern"),
    discord.SelectOption(label="General inbox (info@/hello@)", value="general_inbox"),
]

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


# The visual state of a row MIRRORS the data model rather than decorating it.
# `defer` deliberately records nothing (OQ-H), so a deferred row is indistinguish-
# able from an untouched one — which is correct: it IS still pending, and marking
# it would mean inventing state the feedback loop has agreed not to keep.
_DECIDED_MARK = {"accept": "✅", "reject": "❌"}
_DECIDED_LABEL = {"accept": "Accepted", "reject": "Rejected"}


def row_summary(row: dict) -> str:
    """The five fields that fit a row. The other eight live behind Review.

    Columns will not visually align — Discord renders proportional text and its
    only monospace context is a code block, which cannot contain components. This
    is a readable line, not a table, and that trade was made knowingly.

    A decided row is prefixed with its outcome so the sheet is scannable at a
    glance. The greyed-out button alone is too subtle to read down a 12-row list,
    which is the whole point of a sheet.
    """
    decision = row.get("review_decision")
    parts = []
    if mark := _DECIDED_MARK.get(decision or ""):
        parts.append(mark)
    parts.append(f"**{row['company_name']}**")
    parts.append(row["segment"].replace("_", " "))
    if row.get("headcount_band"):
        parts.append(str(row["headcount_band"]))
    if row.get("hq_location"):
        parts.append(str(row["hq_location"]))
    if row.get("icp_fit_score") is not None:
        parts.append(f"ICP {row['icp_fit_score']}")
    if row.get("email_confidence"):
        parts.append(row["email_confidence"].replace("_", " "))
    if decision == "reject" and row.get("reject_reason"):
        parts.append(f"_{row['reject_reason'].replace('_', ' ')}_")
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
        # Stored rather than read from `interaction.message`: a modal-submit
        # interaction does not reliably carry the message it came from, and we
        # already persist the id the card was posted under.
        self.message_id = row.get("review_message_id")
        self.company_domain = row.get("company_domain")

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
            ephemeral=True,
            # Catches "I noticed the contact was stale while deciding" without a
            # context switch. The sheet pays nothing for it.
            view=EditPromptView(self.cog, self.company_domain),
        )

        # The ephemeral reply confirms to the operator; the sheet is what he
        # reads next time. Refresh it so the decision is visible without
        # remembering which rows were done.
        await self.cog.refresh_sheet(self.message_id)


def _selected(label: discord.ui.Label) -> str | None:
    """The chosen value of a Label-wrapped input, or None if nothing was picked.

    **`RadioGroup` exposes `value` (singular), not `values`.** Reading only
    `.values` returned None for every submit, so every decision failed validation
    with "unknown Gate 0 action: None" and **nothing was ever written** — the
    review sheet rendered perfectly and recorded nothing. The plural fallback is
    kept because select-style components do expose `.values`, and a future field
    may use one.
    """
    component = label.component
    value = getattr(component, "value", None)
    if value:
        return str(value)
    values = getattr(component, "values", None) or []
    return str(values[0]) if values else None


class EditContactModal(discord.ui.Modal):
    """Correct a firm's contact details (interim; `35-` §9 gives this to NocoDB).

    **Exactly five children — Discord's modal limit, with nothing spare.** Four
    text fields plus the confidence selector. Any sixth field means dropping one
    or splitting the modal, so this is a deliberate ceiling rather than an
    accident waiting to be discovered at runtime.

    Blank leaves a field unchanged. Nothing here can clear a value, so a
    half-filled form cannot wipe details it never carried.
    """

    def __init__(self, cog: OutreachDiscoveryCog, record: dict) -> None:
        super().__init__(title=f"Edit — {record['company_name']}"[:45])
        self.cog = cog
        self.domain = record["company_domain"]
        # Kept so submit can send only what actually MOVED. `TextInput.value`
        # falls back to its prefilled default, so an untouched field submits its
        # current value — without this diff every edit would rewrite all five
        # fields and the audit log, which is the history, would record four
        # changes that never happened.
        self.before = record

        def field(label: str, key: str, hint: str) -> discord.ui.Label:
            return discord.ui.Label(
                text=label,
                component=discord.ui.TextInput(
                    default=record.get(key) or None, required=False,
                    placeholder=hint, max_length=300),
            )

        self.contact_name = field("Contact name", "contact_name", "Full name")
        self.contact_title = field("Title", "contact_title", "Founder & CEO")
        self.contact_email = field("Email", "contact_email", "name@company.com")
        self.contact_linkedin = field("Contact LinkedIn", "contact_linkedin_url",
                                      "https://www.linkedin.com/in/…")
        self.confidence = discord.ui.Label(
            text="How well is the email known?",
            description="Verifying it by hand is stronger than a pattern guess.",
            component=discord.ui.RadioGroup(options=_CONFIDENCE, required=False),
        )
        for item in (self.contact_name, self.contact_title, self.contact_email,
                     self.contact_linkedin, self.confidence):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        submitted = {
            "contact_name": _selected(self.contact_name),
            "contact_title": _selected(self.contact_title),
            "contact_email": _selected(self.contact_email),
            "contact_linkedin_url": _selected(self.contact_linkedin),
            "email_confidence": _selected(self.confidence),
        }
        fields = {
            key: value for key, value in submitted.items()
            if value and value != (self.before.get(key) or None)
        }
        if not fields:
            # No database round-trip for a form nobody edited.
            await interaction.response.send_message(
                "Nothing changed — no field was edited.", ephemeral=True)
            return

        try:
            result = await asyncio.to_thread(
                outreach_discovery.update_contact, self.domain, fields)
        except Exception:
            logger.exception("gate 0: contact edit failed for %s", self.domain)
            await interaction.response.send_message(
                "❌ Could not save that — it is logged; nothing changed.",
                ephemeral=True)
            return

        if not result["changed"]:
            await interaction.response.send_message(
                "Nothing changed — no field was edited.", ephemeral=True)
            return

        where = " and ".join(
            place for place, hit in
            (("the pool", result["discovery"]), ("the target", result["target"]))
            if hit
        )
        await interaction.response.send_message(
            f"✏️ Updated {', '.join(result['changed'])} on "
            f"**{result['company_name']}** in {where}.",
            ephemeral=True)
        await self.cog.refresh_for_domain(self.domain)


class EditPromptView(discord.ui.View):
    """An Edit button on the ephemeral confirmation after a decision.

    A button cannot live in a modal and a modal cannot open a modal, so the
    reply message is the only place in the review flow that can carry one. It
    catches the common case — noticing a stale contact WHILE reviewing — without
    a context switch, and costs the sheet nothing.
    """

    def __init__(self, cog: OutreachDiscoveryCog, domain: str) -> None:
        super().__init__(timeout=900)  # the interaction token's own lifetime
        self.cog = cog
        self.domain = domain

    @discord.ui.button(label="Edit contact", style=discord.ButtonStyle.secondary)
    async def edit(self, interaction: discord.Interaction,
                   button: discord.ui.Button) -> None:
        record = await asyncio.to_thread(self.cog.fetch_contact, self.domain)
        if record is None:
            await interaction.response.send_message(
                "That firm is no longer in the pool or the targets.",
                ephemeral=True)
            return
        await interaction.response.send_modal(EditContactModal(self.cog, record))


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
        decision = discovery.get("review_decision")
        super().__init__(
            label=_DECIDED_LABEL.get(decision or "", "Review"),
            style=(discord.ButtonStyle.success if decision == "accept"
                   else discord.ButtonStyle.secondary),
            # A decided row keeps its button, disabled, rather than losing it.
            # Removing it would change the component count between the posted
            # message and any later rebuild, and the row is the record of what
            # was decided.
            disabled=decision in _DECIDED_LABEL,
            custom_id=f"{_REVIEW}{discovery['id']}",
        )
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
        self._reattached = False

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

    @staticmethod
    def fetch_contact(domain: str) -> dict | None:
        with db.connection() as conn:
            return outreach_discovery.contact_record(conn, domain)

    @staticmethod
    def _search_contacts(term: str) -> list[dict]:
        with db.connection() as conn:
            return outreach_discovery.search_contacts(conn, term)

    @staticmethod
    def _message_ids_for(domain: str) -> list[str]:
        with db.connection() as conn:
            record = outreach_discovery.contact_record(conn, domain)
            if record is None or not record["discovery_id"]:
                return []
            row = outreach_discovery.get(conn, record["discovery_id"])
            return [row["review_message_id"]] if row and row["review_message_id"] else []

    async def refresh_for_domain(self, domain: str) -> None:
        """Redraw whichever sheet carries this firm, if any.

        A target-only firm (the CSV imports) has no card to redraw, and that is
        not a failure — it simply has no sheet.
        """
        for message_id in await asyncio.to_thread(self._message_ids_for, domain):
            await self.refresh_sheet(message_id)

    @app_commands.command(
        name="gate0-edit",
        description="Correct a firm's contact details (pool or target).",
    )
    @app_commands.describe(company="Start typing a company name or domain")
    async def gate0_edit(self, interaction: discord.Interaction,
                         company: str) -> None:
        if interaction.user.id != OPERATOR_ID:
            await interaction.response.send_message(
                "Not your record to edit.", ephemeral=True)
            return
        record = await asyncio.to_thread(self.fetch_contact, company)
        if record is None:
            await interaction.response.send_message(
                f"No pool row or target for `{company}`. Pick one from the "
                f"suggestions so the domain matches exactly.", ephemeral=True)
            return
        await interaction.response.send_modal(EditContactModal(self, record))

    @gate0_edit.autocomplete("company")
    async def _company_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Names to read, domains as the value — the domain is the identity both
        tables key on (R0.10), and two firms can share a name."""
        try:
            matches = await asyncio.to_thread(self._search_contacts, current)
        except Exception:
            logger.exception("gate 0: contact autocomplete failed")
            return []
        return [
            app_commands.Choice(name=m["company_name"][:100],
                                value=m["company_domain"][:100])
            for m in matches
        ]

    @staticmethod
    def _fetch_page(message_id: str) -> list[dict]:
        with db.connection() as conn:
            return outreach_discovery.page_rows(conn, message_id)

    async def refresh_sheet(self, message_id: str | None) -> None:
        """Re-render one posted sheet so decided rows show as decided.

        Discord has no partial component update — the whole view is re-sent — so
        this rebuilds the page from current database state and edits the message.
        Rebuilding from the database rather than mutating the in-memory view means
        the sheet always reflects what was actually stored, including a decision
        made from another session or corrected directly in the table.

        Failure here is cosmetic and must never surface as a failed decision: the
        write has already committed by the time this runs.
        """
        if not message_id:
            return
        try:
            rows = await asyncio.to_thread(self._fetch_page, message_id)
            if not rows:
                return  # unknown message id; nothing to redraw
            channel = self.bot.get_channel(TASK_TINDER_CHANNEL_ID)
            if channel is None:
                return
            view = SheetView(self, rows, page=1, pages=1, total=len(rows))
            await channel.get_partial_message(int(message_id)).edit(view=view)
        except Exception:
            logger.exception(
                "gate 0: could not refresh sheet %s — the decision IS recorded; "
                "only the card is stale", message_id,
            )

    @staticmethod
    def _fetch_surfaced_pages() -> dict[str, list[dict]]:
        with db.connection() as conn:
            return outreach_discovery.surfaced_pages(conn)

    async def _reattach_views(self) -> None:
        """Re-register the posted sheets so their buttons survive a restart.

        A persistent view is only live in the process that registered it. Without
        this, every card posted before a restart has dead buttons: the click
        routes to no handler and Discord shows "didn't respond in time" after
        three seconds, with nothing in the log because no callback ever ran.

        The rebuilt view exists only to register handlers by `custom_id` —
        Discord keeps the original message content, so the header arguments below
        are never rendered and their values do not matter.
        """
        try:
            pages = await asyncio.to_thread(self._fetch_surfaced_pages)
        except Exception:
            logger.exception("gate 0: failed to list surfaced pages for re-attach")
            return

        attached = 0
        for message_id, rows in pages.items():
            try:
                view = SheetView(self, rows, page=1, pages=1, total=len(rows))
                self.bot.add_view(view, message_id=int(message_id))
                attached += 1
            except Exception:
                logger.exception("gate 0: could not re-attach message %s", message_id)
        logger.info("gate 0: re-attached %d persistent view(s) covering %d row(s)",
                    attached, sum(len(r) for r in pages.values()))

    @_poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()
        if not self._reattached:
            await self._reattach_views()
            self._reattached = True


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OutreachDiscoveryCog(bot))
