"""Graph builders for the profile poller (Track O, Part 1 · R1.6).

Pure DataPoint builders separated from the cognee writes, mirroring
`tartt.content_graph`. The builders are unit-testable for id determinism and
edge presence without cognee; the `add_*` coroutines need `configure_cognee()`
and therefore run on barry-agent only (C3 — cognee is not importable on the
build box).

Why `ContentItem` carries the edge and not `Fact`: the packet renders traversal
results through `retrieval.normalize_nodes`, which reads `title`/`name` and
`summary`/`text`. `ContentItem` has `title` and `summary` and renders as a
readable line; `Fact` has `content`, which those lookups miss, so it would render
as a bare uuid. The edge is one additive field on `ContentItem`
(`about_orgs`, migration-free — it is an ontology field), and the display path is
left untouched.
"""

from __future__ import annotations

from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from agents._lib import ontology
from agents.outreach import news

# Deterministic Organization id: uuid5 of the company domain. Domain is already
# the unique dedup key on both outreach tables (R0.10), so a re-run upserts to
# one node instead of duplicating — the same discipline as content_item_id.
_ORG_NS = uuid5(NAMESPACE_URL, "afc-richmond/outreach-org")


def organization_id(domain: str) -> UUID:
    return uuid5(_ORG_NS, domain.strip().lower())


def news_item_id(url: str) -> UUID:
    """The observed article's node id. `uuid5` of the canonical URL, so the same
    story seen twice is one node — matching the SQL dedup key (news.dedup_key)."""
    return uuid5(NAMESPACE_URL, news.canonical_url(url))


def build_organization(firm: dict[str, Any]) -> ontology.Organization:
    """The typed Organization node for a firm (pure — no cognee)."""
    return ontology.Organization(
        id=organization_id(firm["company_domain"]),
        name=firm["company_name"].strip(),
        segment=firm.get("segment") or firm.get("sector"),
    )


def build_news_item(url: str, title: str, org: ontology.Organization,
                    ) -> ontology.ContentItem:
    """A typed ContentItem edged to the firm's Organization node.

    The edge is what makes the packet's background traversal return something:
    `add_data_points` walks relationship fields into nodes and edges, and
    `get_neighborhood()` walks them back. A node with no edge traverses to
    nothing — the P2 failure V5 exists to catch.
    """
    return ontology.ContentItem(
        id=news_item_id(url),
        url=news.canonical_url(url),
        title=(title or "").strip(),
        about_orgs=[org],
    )


async def add_organization(firm: dict[str, Any]) -> str:
    """Upsert the firm's Organization node; return its id. barry-agent only."""
    from cognee.tasks.storage import add_data_points  # lazy — optional cognee

    org = build_organization(firm)
    await add_data_points([org])
    return str(org.id)


async def add_news_item(url: str, title: str, firm: dict[str, Any]) -> str:
    """Insert one news ContentItem edged to the firm's org. barry-agent only.

    No LLM: local bge embed, typed DataPoints. The org is rebuilt (deterministic
    id) so the edge resolves whether or not the org node was added this run.
    """
    from cognee.tasks.storage import add_data_points  # lazy — optional cognee

    org = build_organization(firm)
    item = build_news_item(url, title, org)
    await add_data_points([item])
    return str(item.id)
