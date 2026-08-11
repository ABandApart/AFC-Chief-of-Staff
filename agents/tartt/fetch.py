"""Tartt — feed listing + article extraction (Phase 4, Task 2).

Two building blocks the poller (`run.process_source`, wired in Task 3) composes:
list a source's feed into candidate items, and pull one article's clean main
text. Kept separate from the orchestration so both are unit-testable — the
entry-mapping and extraction work on in-memory strings, the network calls are
thin wrappers around them.

`feedparser` is light (in `dev`, so `parse_feed` is tested by default);
`trafilatura` (lxml) is heavy and lives in the `tartt` group only — imported
lazily so this module loads without it.
"""

from __future__ import annotations

import logging

import feedparser

logger = logging.getLogger(__name__)


def _map_entries(parsed: object) -> list[dict]:
    """Map a parsed feed's entries → item dicts, skipping any without a link."""
    items: list[dict] = []
    for e in parsed.entries:  # type: ignore[attr-defined]
        url = (getattr(e, "link", "") or "").strip()
        if not url:
            continue
        items.append(
            {
                "url": url,
                "title": (getattr(e, "title", "") or "").strip(),
                "published": getattr(e, "published", None),
            }
        )
    return items


def parse_feed(content: str | bytes) -> list[dict]:
    """Parse an RSS/Atom feed *body* into item dicts (pure — no network)."""
    return _map_entries(feedparser.parse(content))


def list_source_items(feed_url: str) -> list[dict]:
    """Fetch + parse a source's feed (network). feedparser handles the download,
    encoding, and redirects."""
    items = _map_entries(feedparser.parse(feed_url))
    logger.info("tartt: feed %s → %d item(s)", feed_url, len(items))
    return items


def extract_text(html: str | None) -> str | None:
    """Extract the main article text from HTML (trafilatura). Pure given `html`;
    returns None for empty input or when nothing extractable is found."""
    if not html:
        return None
    import trafilatura

    return trafilatura.extract(html)


def fetch_article_text(url: str) -> str | None:
    """Download an article URL and extract its clean main text (network)."""
    import trafilatura

    html = trafilatura.fetch_url(url)
    if html is None:
        logger.warning("tartt: could not fetch article %s", url)
        return None
    return extract_text(html)
