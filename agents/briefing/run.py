"""Morning briefing — the 6am good-morning post to #briefing.

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

from psycopg.rows import dict_row

from agents._lib import db, heartbeat, task_tinder
from agents._lib.creds import keychain_get
from agents.discord_bot.config import BRIEFING_CHANNEL_ID

# Top reading recommendations to surface. Capped small on purpose: the briefing
# has a Discord message budget (eval UX-1), so it shows the few best, not a list.
READING_RECS_LIMIT = 3

# New inbound prospects (Phase 6, Roy Kent) to surface — same Discord budget logic.
NEW_PROSPECTS_LIMIT = 5

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
        f"• Brain: Postgres reachable ✓"
    )


def fetch_reading_recs(conn: object, limit: int = READING_RECS_LIMIT) -> list[dict]:
    """The top recently-discovered content by interest score (Tartt, Phase 4)."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            SELECT url, title, interest_score
            FROM content_items
            WHERE interest_score IS NOT NULL
              AND collected_at > now() - interval '48 hours'
            ORDER BY interest_score DESC, collected_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def format_reading_recs(recs: list[dict]) -> str:
    """Render the reading-recs section (pure). Empty string when there's nothing —
    so the briefing simply omits the section rather than showing a blank header."""
    if not recs:
        return ""
    lines = ["**📚 Reading — new since yesterday**"]
    for r in recs[:READING_RECS_LIMIT]:
        score = r.get("interest_score") or 0.0
        lines.append(f"• [{r['title']}]({r['url']}) — interest {score:.2f}")
    return "\n".join(lines)


def fetch_new_prospects(conn: object, limit: int = NEW_PROSPECTS_LIMIT) -> list[dict]:
    """Inbound prospects (Phase 6, Roy Kent) received in the last 24h."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            SELECT name, company, icp_fit_score, status
            FROM prospects
            WHERE received_at > now() - interval '24 hours'
            ORDER BY icp_fit_score DESC NULLS LAST, received_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        return cur.fetchall()


def format_new_prospects(prospects: list[dict]) -> str:
    """Render the new-prospects section (pure). Empty string when there's
    nothing — the briefing omits the section rather than showing a blank header."""
    if not prospects:
        return ""
    lines = ["**🤝 New prospects — last 24h**"]
    for p in prospects[:NEW_PROSPECTS_LIMIT]:
        who = f"{p['name']} ({p['company']})" if p.get("company") else p["name"]
        if p.get("icp_fit_score") is not None:
            lines.append(f"• {who} — fit {p['icp_fit_score']:.2f}")
        else:
            lines.append(f"• {who} — not yet qualified")
    return "\n".join(lines)


def fetch_outreach_line(conn: object) -> str:
    """Track O's one briefing line (`35-` §9), or "" when nothing is live.

    Computed here rather than handed over by the 05:45 loop: the numbers are
    cheap and reading them live means the briefing can never quote a stale
    snapshot if that loop failed or was disabled. The loop still has to run
    first — it is what makes the packets the "not ready" count refers to exist.

    Imported lazily so a briefing on a box without the outreach tables (or with
    the loop never activated) degrades to omitting the line rather than failing.
    """
    try:
        from agents.outreach import daily

        counts = daily.briefing_counts(conn)
        with conn.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(
                "SELECT count(*) FROM outreach_packets p WHERE NOT p.ready "
                "AND p.assembled_at > CURRENT_DATE"
            )
            not_ready = cur.fetchone()[0]
        return daily.format_briefing_line(counts, not_ready)
    except Exception:
        logger.exception("briefing: outreach line unavailable — omitting it")
        return ""


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
    with db.connection() as conn:
        recs = fetch_reading_recs(conn)
        prospects = fetch_new_prospects(conn)
        outreach_line = fetch_outreach_line(conn)
    pending = task_tinder.count_pending()
    text = format_briefing(datetime.now(), status)
    if pending:
        text = f"{text}\n\n🗂️ **Pending in Task Tinder:** {pending} candidate(s)"
    recs_text = format_reading_recs(recs)
    if recs_text:
        text = f"{text}\n\n{recs_text}"
    prospects_text = format_new_prospects(prospects)
    if prospects_text:
        text = f"{text}\n\n{prospects_text}"
    if outreach_line:
        text = f"{text}\n\n{outreach_line}"
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
