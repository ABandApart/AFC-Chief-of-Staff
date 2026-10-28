"""Initiatives cog — `/initiative` and `/todo` (`PRD-notion-integration.md`, N3).

- **`/initiative item initiative`** sets or clears the initiative on a pending
  Task Tinder card or an open task. Both options are type-aheads: `item` lists
  pending cards and open tasks, newest first; `initiative` lists the Active
  initiatives from the Notion master document as "Initiative · KR", plus
  "Unassigned". The card or the #tasks checklist redraws right away.
- **`/todo text [initiative] [due]`** adds the operator's own to-do as a pending
  card in #task-tinder. It does not skip acceptance: a to-do written today may not
  be a priority yet (N-D10).

Initiatives are defined only in Notion; Discord only assigns them (N-S13). Both
commands are operator-only. No LLM call. The pure parts and the guarded writes
live in `agents/_lib/initiatives`.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from agents._lib import initiatives
from agents.discord_bot.config import OPERATOR_DISCORD_ID, TASKS_CHANNEL_ID

logger = logging.getLogger(__name__)

MAX_TODO_CHARS = 300


def _choices(pairs: list[tuple[str, str]]) -> list[app_commands.Choice[str]]:
    return [app_commands.Choice(name=n, value=v) for n, v in pairs]


class InitiativesCog(commands.Cog):
    """Provides /initiative and /todo."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _operator(self, interaction: discord.Interaction) -> bool:
        if OPERATOR_DISCORD_ID != 0 and interaction.user.id == OPERATOR_DISCORD_ID:
            return True
        logger.warning("initiatives_denied user=%s", interaction.user.id)
        await interaction.response.send_message("⛔ Operator only.", ephemeral=True)
        return False

    # --- /initiative ---------------------------------------------------------

    @app_commands.command(name="initiative",
                          description="Set the initiative on a Task Tinder card or a task")
    @app_commands.describe(item="A pending card or an open task",
                           initiative="An active initiative, or Unassigned")
    async def initiative(self, interaction: discord.Interaction, item: str,
                         initiative: str) -> None:
        if not await self._operator(interaction):
            return
        try:
            kind, item_id = initiatives.parse_item(item)
            initiative_id = initiatives.parse_initiative(initiative)
            changed = await asyncio.to_thread(initiatives.assign, kind, item_id, initiative_id)
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        except Exception as e:
            logger.exception("/initiative: write failed")
            await interaction.response.send_message(f"⚠️ Update failed: `{e}`", ephemeral=True)
            return
        noun = "Card" if kind == "card" else "Task"
        if not changed:
            state = "decided" if kind == "card" else "done"
            await interaction.response.send_message(
                f"{noun} #{item_id} is already {state}, so it wasn't changed.", ephemeral=True)
            return
        await interaction.response.send_message(
            f"✅ {noun} #{item_id} → "
            f"{'Unassigned' if initiative_id is None else 'initiative set'}.",
            ephemeral=True)
        await self._redraw(kind, item_id)

    async def _redraw(self, kind: str, item_id: int) -> None:
        """Best effort: the card redraws now; the checklist also redraws on its next poll."""
        try:
            if kind == "card":
                cog = self.bot.get_cog("TaskTinderCog")
                if cog is not None:
                    await cog.refresh_card(item_id)
            else:
                cog = self.bot.get_cog("TasksChecklistCog")
                channel = self.bot.get_channel(TASKS_CHANNEL_ID) if TASKS_CHANNEL_ID else None
                if cog is not None and channel is not None:
                    await cog._render(channel)
        except Exception:
            logger.exception("/initiative: redraw failed for %s #%s", kind, item_id)

    @initiative.autocomplete("item")
    async def _item_autocomplete(self, interaction: discord.Interaction,
                                 current: str) -> list[app_commands.Choice[str]]:
        try:
            items = await asyncio.to_thread(initiatives.open_items)
        except Exception:
            logger.exception("/initiative: item autocomplete failed")
            return []
        return _choices(initiatives.item_choices(current, items))

    @initiative.autocomplete("initiative")
    async def _initiative_autocomplete(self, interaction: discord.Interaction,
                                       current: str) -> list[app_commands.Choice[str]]:
        return await self._initiative_choices(current)

    async def _initiative_choices(self, current: str) -> list[app_commands.Choice[str]]:
        try:
            rows = await asyncio.to_thread(initiatives.assignable)
        except Exception:
            logger.exception("initiative autocomplete failed")
            return []
        return _choices(initiatives.initiative_choices(current, rows))

    # --- /todo ---------------------------------------------------------------

    @app_commands.command(name="todo", description="Add your own to-do as a Task Tinder card")
    @app_commands.describe(text="What to do",
                           initiative="Optional: the initiative it serves",
                           due="Optional due date, YYYY-MM-DD")
    async def todo(self, interaction: discord.Interaction,
                   text: app_commands.Range[str, 1, MAX_TODO_CHARS],
                   initiative: str | None = None, due: str | None = None) -> None:
        if not await self._operator(interaction):
            return
        try:
            initiative_id = initiatives.parse_initiative(initiative)
            due_date = initiatives.parse_due(due)
            card_id = await asyncio.to_thread(
                initiatives.add_todo, text.strip(), initiative_id, due_date)
        except ValueError as e:
            await interaction.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        except Exception as e:
            logger.exception("/todo: write failed")
            await interaction.response.send_message(f"⚠️ Couldn't add it: `{e}`", ephemeral=True)
            return
        await interaction.response.send_message(
            f"✅ Added card #{card_id}. It appears in #task-tinder within a minute; "
            "accept it there to make it a task.", ephemeral=True)

    @todo.autocomplete("initiative")
    async def _todo_initiative_autocomplete(self, interaction: discord.Interaction,
                                            current: str) -> list[app_commands.Choice[str]]:
        return await self._initiative_choices(current)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InitiativesCog(bot))
