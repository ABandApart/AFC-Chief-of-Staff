"""Google News channel — segment queries into candidate firms (Part 0 · R0.4/R0.21).

Google News publishes an unauthenticated RSS search endpoint. Querying it is not
scraping and needs no account, which is why it survives the "no source whose
terms prohibit automated access" rule.

The feed returns **stories, not firms** — that was the finding that blocked this
channel until entity extraction was sanctioned (R0.21). The shape is therefore:

    segment -> queries -> RSS items -> ONE bounded LLM call -> named companies
            -> verify.py fetches each domain -> survivors reach the pool

RSS is parsed with the standard library rather than `feedparser`. The format is
trivial and stdlib keeps this channel free of the optional dependency groups that
are not synced on the build box.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any

from agents.outreach.discovery import extract

logger = logging.getLogger(__name__)

NAME = "news_query"
USER_AGENT = "aiadaptive-cos-outreach (Track O discovery)"
TIMEOUT_SECONDS = 20
MAX_ITEMS_PER_QUERY = 25

# US English, matching OQ-C's US-only scope.
FEED = ("https://news.google.com/rss/search"
        "?q={query}&hl=en-US&gl=US&ceid=US:en")

# Queries per segment. Deliberately trigger-shaped rather than descriptive: a
# query for "leadership development firm" returns listicles and press releases
# from the same handful of large players, while one anchored on an EVENT returns
# firms in the act of doing something - which is also what makes them worth a
# touch. The eight triggers in `35-` are the vocabulary being mined here.
QUERIES: dict[str, tuple[str, ...]] = {
    "corporate_l_and_d": (
        '"corporate training" company (acquires OR "series a" OR launches) platform',
        '"sales training" firm ("new CEO" OR expands OR raises)',
    ),
    "coaching_leadership": (
        '"leadership development" firm (raises OR acquires OR launches) platform',
        '"executive coaching" company ("new CEO" OR expansion OR funding)',
    ),
    "instructional_design": (
        '"instructional design" agency (acquires OR launches OR raises)',
        '"elearning development" company expands',
    ),
    "engineering_consultancy": (
        '"engineering consultancy" (acquires OR expands OR "new CEO")',
        '"engineering services" firm launches practice',
    ),
    "product_design_agency": (
        '"product design agency" (acquires OR expands OR launches)',
        '"design studio" acquired agency',
    ),
    "msp_it_consultancy": (
        '"managed service provider" (acquires OR expands OR "new CEO")',
        '"IT consultancy" firm acquires',
    ),
}


def feed_url(query: str) -> str:
    return FEED.format(query=urllib.parse.quote(query))


def parse_rss(xml_text: str) -> list[dict[str, Any]]:
    """Read RSS 2.0 into title/link/summary/published dicts. Never raises."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.warning("discovery: news feed was not parseable XML")
        return []
    items: list[dict[str, Any]] = []
    for node in root.iter("item"):
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        if not (title and link):
            continue
        items.append({
            "title": title,
            "link": link,
            "summary": (node.findtext("description") or "").strip(),
            "published": (node.findtext("pubDate") or "").strip(),
        })
    return items


def fetch(query: str) -> list[dict[str, Any]]:
    """Fetch and parse one query's feed. A failure is a warning, not a raise."""
    request = urllib.request.Request(feed_url(query),
                                     headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        logger.warning("discovery: news query %r failed: %s", query, exc)
        return []
    return parse_rss(body)[:MAX_ITEMS_PER_QUERY]


def find(segment: str) -> list[dict[str, Any]]:
    """Candidates for one segment: fetch every query, extract once, shape."""
    queries = QUERIES.get(segment, ())
    items: list[dict[str, Any]] = []
    for query in queries:
        items.extend(fetch(query))
    if not items:
        return []

    companies = extract.extract(items, segment)
    return extract.to_candidates(
        companies, segment,
        channel=NAME,
        query="; ".join(queries),
    )
