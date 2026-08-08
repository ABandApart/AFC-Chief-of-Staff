"""Briefing skeleton — the 6am good-morning post to #briefing.

Phase 3.5 scope (per `architecture/70-build-order.md` Phase 3 task 5):
a *static* system-status message, no synthesis, no LLM call — so this does
NOT go through the cost helper. Phase 4 upgrades it to a real briefing
(Claude Sonnet via the cost helper, top reading recommendations, etc.).

One-shot process, scheduled by launchd (`launchd/com.aiadaptive.cos.briefing.plist`,
6:00 local). Posts via the Discord REST API directly — a one-shot poster has
no reason to open a gateway connection, so discord.py stays out of it.

Run as barry-agent (keychain holds `discord-bot-token` and `db-url`):

    cd ~/agents
    uv run python -m agents.briefing.run

Failure behavior: any exception (DB down, Discord 4xx/5xx) exits non-zero
and lands in the launchd log — there is deliberately no retry or alerting
here yet; Ted's health checks (Phase 11) own that.
"""

from __future__ import annotations

import json
import logging
import sys
import urllib.request
from datetime import datetime
from typing import Any

from agents._lib import db, heartbeat
from agents._lib.creds import keychain_get
from agents.discord_bot.config import BRIEFING_CHANNEL_ID

# Dead-man's-switch check slug (PERF-4 / 80-telemetry). Absence of this ping is
# the alert that the morning briefing didn't run — the one failure the box can't
# report on itself.
HEARTBEAT_SLUG = "cos-briefing"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

DISCORD_API = "https://discord.com/api/v10"


def gather_status() -> dict[str, Any]:
    """Pull the status numbers from the brain (three cheap queries)."""
    with db.connection() as conn:
        with conn.cursor() as cur:
            # Post-pivot the knowledge lives in the cognee graph, not a `facts`
            # table. `capture_messages` (one row per captured note) is the cheap,
            # same-DB proxy for "notes captured" — no cross-DB / cognee call in
            # the briefing.
            cur.execute(
                """
                SELECT count(*),
                       count(*) FILTER (WHERE captured_at > now() - interval '24 hours')
                FROM capture_messages
                """
            )
            notes_total, notes_24h = cur.fetchone()
            cur.execute(
                """
                SELECT coalesce(sum(usd_cost), 0),
                       count(*),
                       count(*) FILTER (WHERE status <> 'success')
                FROM agent_runs
                WHERE started_at > now() - interval '24 hours'
                """
            )
            spend_24h, calls_24h, failures_24h = cur.fetchone()
            cur.execute("SELECT count(*) FROM outcomes")
            outcomes_total = cur.fetchone()[0]
    return {
        "notes_total": notes_total,
        "notes_24h": notes_24h,
        "spend_24h": float(spend_24h),
        "calls_24h": calls_24h,
        "failures_24h": failures_24h,
        "outcomes_total": outcomes_total,
    }


def format_briefing(now: datetime, status: dict[str, Any]) -> str:
    """Render the briefing text (pure — unit-tested)."""
    failures = status["failures_24h"]
    failure_str = "no failures" if failures == 0 else f"⚠️ {failures} failure(s)"
    return (
        f"☀️ Good morning — {now.strftime('%A %d %B %Y')}\n\n"
        f"**System status**\n"
        f"• Notes captured: {status['notes_total']} total, {status['notes_24h']} in the last 24h\n"
        f"• LLM calls (24h): {status['calls_24h']} for ${status['spend_24h']:.6f} — {failure_str}\n"
        f"• Outcomes recorded: {status['outcomes_total']}\n"
        f"• Brain: Postgres reachable ✓\n\n"
        f"_Briefing skeleton — real synthesis arrives with Tartt in Phase 4._"
    )


def post_to_discord(text: str) -> None:
    """POST one message to #briefing via the REST API (Bot token auth)."""
    req = urllib.request.Request(
        f"{DISCORD_API}/channels/{BRIEFING_CHANNEL_ID}/messages",
        data=json.dumps({"content": text}).encode("utf-8"),
        headers={
            "Authorization": f"Bot {keychain_get('discord-bot-token')}",
            "Content-Type": "application/json",
            "User-Agent": "aiadaptive-cos-briefing (Phase 3.5)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        logger.info("Posted briefing to #briefing (HTTP %d)", resp.status)


def main() -> int:
    status = gather_status()
    text = format_briefing(datetime.now(), status)
    post_to_discord(text)
    # Success path only — the ping means "the briefing actually posted". Never
    # move this into a finally: a ping on a crashed run manufactures confidence.
    heartbeat.ping(HEARTBEAT_SLUG)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # A broken run signals /fail so it alerts now, not after the grace window.
        logger.exception("briefing failed")
        heartbeat.ping_fail(HEARTBEAT_SLUG)
        sys.exit(1)
    finally:
        db.close_pool()
