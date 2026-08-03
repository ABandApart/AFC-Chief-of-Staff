"""Approvals cog — the `#approvals` human "yes" gate (B2).

Posts each `pending` `approval_queue` row to `#approvals` as a card with
Approve / Reject / Edit buttons, and executes the registered handler for the
item's `item_type` **only** after a human clicks Approve (or Edit → amend →
approve-with-changes). This is the exit trust boundary: no outbound side-effect
runs without a human decision here.

The action logic, state machine, and DB writes live in `agents/_lib/approvals`
(Discord-free, unit-tested). This cog is the Discord surface:

  - A background poller (`_poll`, every 10s) posts any `pending` row that has
    no Discord message yet. Polling — rather than an in-process call — is what
    lets a *separate* agent process enqueue an approval and have it appear.
  - Buttons use **persistent Views** (`timeout=None`, stable `custom_id`s keyed
    by row id) re-attached on startup, so a card posted before a restart still
    works afterward.
  - Idempotency is the DB's: `approvals.decide(...)` guards on
    `status='pending'`, so a double-click executes the action exactly once.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands, tasks

from agents._lib import approvals
from agents.discord_bot.config import APPROVALS_CHANNEL_ID

logger = logging.getLogger(__name__)

# custom_id prefixes — stable across restarts so persistent Views re-bind.
_APPROVE = "approval:approve:"
_REJECT = "approval:reject:"
_EDIT = "approval:edit:"


def build_card(row_id: int, item_type: str, envelope: dict) -> discord.Embed:
    """The #approvals card for one pending request."""
    summary = approvals.envelope_summary(envelope)
    embed = discord.Embed(
        title=f"Approval needed · {item_type}",
        description=summary or "_(no summary provided)_",
        color=discord.Color.orange(),
    )
    embed.set_footer(text=f"approval #{row_id}")
    return embed


class EditModal(discord.ui.Modal, title="Edit before approving"):
    """Amends the single editable draft field, then approves-with-changes."""

    def __init__(self, cog: ApprovalsCog, row_id: int, edit_field: str, current: str):
        super().__init__()
        self.cog = cog
        self.row_id = row_id
        self.edit_field = edit_field
        self.draft = discord.ui.TextInput(
            label=edit_field[:45],
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=4000,
            default=current,
        )
        self.add_item(self.draft)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        new_value = str(self.draft)
        await self.cog.finish_decision(
            interaction,
            self.row_id,
            "edit",
            payload={self.edit_field: new_value},
            edit_notes=f"edited {self.edit_field} via #approvals",
        )


class ApprovalView(discord.ui.View):
    """Persistent (timeout=None) 3-button view for one approval row."""

    def __init__(self, cog: ApprovalsCog, row_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.row_id = row_id

        approve = discord.ui.Button(
            label="Approve",
            style=discord.ButtonStyle.success,
            custom_id=f"{_APPROVE}{row_id}",
        )
        reject = discord.ui.Button(
            label="Reject",
            style=discord.ButtonStyle.danger,
            custom_id=f"{_REJECT}{row_id}",
        )
        edit = discord.ui.Button(
            label="Edit",
            style=discord.ButtonStyle.secondary,
            custom_id=f"{_EDIT}{row_id}",
        )
        approve.callback = self._approve
        reject.callback = self._reject
        edit.callback = self._edit
        self.add_item(approve)
        self.add_item(reject)
        self.add_item(edit)

    async def _approve(self, interaction: discord.Interaction) -> None:
        await self.cog.finish_decision(interaction, self.row_id, "approve")

    async def _reject(self, interaction: discord.Interaction) -> None:
        await self.cog.finish_decision(interaction, self.row_id, "reject")

    async def _edit(self, interaction: discord.Interaction) -> None:
        # Seed the modal from the current envelope; if the row is already
        # decided or gone, say so instead of opening an editor.
        row = await asyncio.to_thread(approvals.get_row, self.row_id)
        if row is None or row["status"] != "pending":
            await interaction.response.send_message(
                f"Approval #{self.row_id} is already decided.", ephemeral=True
            )
            return
        envelope = row["payload"]
        edit_field = approvals.envelope_edit_field(envelope)
        current = str(approvals.envelope_payload(envelope).get(edit_field, ""))
        await interaction.response.send_modal(
            EditModal(self.cog, self.row_id, edit_field, current)
        )


class ApprovalsCog(commands.Cog):
    """Posts approval cards and dispatches handlers on human approval."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._reattached = False

    async def cog_load(self) -> None:
        self._poll.start()

    async def cog_unload(self) -> None:
        self._poll.cancel()

    # --- posting / re-attaching ------------------------------------------

    @tasks.loop(seconds=10)
    async def _poll(self) -> None:
        """Post any pending rows without a Discord message yet."""
        channel = self.bot.get_channel(APPROVALS_CHANNEL_ID)
        if channel is None:
            return  # not connected yet; try again next tick
        try:
            rows = await asyncio.to_thread(approvals.list_undelivered)
        except Exception:
            logger.exception("approvals poll: failed to list undelivered rows")
            return
        for row in rows:
            await self._post_card(channel, row)

    @_poll.before_loop
    async def _before_poll(self) -> None:
        # Wait for the gateway/cache, then re-attach views for already-posted
        # pending rows once, so their buttons survive a restart.
        await self.bot.wait_until_ready()
        if not self._reattached:
            await self._reattach_views()
            self._reattached = True

    async def _reattach_views(self) -> None:
        try:
            rows = await asyncio.to_thread(approvals.list_pending_posted)
        except Exception:
            logger.exception("approvals: failed to list pending-posted rows")
            return
        for row in rows:
            msg_id = row["discord_message_id"]
            if not msg_id:
                continue
            self.bot.add_view(
                ApprovalView(self, row["id"]), message_id=int(msg_id)
            )
        logger.info("approvals: re-attached %d persistent view(s)", len(rows))

    async def _post_card(self, channel: discord.abc.Messageable, row: dict) -> None:
        row_id = row["id"]
        try:
            embed = build_card(row_id, row["item_type"], row["payload"])
            message = await channel.send(embed=embed, view=ApprovalView(self, row_id))
            await asyncio.to_thread(approvals.mark_posted, row_id, message.id)
            logger.info("approvals: posted card for #%s (%s)", row_id, row["item_type"])
        except Exception:
            logger.exception("approvals: failed to post card for #%s", row_id)

    # --- decision handling -----------------------------------------------

    async def finish_decision(
        self,
        interaction: discord.Interaction,
        row_id: int,
        action: str,
        *,
        payload: dict | None = None,
        edit_notes: str | None = None,
    ) -> None:
        """Transition the row (guarded), then dispatch its handler if it shipped."""
        result = await asyncio.to_thread(
            approvals.decide, row_id, action, payload=payload, edit_notes=edit_notes
        )
        if result is None:
            # Already decided by an earlier click — the idempotent no-op.
            await interaction.response.send_message(
                f"Approval #{row_id} was already decided.", ephemeral=True
            )
            return

        await self._disable_card(interaction, result["status"])

        if result["status"] not in approvals.DISPATCH_STATUSES:
            await self._reply(
                interaction, f"🚫 Rejected approval #{row_id} — nothing ran."
            )
            return

        # The action ships: run its handler (a slow one runs off the loop).
        try:
            handler = approvals.get_handler(result["item_type"])
            outcome = await asyncio.to_thread(handler, result["payload"])
        except Exception as e:
            logger.exception("approvals: handler failed for #%s", row_id)
            await self._reply(
                interaction,
                f"⚠️ Approved #{row_id} but the handler failed: `{e}` — check #system.",
            )
            return

        verb = "Approved" if action == "approve" else "Approved with edits"
        await self._reply(interaction, f"✅ {verb} #{row_id}. Result: {outcome}")

    async def _disable_card(
        self, interaction: discord.Interaction, status: str
    ) -> None:
        """Grey out the buttons on the decided card so it can't be re-clicked."""
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
        """Respond once, whether or not the interaction was already answered."""
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)
        except discord.HTTPException:
            logger.exception("approvals: failed to reply to interaction")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ApprovalsCog(bot))
