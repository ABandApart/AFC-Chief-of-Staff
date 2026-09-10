"""#tasks pinned-checklist cog (Track O, `PRD-tasktinder-refinements.md` Increment 3).

One bot-maintained **pinned** message in `#tasks` lists open tasks
(`tasks WHERE status='open'`) with a check-off control each, plus tasks completed
in the last 24h shown struck-through (they archive out after that). Checking an
item completes it; unchecking reopens it (operator decision, 2026-09-10). The
message updates in place and survives a restart.

Discord has no bot-controllable side panel, so the native equivalent of a
persistent checklist is a pinned message the bot edits — here a **Components-v2**
`LayoutView` (one `Section` per task + an accessory `Button`), re-attached on
startup. The list, ordering, and the guarded writes live in
`agents/_lib/tasks_checklist`; this file owns only the Discord surface.

The single maintained message differs from the one-card-per-item cogs: a 60s poll
rebuilds it from `tasks`, so a newly accepted card's task appears within a cycle
without cross-cog wiring, and a check/uncheck edits it immediately. Pinning needs
the bot to hold **Manage Messages** in `#tasks`; without it the message still
works, just unpinned. FAIL-CLOSED: while `TASKS_CHANNEL_ID` is 0 the cog does
nothing.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands, tasks

from agents._lib import tasks_checklist as tc
from agents.discord_bot.config import OPERATOR_DISCORD_ID, TASKS_CHANNEL_ID

logger = logging.getLogger(__name__)

OPERATOR_ID = OPERATOR_DISCORD_ID

# custom_id prefixes — stable across restarts so the persistent view re-binds.
_CHECK = "tasks:check:"
_UNCHECK = "tasks:uncheck:"

_EMPTY = "_Nothing open — accept a card in #task-tinder to add a task._"


def _header(rows: list[dict], open_count: int) -> str:
    """The checklist's title line: open count, plus a note when the cap hides
    some open rows or when completed rows are shown struck-through."""
    shown_open = sum(1 for r in rows if not tc.is_done(r))
    done = sum(1 for r in rows if tc.is_done(r))
    line = f"**Tasks — {open_count} open**"
    if open_count > shown_open:
        # Over the per-message budget; say so in the header (no extra component)
        # rather than dropping rows silently. Checking some off surfaces the rest.
        line += f" · showing {shown_open} of {open_count} — check some off to see the rest"
    if done:
        line += f" · {done} just done"
    return line


class ChecklistView(discord.ui.LayoutView):
    """Persistent (timeout=None) Components-v2 checklist. One `Section` per task
    with a check (open) or undo (completed) accessory button."""

    def __init__(self, cog: TasksChecklistCog, rows: list[dict], open_count: int):
        super().__init__(timeout=None)
        self.cog = cog
        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay(_header(rows, open_count)))
        container.add_item(discord.ui.Separator())
        if not rows:
            container.add_item(discord.ui.TextDisplay(_EMPTY))
        for row in rows:
            container.add_item(self._section(row))
        self.add_item(container)

    def _section(self, row: dict) -> discord.ui.Section:
        done = tc.is_done(row)
        button = discord.ui.Button(
            label="Undo" if done else "Done",
            style=discord.ButtonStyle.secondary if done else discord.ButtonStyle.success,
            custom_id=f"{_UNCHECK if done else _CHECK}{row['id']}",
        )
        button.callback = self._callback(int(row["id"]), reopen=done)
        return discord.ui.Section(discord.ui.TextDisplay(tc.format_line(row)), accessory=button)

    def _callback(self, task_id: int, *, reopen: bool):
        async def _cb(interaction: discord.Interaction) -> None:
            await self.cog.handle_toggle(interaction, task_id, reopen=reopen)
        return _cb


class TasksChecklistCog(commands.Cog):
    """Maintains the single pinned checklist message and its check-off controls."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._reattached = False
        self._message_id: int | None = None

    async def cog_load(self) -> None:
        self._poll.start()

    async def cog_unload(self) -> None:
        self._poll.cancel()

    def _channel(self) -> discord.abc.Messageable | None:
        if not TASKS_CHANNEL_ID:
            return None  # fail-closed: channel unset
        return self.bot.get_channel(TASKS_CHANNEL_ID)

    @staticmethod
    def _fetch() -> tuple[list[dict], int]:
        return tc.list_checklist(), tc.count_open()

    # --- rendering / re-attaching ----------------------------------------

    @tasks.loop(seconds=60)
    async def _poll(self) -> None:
        channel = self._channel()
        if channel is None:
            return  # unset (fail-closed) or not connected yet
        try:
            await self._render(channel)
        except Exception:
            logger.exception("tasks checklist: render failed")

    @_poll.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()
        if not self._reattached:
            await self._reattach()
            self._reattached = True

    async def _reattach(self) -> None:
        channel = self._channel()
        if channel is None:
            return
        try:
            message = await self._find_message(channel)
        except discord.HTTPException:
            logger.exception("tasks checklist: failed to look up the pinned message")
            return
        if message is None:
            return  # none yet; the first render creates and pins it
        self._message_id = message.id
        rows, open_count = await asyncio.to_thread(self._fetch)
        view = ChecklistView(self, rows, open_count)
        if view.is_persistent():  # a view with no task buttons cannot be registered
            self.bot.add_view(view, message_id=message.id)
        logger.info("tasks checklist: re-attached the checklist view")

    async def _find_message(self, channel: discord.abc.Messageable) -> discord.Message | None:
        """The bot's own pinned checklist message, if one exists."""
        for message in await channel.pins():
            if self.bot.user is not None and message.author.id == self.bot.user.id:
                return message
        return None

    async def _render(self, channel: discord.abc.Messageable) -> None:
        rows, open_count = await asyncio.to_thread(self._fetch)
        view = ChecklistView(self, rows, open_count)
        if self._message_id is None:
            message = await channel.send(view=view)
            self._message_id = message.id
            try:
                await message.pin()
            except discord.HTTPException:
                logger.warning(
                    "tasks checklist: could not pin the message (needs Manage Messages)"
                )
            return
        try:
            await channel.get_partial_message(self._message_id).edit(view=view)
        except discord.NotFound:
            self._message_id = None  # deleted out from under us; recreate next tick

    # --- check / uncheck -------------------------------------------------

    async def handle_toggle(
        self, interaction: discord.Interaction, task_id: int, *, reopen: bool
    ) -> None:
        """Complete (or reopen) a task, then update the pinned message in place."""
        if not await self._authorized(interaction, task_id):
            return
        write = tc.reopen_task if reopen else tc.complete_task
        try:
            changed = await asyncio.to_thread(write, task_id)
        except Exception as e:
            logger.exception("tasks checklist: toggle failed for task #%s", task_id)
            await self._reply(interaction, f"⚠️ Update failed: `{e}` — check #system.")
            return
        rows, open_count = await asyncio.to_thread(self._fetch)
        try:
            await interaction.response.edit_message(view=ChecklistView(self, rows, open_count))
        except discord.HTTPException:
            logger.exception("tasks checklist: failed to update the checklist after a toggle")
            return
        if changed is None:  # already in the target state — the list is now correct anyway
            await self._reply(interaction, f"Task #{task_id} was already up to date.")

    async def _authorized(self, interaction: discord.Interaction, task_id: int) -> bool:
        """True iff the clicking user is the configured operator (denies loudly)."""
        if OPERATOR_ID != 0 and interaction.user.id == OPERATOR_ID:
            return True
        logger.warning("tasks_checklist_denied user=%s task=%s", interaction.user.id, task_id)
        await self._reply(interaction, "⛔ Not authorized to check off tasks.")
        return False

    async def _reply(self, interaction: discord.Interaction, content: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)
        except discord.HTTPException:
            logger.exception("tasks checklist: failed to reply to interaction")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TasksChecklistCog(bot))
