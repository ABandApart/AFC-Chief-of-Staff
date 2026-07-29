"""Channel-agnostic note ingestion into the cognee graph (W5).

The core of capture, factored out of the Discord cog so the **primary** API
ingestion path and Discord share one implementation (operator 2026-07-28: API +
tools are the primary ingestion channel; Discord is one thin caller).

Flow: normalize + hash the text → skip if this exact note was already ingested
(pre-LLM dedup via `capture_messages` in `aiadaptive_cos`) → `cognee.add` +
`cognify` into the graph under a `labeled()` block (spend → `agent_runs` via the
M1 callback). The hash is recorded only after a successful cognify, so a failed
ingest can be retried verbatim.

cognee is imported lazily (optional `cognee` group); `configure_cognee()` must
have run first (bot startup / CLI start).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging

from agents._lib import db
from agents._lib.telemetry_context import labeled

logger = logging.getLogger(__name__)

CAPTURE_DATASET = "capture"


def message_hash(text: str) -> str:
    """sha256 hex of the normalized text (dedup key): whitespace runs collapsed
    and casefolded, so re-posts differing only in spacing/case still match."""
    normalized = " ".join(text.split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _message_seen(content_hash: str) -> bool:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM capture_messages WHERE content_hash = %s", (content_hash,)
            )
            return cur.fetchone() is not None


def _record_message(content_hash: str, source_ref: str) -> None:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO capture_messages (content_hash, message_id) "
                "VALUES (%s, %s) ON CONFLICT (content_hash) DO NOTHING",
                (content_hash, source_ref),
            )


async def ingest_note(
    text: str,
    *,
    source_ref: str,
    source_type: str = "discord",
    dataset: str = CAPTURE_DATASET,
    label_agent: str = "fact-extraction",
    label_function: str = "customer_discovery",
) -> str:
    """Ingest one note into the graph. Returns 'repost' (seen before, nothing
    done) or 'captured'. Raises on failure (the caller records/handles it).

    `dataset` selects the cognee dataset (default `capture` — the Discord/untrusted
    lane). `label_agent`/`label_function` attribute the cognify spend in the ledger
    (M1). The defaults reproduce Discord capture exactly; other channels (e.g. the
    Granola meeting poller) override them for their own dataset + spend attribution
    while sharing this one ingest core.
    """
    h = message_hash(text)
    if await asyncio.to_thread(_message_seen, h):
        logger.info("ingest %s: exact re-post (hash %s…) — skipped before cognify",
                    source_ref, h[:12])
        return "repost"

    import cognee  # lazy — optional `cognee` dependency group

    with labeled(
        label_agent, label_function,
        trigger_kind="event", correlation_id=source_ref,
    ):
        await cognee.add(text, dataset_name=dataset)
        await cognee.cognify(datasets=[dataset])

    await asyncio.to_thread(_record_message, h, source_ref)
    logger.info("ingest %s (%s): cognified into the '%s' dataset",
                source_ref, source_type, dataset)
    return "captured"
