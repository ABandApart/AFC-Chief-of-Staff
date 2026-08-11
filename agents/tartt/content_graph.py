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

# Stable namespace → ids reproducible across runs/processes.
_CONTENT_NS = uuid5(NAMESPACE_URL, "afc-richmond/content")


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
