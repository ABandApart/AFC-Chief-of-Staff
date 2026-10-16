"""Outcomes cog — the `/outcome` slash command.

Records business outcomes into the `outcomes` table — the KR1 measurement
substrate (`80-telemetry-layer.md`). Entered by hand, by design: no
automation backfills this table; Higgins (Phase 11) reports against it.

No LLM call — a pure structured write, so this does NOT go through the cost
helper.

Command shape (`PRD-outcome-attribution.md`, increment 2):
  - **type** is a slash-command choice (a real dropdown in the command UI).
  - **company** is an optional autocomplete. One pick sets both `client_id` and,
    when an outreach target shares the domain, `attributed_target_id` (D2).
    Required for the contract types (D3) — KR3 pairs them by client.
  - Selecting the command opens a **modal** for the free-form fields:
    Description (required), Total contract value $ (optional, D4), and Client
    name (only when the pick creates a new client).

The old optional fact-link (autocomplete against the `facts` table) was dropped
in the cognee pivot (W5): knowledge now lives in the graph as auto-extracted
nodes, not numbered rows, so there is no stable id to link an outcome to.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from agents._lib import outreach
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

# Contract outcomes must name a company (D3). Mirrored by the database CHECK
# `outcomes_engagement_has_client_ck` (migration 0031) — change both together.
CONTRACT_TYPES = frozenset(
    {"engagement_signed", "engagement_renewed", "maintenance_converted"}
)

MAX_CHOICES = 25  # Discord's autocomplete limit
MAX_CHOICE_CHARS = 100  # Discord's limit on a choice's name and value


def requires_company(outcome_type: str) -> bool:
    return outcome_type in CONTRACT_TYPES


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
            f"'{raw}' isn't a number — leave the value blank or enter e.g. 18000"
        ) from e


@dataclass(frozen=True)
class CompanyRef:
    """A parsed `company` option value.

    kind: 'client' (key = client id), 'target' (key = normalized domain), or
    'new' (key = the typed text; `domain` set when the text looks like a domain).
    """

    kind: str
    key: str
    domain: str | None = None


def _looks_like_domain(text: str) -> bool:
    return "." in text and not any(ch.isspace() for ch in text)


def parse_company(value: str) -> CompanyRef:
    """Parse an autocomplete value. Free text that was not picked → ValueError.

    Only picked choices carry a prefix, so an unprefixed value means the
    operator typed past the suggestions — refused rather than guessed, the same
    rule `/gate0-edit` applies.
    """
    kind, sep, key = (value or "").partition(":")
    key = key.strip()
    if not sep or not key:
        raise ValueError("Pick a company from the suggestions.")
    if kind == "client":
        if not key.isdigit():
            raise ValueError("Pick a company from the suggestions.")
        return CompanyRef("client", key)
    if kind == "target":
        return CompanyRef("target", outreach.normalize_domain(key))
    if kind == "new":
        domain = outreach.normalize_domain(key) if _looks_like_domain(key) else None
        return CompanyRef("new", key, domain=domain or None)
    raise ValueError("Pick a company from the suggestions.")


def _clip(text: str) -> str:
    return text[:MAX_CHOICE_CHARS]


def company_choices(
    term: str,
    clients: list[dict],
    targets: list[dict],
) -> list[tuple[str, str]]:
    """(name, value) pairs for the `company` autocomplete, in display order.

    Clients first, then outreach targets that are not yet clients, then — when
    nothing matches the typed text exactly — one "New client" choice. The new
    choice is kept even when the list is full, so a new company is never
    unreachable.
    """
    typed = (term or "").strip()
    typed_l = typed.lower()
    client_domains = {c["domain"] for c in clients if c.get("domain")}

    choices: list[tuple[str, str]] = []
    exact = False
    for c in clients:
        label = f"{c['name']} ({c['domain']})" if c.get("domain") else c["name"]
        choices.append((_clip(label), f"client:{c['id']}"))
        exact = exact or typed_l in {c["name"].lower(), (c.get("domain") or "")}
    for t in targets:
        if t["company_domain"] in client_domains:
            continue
        label = f"{t['company_name']} ({t['company_domain']}) · outreach target"
        choices.append((_clip(label), _clip(f"target:{t['company_domain']}")))
        exact = exact or typed_l in {t["company_name"].lower(), t["company_domain"]}

    if typed and not exact:
        new = (_clip(f"➕ New client: {typed}"), _clip(f"new:{typed}"))
        return choices[: MAX_CHOICES - 1] + [new]
    return choices[:MAX_CHOICES]


class OutcomeModal(discord.ui.Modal, title="Record an outcome"):
    desc = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
        placeholder="What happened? (e.g. 'Signed a 3-month AI pilot with Acme Co.')",
    )
    amount = discord.ui.TextInput(
        label="Total contract value $ (optional)",
        style=discord.TextStyle.short,
        required=False,
        max_length=20,
        placeholder="Full contract; first term for maintenance. e.g. 18000",
    )

    def __init__(self, outcome_type: str, company: CompanyRef | None = None):
        super().__init__()
        self.outcome_type = outcome_type
        self.company = company
        self.client_name: discord.ui.TextInput | None = None
        if company is not None and company.kind == "new":
            # A typed domain carries no name; a typed name is offered for editing.
            self.client_name = discord.ui.TextInput(
                label="Client name",
                style=discord.TextStyle.short,
                required=True,
                max_length=200,
                default=None if company.domain else company.key[:200],
                placeholder="Company name as it should appear in reports",
            )
            self.add_item(self.client_name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Parse optional value; bad input → friendly ephemeral error, no write.
        try:
            value = parse_value(str(self.amount))
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return

        try:
            result = await asyncio.to_thread(
                brain.insert_outcome,
                outcome_type=self.outcome_type,
                description=str(self.desc),
                value=value,
                company=self.company,
                client_name=str(self.client_name) if self.client_name else None,
            )
        except KeyError as e:
            await interaction.response.send_message(
                f"⚠️ {e.args[0]} — pick the company again from the suggestions.",
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

        label = self.outcome_type.replace("_", " ")
        client_str = f" — {result['client_name']}" if result["client_name"] else ""
        val_str = f" (${value:,.0f})" if value is not None else ""
        target_str = " · linked to outreach target" if result["target_id"] else ""
        await interaction.response.send_message(
            f"✅ Recorded outcome #{result['id']}: **{label}**"
            f"{client_str}{val_str}{target_str}",
            ephemeral=True,
        )
        logger.info(
            "outcome #%s recorded: %s value=%s client=%s target=%s",
            result["id"], self.outcome_type, value,
            result["client_id"], result["target_id"],
        )


class OutcomesCog(commands.Cog):
    """Provides /outcome."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="outcome", description="Record a business outcome")
    @app_commands.describe(
        type="The kind of outcome",
        company="Client or outreach company — required for contract outcomes",
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
        company: str | None = None,
    ) -> None:
        # Refuse before the modal opens, so nothing typed into it is lost.
        if requires_company(type.value) and not company:
            await interaction.response.send_message(
                "⚠️ Pick a company — contract outcomes need one for KR3.",
                ephemeral=True,
            )
            return
        ref = None
        if company:
            try:
                ref = parse_company(company)
            except ValueError as e:
                await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
                return
        await interaction.response.send_modal(
            OutcomeModal(outcome_type=type.value, company=ref)
        )

    @outcome.autocomplete("company")
    async def _company_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        try:
            clients, targets = await asyncio.to_thread(brain.search_companies, current)
        except Exception:
            logger.exception("/outcome: company autocomplete failed")
            return []
        return [
            app_commands.Choice(name=name, value=value)
            for name, value in company_choices(current, clients, targets)
        ]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OutcomesCog(bot))
