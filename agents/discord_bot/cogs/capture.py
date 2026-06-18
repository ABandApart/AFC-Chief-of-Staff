"""Capture cog — #capture listener → facts.

Flow (per `architecture/50-channel-layer.md` capture interaction):
  1. Operator posts a thought in #capture.
  2. Bot reacts ⏳.
  3. Claude Haiku (via the cost helper) extracts atomic facts as JSON.
  4. Gemini text-embedding-004 (via the cost helper) embeds each fact.
  5. Each fact is written to the `facts` table with provenance
     (source_type='discord', source_ref=<message_id>).
  6. ⏳ is replaced with ✅ and the bot replies with a one-line summary.

Edge cases:
  - Empty message → ask for text, no LLM call.
  - No extractable facts (e.g. a bare URL) → 🤔 + a nudge, nothing written.
  - Unparseable extraction → ⚠️ + a rephrase nudge, nothing written.

The blocking pipeline (LLM calls + DB writes) runs in a worker thread via
`asyncio.to_thread` so the Discord event loop stays responsive.
"""

from __future__ import annotations

import asyncio
import json
import logging

import discord
from discord.ext import commands

from agents._lib.runs import agent_run
from agents.discord_bot.brain import insert_fact
from agents.discord_bot.config import CAPTURE_CHANNEL_ID

logger = logging.getLogger(__name__)

EXTRACTION_MODEL = "claude-haiku-4-5"
# gemini-embedding-001 is the embedContent model available on our key
# (text-embedding-004 returns 404). The cost helper requests 768 dims to
# match the facts.embedding vector(768) column.
EMBEDDING_MODEL = "gemini-embedding-001"

EXTRACTION_SYSTEM_PROMPT = """You extract atomic facts from a person's captured note.

Return ONLY valid JSON of this exact shape:
{"facts": [{"content": "<one atomic claim>", "domain": "<short category>", "confidence": <number 0.0-1.0>}]}

Rules:
- Each fact is ONE atomic, self-contained claim. Split compound statements into separate facts.
- Write each fact as a complete sentence that is understandable on its own, without the original note.
- `domain` is a short lowercase category, e.g.: business, project, decision, contact, personal, idea, task, preference.
- `confidence` reflects how explicitly the note states the fact (1.0 = stated outright, lower = inferred).
- If the note has no extractable facts (e.g. it is only a URL with no commentary, or it is empty), return {"facts": []}.
- Never invent facts that the note does not support.
- Output JSON only. No prose. No markdown code fences."""


def parse_facts(raw: str) -> list[dict]:
    """Parse the model's extraction output into a list of validated facts.

    Tolerant of markdown code fences. Returns a (possibly empty) list of
    dicts: {"content": str, "domain": str|None, "confidence": float}.

    Raises ValueError if the payload is not parseable JSON or is missing the
    `facts` key — the caller treats that as a graceful "couldn't structure
    this" rather than writing garbage.
    """
    text = raw.strip()

    # Strip a leading ```json / ``` fence and trailing ``` if present.
    if text.startswith("```"):
        lines = text.splitlines()
        lines = lines[1:]  # drop opening fence line
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]  # drop closing fence
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"extraction output is not valid JSON: {e}") from e

    if not isinstance(data, dict) or "facts" not in data:
        raise ValueError("extraction JSON missing 'facts' key")

    facts_in = data["facts"]
    if not isinstance(facts_in, list):
        raise ValueError("'facts' is not a list")

    out: list[dict] = []
    for item in facts_in:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        domain = item.get("domain")
        domain = domain.strip() if isinstance(domain, str) and domain.strip() else None
        conf_raw = item.get("confidence", 1.0)
        try:
            conf = float(conf_raw)
        except (TypeError, ValueError):
            conf = 1.0
        conf = max(0.0, min(1.0, conf))
        out.append({"content": content.strip(), "domain": domain, "confidence": conf})
    return out


def format_summary(facts: list[dict]) -> str:
    """Build the one-line (or short) reply summarizing what was captured."""
    n = len(facts)
    domains = sorted({f["domain"] for f in facts if f["domain"]})
    dstr = f" ({', '.join(domains)})" if domains else ""
    if n == 1:
        return f"Captured 1 fact{dstr}: {facts[0]['content']}"
    preview = "\n".join(f"• {f['content']}" for f in facts[:5])
    more = f"\n…and {n - 5} more" if n > 5 else ""
    return f"Captured {n} facts{dstr}:\n{preview}{more}"


class CaptureCog(commands.Cog):
    """Listens to #capture and turns notes into facts."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _process_capture(self, text: str, message_id: str) -> list[dict]:
        """Synchronous pipeline: extract → embed → insert. Runs in a thread.

        Returns the list of inserted facts (each with an added `id`). Empty
        list means the model found nothing to remember. Raises ValueError on
        an unparseable extraction; other exceptions propagate as failures.
        """
        # 1. Extract (Anthropic — its own agent_runs row)
        with agent_run(
            "fact-extraction",
            "infrastructure",
            trigger_kind="event",
            correlation_id=message_id,
            correlation_kind="discord_message",
        ) as run:
            raw = run.call_anthropic(
                messages=[{"role": "user", "content": text}],
                model=EXTRACTION_MODEL,
                max_input_tokens=4000,
                max_output_tokens=600,
                system=EXTRACTION_SYSTEM_PROMPT,
            )

        facts = parse_facts(raw)
        if not facts:
            return []

        # 2. Embed (Gemini — its own agent_runs row)
        contents = [f["content"] for f in facts]
        with agent_run(
            "fact-extraction",
            "infrastructure",
            trigger_kind="event",
            correlation_id=message_id,
            correlation_kind="discord_message",
        ) as run:
            embeddings = run.call_embedding(contents, model=EMBEDDING_MODEL)

        # 3. Insert each fact with its embedding
        inserted: list[dict] = []
        for fact, emb in zip(facts, embeddings):
            fid = insert_fact(
                content=fact["content"],
                source_type="discord",
                source_ref=message_id,
                domain=fact["domain"],
                confidence=fact["confidence"],
                embedding=emb,
            )
            inserted.append({**fact, "id": fid})
        return inserted

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Only #capture; never react to our own (or any bot's) messages.
        if message.channel.id != CAPTURE_CHANNEL_ID:
            return
        if message.author.bot:
            return

        text = message.content.strip()
        if not text:
            await message.reply(
                "I need text to capture — send a sentence (a link by itself isn't enough).",
                mention_author=False,
            )
            return

        await message.add_reaction("⏳")
        try:
            facts = await asyncio.to_thread(self._process_capture, text, str(message.id))
            await _safe_remove_hourglass(message, self.bot)

            if not facts:
                await message.add_reaction("🤔")
                await message.reply(
                    "I didn't find anything to remember there — add a sentence about why it matters.",
                    mention_author=False,
                )
                return

            await message.add_reaction("✅")
            await message.reply(format_summary(facts), mention_author=False)
            logger.info(
                "capture msg %s → %d fact(s) [%s]",
                message.id,
                len(facts),
                ", ".join(str(f["id"]) for f in facts),
            )

        except ValueError as e:
            await _safe_remove_hourglass(message, self.bot)
            await message.add_reaction("⚠️")
            await message.reply(
                "I couldn't structure that into facts — try rephrasing it.",
                mention_author=False,
            )
            logger.warning("capture parse failure on msg %s: %s", message.id, e)

        except Exception:
            await _safe_remove_hourglass(message, self.bot)
            await message.add_reaction("⚠️")
            await message.reply(
                "Something went wrong capturing that — check the logs / #system.",
                mention_author=False,
            )
            logger.exception("capture failed on msg %s", message.id)


async def _safe_remove_hourglass(message: discord.Message, bot: commands.Bot) -> None:
    """Remove the ⏳ reaction, ignoring errors (it may already be gone)."""
    try:
        await message.remove_reaction("⏳", bot.user)
    except discord.HTTPException:
        pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CaptureCog(bot))
