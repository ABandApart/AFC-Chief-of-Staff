"""News feed helpers for the profile poller (Track O, Part 1 · pure).

Everything here is pure except the two thin fetch wrappers, so the URL, dedup and
date logic is unit-tested without a network. Feed parsing itself is reused from
`tartt.fetch` rather than reimplemented — `feedparser` already handles RSS, Atom,
encodings and redirects.

Distinct from `agents/outreach/discovery/news_query.py`, which mines news to
*find* companies. This module observes news *about a company already in hand*, so
its query is anchored on the company, not on a segment.
"""

from __future__ import annotations

import urllib.parse
from datetime import date, datetime
from typing import Any

from agents.tartt import fetch as tartt_fetch

# US English, matching OQ-C. Same endpoint the discovery channel uses; the
# difference is the query, which here names one company.
_GOOGLE_NEWS = ("https://news.google.com/rss/search"
                "?q={query}&hl=en-US&gl=US&ceid=US:en")

# Query params carry session/tracking junk that differs per fetch. Stripping the
# whole query is safe for news links, whose identity is host+path; keeping it
# would drift the dedup key and re-observe the same story as new (R19's failure).
_STRIP_QUERY = True


def google_news_url(company_name: str, query_override: str | None = None) -> str:
    """The company's Google News RSS feed URL.

    `query_override` is `outreach_targets.news_query` when the operator has set
    one — a common name needs a sector word or the founder's name to stop pulling
    in a namesake's news (V6). Otherwise the company name in double quotes, which
    Google News treats as a phrase match.
    """
    query = (query_override or "").strip() or f'"{company_name.strip()}"'
    return _GOOGLE_NEWS.format(query=urllib.parse.quote(query))


def canonical_url(url: str) -> str:
    """Lowercase the host, drop the fragment and (for news) the query, keep the
    path case-sensitively. The path is case-sensitive; the host is not."""
    parts = urllib.parse.urlsplit(url.strip())
    host = parts.netloc.lower()
    query = "" if _STRIP_QUERY else parts.query
    return urllib.parse.urlunsplit((
        parts.scheme.lower() or "https", host, parts.path.rstrip("/"), query, "",
    ))


def dedup_key(url: str) -> str:
    """The canonical URL is the key. Google News links are stable per story and
    newsroom feeds give real article URLs, so one rule covers both feeds."""
    return canonical_url(url)


# RFC 822 / RFC 2822 shapes feedparser passes through as strings, and ISO.
_DATE_FORMATS = (
    "%a, %d %b %Y %H:%M:%S %z",
    "%a, %d %b %Y %H:%M:%S %Z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%d",
)


def parse_published(raw: str | None) -> tuple[date | None, str]:
    """Return (event_date, basis). `basis` is 'published' when the feed's date
    parsed, else 'discovered' — the caller falls back to today and the packet
    must never claim a precision it does not have (the increment 1b discipline)."""
    text = (raw or "").strip()
    if text:
        for fmt in _DATE_FORMATS:
            try:
                return (datetime.strptime(text, fmt).date(), "published")
            except ValueError:
                continue
    return (None, "discovered")


def feed_urls_for(firm: dict[str, Any]) -> list[tuple[str, str]]:
    """The (source_kind, feed_url) pairs to poll for one firm.

    Google News is always available (derived from the name); the company's own
    newsroom feed only when `news_feed_url` is set. A firm with neither cannot
    happen — the name always yields a Google News query.
    """
    feeds: list[tuple[str, str]] = [(
        "news_rss",
        google_news_url(firm["company_name"], firm.get("news_query")),
    )]
    if firm.get("news_feed_url"):
        feeds.append(("newsroom_rss", firm["news_feed_url"]))
    return feeds


def feed_items_for_firm(firm: dict[str, Any]) -> list[dict[str, Any]]:
    """Every feed's items for one firm, tagged with their source_kind. The fetch
    is the only impure step; a failed feed yields nothing rather than raising, so
    one dead newsroom URL cannot cost the firm its Google News items."""
    items: list[dict[str, Any]] = []
    for source_kind, feed_url in feed_urls_for(firm):
        try:
            raw = tartt_fetch.list_source_items(feed_url)
        except Exception:  # noqa: BLE001 - a failed feed is data, not a crash
            continue
        for item in raw:
            event_date, basis = parse_published(item.get("published"))
            items.append({
                "source_kind": source_kind,
                "url": item["url"],
                "title": item.get("title", ""),
                "event_date": event_date,
                "date_basis": basis,
                "dedup_key": dedup_key(item["url"]),
            })
    return items
