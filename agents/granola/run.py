"""Granola meeting-note poller (Track C — channel 1).

A scheduled one-shot: pull Granola notes updated since the last run and ingest
each into the cognee graph (mode-1) via the shared `ingest_note` core. Run by the
control-plane scheduler on a cron (`loops/granola-poll.md`), so this just does one
poll cycle and exits — like `agents/briefing/run.py`.

Flow:
  configure_cognee() → read the `granola` watermark (channel_state) →
  list notes updated_after the watermark → for each, fetch full note +
  assemble the text blob → ingest_note(dataset="granola", label_agent="granola")
  → advance the watermark to the last successfully-ingested note.

Design choices:
  - **Watermark** (`channel_state.cursor`) holds the max `updated_at` ingested, so
    each run fetches only new/edited notes. It advances only across a *contiguous*
    run of successes (stop at the first failure) so a transient error never skips
    a note — it's retried next cycle. `capture_messages` content-hash dedup is the
    correctness backstop; the watermark is just fetch efficiency.
  - **Soft breaker**: `assert_under_ceiling("granola")` up front — if today's
    granola spend is over the $3 ceiling, skip the cycle cleanly (log, exit 0).
  - Needs the keys + cognee (barry-agent). `configure_cognee()` runs first.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime

from agents._lib import cognee_setup, creds, db, granola_client, meeting_graph, runs
from agents._lib.ingest import ingest_note

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] granola: %(message)s",
)
logger = logging.getLogger(__name__)

CHANNEL = "granola"
API_KEY_ITEM = "granola-api-key"

# Bound the work per cycle: at most this many notes are cognified per run; the
# rest carry to the next cycle (the watermark advances to the last one done).
# Caps Anthropic extraction spend + wall-time per cycle. (Embeddings are local
# now, so there's no embedding rate limit to respect — this is about spend/time.)
MAX_NOTES_PER_RUN = 10


def go_forward_seed_needed(watermark: str | None, *, backfill: bool) -> bool:
    """First run with no watermark and no --backfill → seed go-forward (pure).

    Operator chose go-forward (2026-08-03): don't backfill history on the first
    poll. With no stored watermark we seed it to 'now' and ingest nothing
    historical; new/updated notes are picked up from the next cycle. `--backfill`
    opts into ingesting all history instead.
    """
    return watermark is None and not backfill


def _get_watermark(channel: str) -> str | None:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT cursor FROM channel_state WHERE channel = %s", (channel,))
            row = cur.fetchone()
            return row[0] if row else None


def _set_watermark(channel: str, cursor: str) -> None:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO channel_state (channel, cursor, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (channel)
                DO UPDATE SET cursor = EXCLUDED.cursor, updated_at = now()
                """,
                (channel, cursor),
            )


async def _run(*, backfill: bool = False, since: str | None = None) -> int:
    cognee_setup.configure_cognee()
    token = creds.keychain_get(API_KEY_ITEM)

    try:
        runs.assert_under_ceiling(CHANNEL)
    except runs.DailyCeilingExceeded as e:
        logger.warning("skipping poll — %s", e)
        return 0

    watermark = since or await asyncio.to_thread(_get_watermark, CHANNEL)

    # Go-forward: on a first run with no watermark, seed to now and skip history.
    if go_forward_seed_needed(watermark, backfill=backfill):
        now = datetime.now(UTC).isoformat()
        await asyncio.to_thread(_set_watermark, CHANNEL, now)
        logger.info("no watermark — seeded go-forward to %s; historical notes "
                    "skipped (use --backfill to ingest history)", now)
        return 0

    summaries = await asyncio.to_thread(
        granola_client.iter_note_summaries, token, updated_after=watermark
    )
    if not summaries:
        logger.info("no notes updated since %s — nothing to do", watermark or "(start)")
        return 0

    # Cap the batch; the remainder carries to the next cycle via the watermark.
    batch = summaries[:MAX_NOTES_PER_RUN]
    deferred = len(summaries) - len(batch)
    logger.info("%d note(s) to check since %s; processing %d%s",
                len(summaries), watermark or "(start)", len(batch),
                f", {deferred} deferred to next cycle" if deferred else "")
    captured = reposted = 0
    new_watermark = watermark

    for summary in batch:
        note_id = summary["id"]
        try:
            note = await asyncio.to_thread(
                granola_client.get_note, token, note_id, include_transcript=True
            )
            text = granola_client.assemble_note_text(note)
            if not text.strip():
                logger.info("note %s has no text — skipped", note_id)
            else:
                result = await ingest_note(
                    text,
                    source_ref=note_id,
                    source_type="granola",
                    dataset="granola",
                    label_agent="granola",
                    label_function="customer_discovery",
                )
                if result == "captured":
                    captured += 1
                    # Hybrid: also attach the typed Meeting + Person nodes (an
                    # idempotent upsert on deterministic ids). Guarded — a
                    # structured-insert failure must NOT fail the note; the mode-1
                    # content is already durable.
                    try:
                        await meeting_graph.add_meeting_graph(note)
                    except Exception:
                        logger.exception(
                            "typed Meeting insert failed for %s (mode-1 content is "
                            "durable; will retry on the note's next update)", note_id
                        )
                else:
                    reposted += 1
        except Exception:
            # Stop advancing the watermark at the last success so this note is
            # retried next cycle rather than skipped.
            logger.exception("failed to ingest note %s — stopping this cycle here", note_id)
            break
        updated = note.get("updated_at") or summary.get("updated_at")
        if updated and (new_watermark is None or updated > new_watermark):
            new_watermark = updated

    if new_watermark and new_watermark != watermark:
        await asyncio.to_thread(_set_watermark, CHANNEL, new_watermark)

    logger.info("done: %d captured, %d already-ingested; watermark → %s",
                captured, reposted, new_watermark)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll Granola and ingest notes into the graph.")
    parser.add_argument("--backfill", action="store_true",
                        help="ingest ALL history (ignore the go-forward seed); drains over "
                             "cycles at the per-run cap")
    parser.add_argument("--since", metavar="ISO",
                        help="only notes updated after this ISO timestamp (overrides the "
                             "stored watermark for this run)")
    args = parser.parse_args()
    return asyncio.run(_run(backfill=args.backfill, since=args.since))


if __name__ == "__main__":
    sys.exit(main())
