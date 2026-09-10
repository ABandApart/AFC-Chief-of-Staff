"""Routing invariant: outreach decision cards go to #outreach, not #task-tinder.

`PRD-tasktinder-refinements.md` Increment 1 separated the workflows: `#task-tinder`
is non-outreach (content suggestions + inbound leads), and every outreach card cog
posts to `OUTREACH_CHANNEL_ID`. This grep-guard keeps a future outreach cog from
silently defaulting back to `TASK_TINDER_CHANNEL_ID` and recreating the confusion
this increment removed — the invariant is self-enforcing, not two file swaps.
"""
from __future__ import annotations

from pathlib import Path

_COGS = Path(__file__).resolve().parents[1] / "agents" / "discord_bot" / "cogs"

# Cogs that post outreach cards — all must route to #outreach.
_OUTREACH_COGS = (
    "outreach_intake",     # Gate-1 intake (Work this / Watchlist / Drop)
    "outreach_discovery",  # Gate-0 review sheets
    "outreach_rescore",    # O2 stale-signal S4/S5 re-check
    "outreach_today",      # daily Contact/Defer surface (was already here)
)


def test_outreach_cogs_post_to_outreach_channel():
    for name in _OUTREACH_COGS:
        src = (_COGS / f"{name}.py").read_text(encoding="utf-8")
        assert "OUTREACH_CHANNEL_ID" in src, f"{name} does not reference OUTREACH_CHANNEL_ID"
        assert "TASK_TINDER_CHANNEL_ID" not in src, (
            f"{name} still routes to #task-tinder — outreach cards belong in #outreach"
        )


def test_task_tinder_cog_stays_on_task_tinder():
    # The content control surface is the one cog that keeps #task-tinder.
    src = (_COGS / "task_tinder.py").read_text(encoding="utf-8")
    assert "TASK_TINDER_CHANNEL_ID" in src
    assert "OUTREACH_CHANNEL_ID" not in src
