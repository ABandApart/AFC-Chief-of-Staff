"""Typed `ContentItem` DataPoints for Tartt (Phase 4, Task 3).

Mirrors `meeting_graph`: alongside any mode-1 text, each discovered article gets
a **typed `ContentItem` node** (url, title, summary) so content is
entity-addressable and interest-scorable in the graph. **Entity resolution =
deterministic id:** a ContentItem's id is `uuid5` of its URL, so a re-seen URL
**upserts to one node** instead of duplicating (like the meeting/person ids).

`add_data_points` embeds only the `index_fields` (title+summary) via the local
bge embedder — no LLM, ~free. Runs under the `tartt` telemetry label.
`build_content_item` is pure (no cognee); `add_content_item` is the graph write.

Note: the typed node is added for **all** items (cheap, local embed); mode-1
cognify (Anthropic) is interest-GATED and lands in Task 4 alongside scoring.
"""

from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from agents._lib import ontology
from agents._lib.telemetry_context import labeled

# The cognee pgvector table holding InterestSignal nodes (one row per topic; the
# topic text lives in payload->>'text'). scoring.py reads it for the cosine; the
# management functions below read/delete it. Named here so the coupling to
# cognee's internal layout is in one place.
_INTEREST_TABLE = '"InterestSignal_topic_label"'

# Stable namespaces → ids reproducible across runs/processes.
_CONTENT_NS = uuid5(NAMESPACE_URL, "afc-richmond/content")
_INTEREST_NS = uuid5(NAMESPACE_URL, "afc-richmond/interest")


def content_item_id(url: str) -> UUID:
    """Deterministic ContentItem id: `uuid5` of the (stripped) URL — the dedup key.

    Not lowercased: URL paths are case-sensitive, so two differently-cased URLs
    are treated as distinct articles (matching the tracker's UNIQUE(url)).
    """
    return uuid5(_CONTENT_NS, url.strip())


def build_content_item(url: str, title: str, summary: str | None) -> ontology.ContentItem:
    """Construct the typed ContentItem DataPoint (pure — no cognee)."""
    return ontology.ContentItem(
        id=content_item_id(url),
        url=url.strip(),
        title=(title or "").strip(),
        summary=(summary or None),
    )


async def add_content_item(url: str, title: str, summary: str | None) -> str:
    """Insert the typed ContentItem into the graph (local bge embed, no LLM).

    Returns the node id as a string. `configure_cognee()` must have run first.
    Raises on failure — the caller decides (the tracker row is written only after
    this succeeds, so the graph node and the tracker stay consistent).
    """
    from cognee.tasks.storage import add_data_points  # lazy — optional cognee

    item = build_content_item(url, title, summary)
    with labeled("tartt", "news_aggregation", trigger_kind="scheduled", correlation_id=url):
        await add_data_points([item])
    return str(item.id)


def interest_signal_id(topic_label: str) -> UUID:
    """Deterministic InterestSignal id: `uuid5` of the lowercased topic label —
    re-seeding the same interest upserts to one node (topics are case-insensitive
    concepts, unlike URLs)."""
    return uuid5(_INTEREST_NS, topic_label.strip().lower())


def build_interest_signal(topic_label: str, weight: float = 1.0) -> ontology.InterestSignal:
    """Construct the typed InterestSignal DataPoint (pure — no cognee)."""
    return ontology.InterestSignal(
        id=interest_signal_id(topic_label),
        topic_label=topic_label.strip(),
        weight=weight,
    )


async def add_interest_signals(signals: list[tuple[str, float]]) -> list[str]:
    """Seed/refresh the operator's InterestSignal nodes (local bge embed, no LLM).

    Deterministic ids mean re-running with the same topics upserts rather than
    duplicating. Returns the node ids. `configure_cognee()` must have run first.
    """
    from cognee.tasks.storage import add_data_points  # lazy — optional cognee

    items = [build_interest_signal(label, weight) for label, weight in signals]
    with labeled("tartt", "news_aggregation", trigger_kind="manual"):
        await add_data_points(items)
    return [str(i.id) for i in items]


def list_interest_signals() -> list[str]:
    """The operator's current interest topics, from the graph (read-only, no LLM).

    Reads the topic text out of the cognee node payload. Sorted for a stable,
    readable listing. Empty list when nothing is seeded.
    """
    import psycopg

    from agents._lib import cognee_setup, creds

    dsn = cognee_setup.cognee_dsn(creds.keychain_get("db-url"))
    sql = (f"SELECT payload->>'text' FROM {_INTEREST_TABLE} "
           "WHERE payload->>'text' IS NOT NULL ORDER BY 1")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql)
        return [r[0] for r in cur.fetchall()]


def remove_interest_signals(topic_labels: list[str]) -> int:
    """Delete InterestSignal nodes by **topic text** (case-insensitive).

    Text-matched on purpose: some early rows were seeded with random ids rather
    than the deterministic `interest_signal_id`, so a delete-by-id would miss
    them. Deleting the row is what makes scoring stop counting the interest (the
    cosine in `scoring.py` reads every row in this table). Returns the count
    removed — 0 means nothing matched, which the caller reports as "not found".
    """
    import psycopg

    from agents._lib import cognee_setup, creds

    wanted = [t.strip().lower() for t in topic_labels if t.strip()]
    if not wanted:
        return 0
    dsn = cognee_setup.cognee_dsn(creds.keychain_get("db-url"))
    sql = f"DELETE FROM {_INTEREST_TABLE} WHERE lower(payload->>'text') = ANY(%s)"
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, (wanted,))
        return cur.rowcount
