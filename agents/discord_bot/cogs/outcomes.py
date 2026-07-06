"""Outcomes cog — the `/outcome` slash command.

Records business outcomes into the `outcomes` table — the KR1 measurement
substrate (`80-telemetry-layer.md`). Entered by hand, by design: no
automation backfills this table; Higgins (Phase 11) reports against it.

No LLM call — a pure structured write, so this does NOT go through the cost
helper.

Command shape (2026-07 refactor):
  - **type** is a slash-command choice (a real dropdown in the command UI).
  - **fact** is an optional slash-command parameter with *autocomplete*
    against recent/matching facts — no more typing raw fact ids looked up
    via SQL. (Discord modals can't hold dropdowns or autocomplete, which is
    why both live on the command, not in the modal.)
  - Selecting the command opens a **modal** for the free-form fields:
    Description (required, multi-line) and Value $ (optional).

Fact-link validation is the FK constraint itself: a stale id raises
ForeignKeyViolation on insert (no read-check race).
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands
from psycopg import errors as pg_errors

from agents.discord_bot import brain

logger = logging.getLogger(__name__)

# Outcome types from architecture/80-telemetry-layer.md.
OUTCOME_TYPES = [
    "discovery_call_booked",
    "proposal_sent",
    "engagement_signed",
    "engagement_renewed",
    "maintenance_converted",
    "newsletter_published",
    "roundtable_topic_used",
    "partnership_explored",
]


def parse_value(raw: str) -> float | None:
    """Parse the optional dollar value. Blank → None. Bad → ValueError.

    Tolerates a leading '$' and thousands commas.
    """
    raw = raw.strip()
    if not raw:
        return None
    cleaned = raw.lstrip("$").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError as e:
        raise ValueError(
            f"'{raw}' isn't a number — leave Value blank or enter e.g. 5000"
        ) from e


class OutcomeModal(discord.ui.Modal, title="Record an outcome"):
    desc = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
        placeholder="What happened? (e.g. 'Signed 6-month retainer with Acme Co.')",
    )
    amount = discord.ui.TextInput(
        label="Value $ (optional)",
        style=discord.TextStyle.short,
        required=False,
        max_length=20,
        placeholder="e.g. 12000",
    )

    def __init__(self, outcome_type: str, fact_id: int | None):
        super().__init__()
        self.outcome_type = outcome_type
        self.fact_id = fact_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Parse optional value; bad input → friendly ephemeral error, no write.
        try:
            value = parse_value(str(self.amount))
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return

        try:
            oid = await asyncio.to_thread(
                brain.insert_outcome,
                outcome_type=self.outcome_type,
                description=str(self.desc),
                value=value,
                attributed_fact_id=self.fact_id,
            )
        except pg_errors.ForeignKeyViolation:
            logger.warning(
                "outcome rejected — no fact with id %s (type=%s, value=%s)",
                self.fact_id, self.outcome_type, value,
            )
            await interaction.response.send_message(
                f"⚠️ No fact with id {self.fact_id}. Outcome not saved — "
                f"re-run and pick a fact from the suggestions (or leave it blank).",
                ephemeral=True,
            )
            return
        except Exception:
            logger.exception("failed to write outcome (%s)", self.outcome_type)
            await interaction.response.send_message(
                "⚠️ Something went wrong saving that — check the logs.",
                ephemeral=True,
            )
            return

        val_str = f" (${value:,.0f})" if value is not None else ""
        link_str = f", linked to fact #{self.fact_id}" if self.fact_id is not None else ""
        await interaction.response.send_message(
            f"✅ Recorded outcome #{oid}: **{self.outcome_type}**{val_str}{link_str}",
            ephemeral=True,
        )
        logger.info(
            "outcome #%s recorded: %s value=%s fact=%s",
            oid, self.outcome_type, value, self.fact_id,
        )


class OutcomesCog(commands.Cog):
    """Provides /outcome."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="outcome", description="Record a business outcome")
    @app_commands.describe(
        type="The kind of outcome",
        fact="Fact this outcome traces back to (optional — type to search)",
    )
    @app_commands.choices(
        type=[
            app_commands.Choice(name=t.replace("_", " "), value=t)
            for t in OUTCOME_TYPES
        ]
    )
    async def outcome(
        self,
        interaction: discord.Interaction,
        type: app_commands.Choice[str],
        fact: int | None = None,
    ) -> None:
        await interaction.response.send_modal(
            OutcomeModal(outcome_type=type.value, fact_id=fact)
        )

    @outcome.autocomplete("fact")
    async def fact_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        try:
            rows = await asyncio.to_thread(brain.search_facts, current, 20)
        except Exception:
            logger.exception("fact autocomplete failed")
            return []
        # Choice names are capped at 100 chars by Discord.
        return [
            app_commands.Choice(name=f"#{fid} · {content}"[:100], value=fid)
            for fid, content in rows
        ]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OutcomesCog(bot))
