"""Unit tests for Tartt's feed listing + extraction (Phase 4, Task 2).

`parse_feed` runs on an in-memory RSS string (feedparser is in `dev`); the
trafilatura extraction is guarded by importorskip since it lives in the `tartt`
group only. Network calls (`list_source_items`, `fetch_article_text`) are not
exercised here.
"""

from __future__ import annotations

import pytest

from agents.tartt import fetch

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Test feed</title>
  <item><title>First post</title><link>https://ex.com/1</link>
        <pubDate>Mon, 11 Aug 2026 09:00:00 GMT</pubDate></item>
  <item><title>Second post</title><link>https://ex.com/2</link></item>
  <item><title>Linkless item</title></item>
</channel></rss>"""


def test_parse_feed_extracts_items_with_links():
    items = fetch.parse_feed(RSS)
    # The linkless item is skipped; the two with links come through in order.
    assert [i["url"] for i in items] == ["https://ex.com/1", "https://ex.com/2"]
    assert items[0]["title"] == "First post"
    assert items[0]["published"]  # pubDate carried through


def test_parse_feed_empty_on_garbage():
    assert fetch.parse_feed("not a feed at all") == []


def test_extract_text_none_on_empty_input():
    # Guard clause returns before importing trafilatura.
    assert fetch.extract_text(None) is None
    assert fetch.extract_text("") is None


def test_extract_text_pulls_main_content():
    pytest.importorskip("trafilatura")
    html = (
        "<html><body><nav>menu</nav>"
        "<article><h1>Title</h1><p>This is the main article body worth reading.</p>"
        "</article><footer>junk</footer></body></html>"
    )
    out = fetch.extract_text(html)
    assert out and "main article body" in out
