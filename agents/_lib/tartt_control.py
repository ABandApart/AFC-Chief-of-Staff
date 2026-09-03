"""Conversational Tartt control — parse an operator message into a feed/interest
change and apply it (Phase 4, operator self-service).

The operator manages Tartt from #briefing in plain language ("Tartt, add
TechCrunch https://techcrunch.com/feed/", "drop arxiv", "I care about
energy-transition stories"). This module is the testable core: `extract_intent`
turns the message into a validated action via one cheap Haiku call, and the
apply functions mutate the two stores:

- **Feeds → the `sources` SQL table** (add / pause / list). Pausing sets
  `active=false` — reversible, and it keeps the row + its content history.
- **Interests → the cognee graph** (add / remove / list), where scoring already
  reads them. Removal is text-matched, not id-matched: some interest nodes were
  seeded with random ids rather than the deterministic one, so only the topic
  text reliably identifies them.

**Safety boundary (enforced by the cog, relied on here):** this acts only on the
operator's own Discord messages. Tartt ingests untrusted newsletters and posts
their summaries into #briefing; if the cog obeyed its own channel, a hostile feed
could inject "remove all feeds". The cog gates on author == operator, so every
call here originates from the operator — never from ingested content.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from agents._lib import db
from agents._lib.runs import agent_run
from agents.tartt import content_graph

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5"
DEFAULT_CADENCE_HOURS = 12

# The operator addresses Tartt by name; the cog also accepts an @-mention and
# passes the stripped remainder. Kept here so the prefix rule is unit-testable.
_PREFIX_RE = re.compile(r"^\s*tartt\b[\s,:—-]*(.*)", re.IGNORECASE | re.DOTALL)

_TOOL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["add_feed", "remove_feed", "list_feeds",
                     "add_interest", "remove_interest", "list_interests", "unknown"],
            "description": "Which management action the operator is asking for.",
        },
        "name": {"type": "string", "description": "Feed display name (add_feed)."},
        "url": {"type": "string",
                "description": "Feed URL (add_feed). Never invent one; leave empty if not given."},
        "cadence_hours": {"type": "integer",
                          "description": "Poll interval in hours (add_feed); omit to default."},
        "identifier": {"type": "string",
                       "description": "Which feed/interest to remove: a feed name/URL fragment "
                                      "or the interest topic text."},
        "topic": {"type": "string", "description": "The interest topic text (add_interest)."},
    },
    "required": ["action"],
}

_SYSTEM = (
    "You turn a single operator message into one Tartt management action. Tartt is "
    "a news-discovery agent with RSS/Atom feeds and a set of interest topics used to "
    "rank stories. Map the message to exactly one action and extract its fields. "
    "Use 'unknown' if it isn't a feed or interest request. Never fabricate a feed URL "
    "— if the operator names a feed to add but gives no URL, still use add_feed and "
    "leave url empty so the system can ask for it."
)


def strip_prefix(content: str) -> str | None:
    """Return the command text if the message addresses Tartt by the name prefix,
    else None. (The cog handles the @-mention form separately.)"""
    m = _PREFIX_RE.match(content or "")
    if not m:
        return None
    rest = m.group(1).strip()
    return rest or None


def extract_intent(text: str) -> dict[str, Any]:
    """One Haiku call → a schema-validated {action, ...} dict."""
    with agent_run("tartt-control", "feed_admin", trigger_kind="event") as run:
        return run.call_anthropic_structured(
            messages=[{"role": "user", "content": text}],
            model=MODEL,
            max_output_tokens=300,
            tool_name="manage_tartt",
            tool_description="Record the operator's Tartt feed/interest management request.",
            input_schema=_TOOL_SCHEMA,
            system=_SYSTEM,
        )


# --- feeds (sources table) ---------------------------------------------------

def add_feed(name: str, url: str, cadence_hours: int = DEFAULT_CADENCE_HOURS,
             source_kind: str = "rss") -> dict[str, Any]:
    """Insert a feed, or reactivate it if the URL was already present."""
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT id, name, active FROM sources WHERE url = %s", (url,))
        row = cur.fetchone()
        if row:
            if row[2]:
                return {"status": "exists", "name": row[1], "url": url}
            cur.execute("UPDATE sources SET active = true WHERE id = %s", (row[0],))
            return {"status": "reactivated", "name": row[1], "url": url}
        cur.execute(
            "INSERT INTO sources (name, url, source_kind, trust_score, "
            "poll_interval_hours, active) VALUES (%s, %s, %s, 0.5, %s, true)",
            (name, url, source_kind, cadence_hours),
        )
        return {"status": "added", "name": name, "url": url, "cadence_hours": cadence_hours}


def remove_feed(identifier: str) -> dict[str, Any]:
    """Pause (active=false) the one active feed matching name/url; report matches."""
    like = f"%{identifier.strip()}%"
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, url FROM sources "
            "WHERE active AND (name ILIKE %s OR url ILIKE %s)",
            (like, like),
        )
        rows = cur.fetchall()
        if not rows:
            return {"status": "not_found"}
        if len(rows) > 1:
            return {"status": "ambiguous", "matches": [r[1] for r in rows]}
        cur.execute("UPDATE sources SET active = false WHERE id = %s", (rows[0][0],))
        return {"status": "paused", "name": rows[0][1], "url": rows[0][2]}


def list_feeds() -> list[dict[str, Any]]:
    with db.connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT name, url, poll_interval_hours FROM sources "
            "WHERE active ORDER BY name"
        )
        return [{"name": n, "url": u, "cadence_hours": h} for n, u, h in cur.fetchall()]


# --- interests (cognee graph) ------------------------------------------------

def add_interest(topic: str) -> dict[str, Any]:
    existing = {t.lower() for t in content_graph.list_interest_signals()}
    if topic.strip().lower() in existing:
        return {"status": "exists", "topic": topic.strip()}
    asyncio.run(content_graph.add_interest_signals([(topic.strip(), 1.0)]))
    return {"status": "added", "topic": topic.strip()}


def remove_interest(identifier: str) -> dict[str, Any]:
    removed = content_graph.remove_interest_signals([identifier])
    return {"status": "removed" if removed else "not_found", "topic": identifier.strip()}


def list_interests() -> list[str]:
    return content_graph.list_interest_signals()


# --- orchestration -----------------------------------------------------------

def handle(text: str) -> str:
    """Parse `text` into an action, apply it, and return the operator-facing reply.

    Sync on purpose (the cog runs it in a worker thread) so the blocking Haiku
    call and the cognee coroutine don't touch the bot's event loop.
    """
    intent = extract_intent(text)
    action = intent.get("action", "unknown")

    if action == "add_feed":
        url = (intent.get("url") or "").strip()
        name = (intent.get("name") or "").strip()
        if not url:
            label = name or "that feed"
            return (f"What's the feed URL for **{label}**? Send it like "
                    f"`Tartt, add {name or 'TechCrunch'} https://…/feed/`.")
        cadence = int(intent.get("cadence_hours") or DEFAULT_CADENCE_HOURS)
        r = add_feed(name or url, url, cadence)
        if r["status"] == "exists":
            return f"📥 **{r['name']}** is already an active feed — nothing changed."
        verb = "Re-enabled" if r["status"] == "reactivated" else "Added"
        return (f"✅ {verb} **{r['name']}** ({r['url']}) — polling every "
                f"{cadence}h. Live on the next poll.")

    if action == "remove_feed":
        r = remove_feed((intent.get("identifier") or "").strip())
        if r["status"] == "not_found":
            return "I couldn't find an active feed matching that. Try `Tartt, list feeds`."
        if r["status"] == "ambiguous":
            return ("That matches more than one feed: "
                    + ", ".join(f"**{m}**" for m in r["matches"])
                    + ". Which one?")
        return (f"✅ Paused **{r['name']}** — it won't be polled. Re-add it any time "
                f"to bring it back.")

    if action == "list_feeds":
        feeds = list_feeds()
        if not feeds:
            return "No active feeds right now. Add one with `Tartt, add <name> <url>`."
        lines = "\n".join(f"• **{f['name']}** — {f['url']} (every {f['cadence_hours']}h)"
                          for f in feeds)
        return f"📥 **Active feeds ({len(feeds)}):**\n{lines}"

    if action == "add_interest":
        topic = (intent.get("topic") or intent.get("identifier") or "").strip()
        if not topic:
            return "What topic should I add? e.g. `Tartt, I care about energy-transition stories`."
        r = add_interest(topic)
        if r["status"] == "exists":
            return f"🎯 **{r['topic']}** is already one of your interests."
        return (f"✅ Added the interest **{r['topic']}** — Tartt will weigh stories "
                f"toward it from the next poll.")

    if action == "remove_interest":
        ident = (intent.get("identifier") or intent.get("topic") or "").strip()
        if not ident:
            return "Which interest should I drop? Try `Tartt, list interests`."
        r = remove_interest(ident)
        if r["status"] == "not_found":
            return (f"No interest matched **{ident}**. `Tartt, list interests` shows "
                    f"the exact wording.")
        return f"✅ Dropped **{r['topic']}** from your interests."

    if action == "list_interests":
        topics = list_interests()
        if not topics:
            return "No interest topics set. Add one with `Tartt, I care about …`."
        lines = "\n".join(f"• {t}" for t in topics)
        return f"🎯 **Your interests ({len(topics)}):**\n{lines}"

    return ("I can manage Tartt's **feeds** and **interests** — e.g. "
            "`Tartt, add TechCrunch https://techcrunch.com/feed/`, `Tartt, drop arxiv`, "
            "`Tartt, I care about energy-transition stories`, or `Tartt, list feeds`.")
