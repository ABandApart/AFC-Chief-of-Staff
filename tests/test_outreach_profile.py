"""Unit tests for Part 1 — news observation (Track O).

The pure feed/URL/graph logic is tested directly; the SQL writes are covered by
integration tests on barry-agent (this file stays network- and cognee-free). The
guarantees that must hold:

  * **Dedup is stable across tracking junk** — a story seen twice, with different
    query strings, is one signal, or R19 fails through a side door.
  * **A news item is never re-dated.** `insert_watch_signal` is insert-or-skip,
    never an upsert that advances a last-seen date.
  * **The graph edge is present** on the built ContentItem, or the packet's
    background traversal returns nothing (the P2 failure V5 catches).
  * **A pool firm's signals reparent on promotion**, so history is not orphaned.
"""

from __future__ import annotations

from datetime import date

from agents._lib import ontology
from agents.outreach import news, profile_graph

# --- pure feed / URL logic ----------------------------------------------------


def test_google_news_url_quotes_a_bare_company_name():
    url = news.google_news_url("AIIR Consulting")
    assert "%22AIIR%20Consulting%22" in url  # phrase match on the quoted name


def test_a_query_override_replaces_the_name():
    url = news.google_news_url("Maestro", "Maestro learning platform")
    assert "Maestro%20learning%20platform" in url
    assert "%22Maestro%22" not in url


def test_canonical_url_lowercases_host_keeps_path_drops_query_and_fragment():
    assert (news.canonical_url("HTTPS://News.Example.com/Story/A?utm=x#f")
            == "https://news.example.com/Story/A")


def test_dedup_is_stable_across_tracking_params():
    """The identity of a news link is host+path; query strings are session junk.
    A drifting key re-observes the same story as new — R19 through a side door."""
    assert news.dedup_key("https://n.example/a?b=1") == \
           news.dedup_key("https://n.example/a?c=2")


def test_dedup_distinguishes_genuinely_different_paths():
    assert news.dedup_key("https://n.example/a") != news.dedup_key("https://n.example/b")


def test_published_date_parses_rfc822_and_iso():
    assert news.parse_published("Wed, 20 Aug 2026 12:00:00 +0000") == \
           (date(2026, 8, 20), "published")
    assert news.parse_published("2026-08-20") == (date(2026, 8, 20), "published")


def test_an_unparseable_date_falls_back_to_discovered_not_a_guess():
    """The packet must never claim a precision it does not have (increment 1b)."""
    assert news.parse_published("last tuesday") == (None, "discovered")
    assert news.parse_published(None) == (None, "discovered")


def test_a_firm_always_has_google_news_and_a_newsroom_only_when_configured():
    def kinds(firm):
        return [k for k, _ in news.feed_urls_for(firm)]
    assert kinds({"company_name": "X"}) == ["news_rss"]
    assert kinds({"company_name": "X", "news_feed_url": "https://x/feed"}) == \
           ["news_rss", "newsroom_rss"]


# --- graph builders (id determinism + the load-bearing edge) ------------------


def test_organization_id_is_stable_and_case_insensitive_on_domain():
    assert profile_graph.organization_id("AIIRConsulting.com") == \
           profile_graph.organization_id("aiirconsulting.com")


def test_a_news_item_node_id_matches_the_sql_dedup_key():
    """The graph node and the SQL row must dedup identically, or the two homes
    drift and a story is one place but two in the other."""
    a = profile_graph.news_item_id("https://n.example/A?x=1")
    b = profile_graph.news_item_id("https://n.example/A?y=2")
    assert a == b


def test_the_content_item_carries_the_org_edge():
    """A node with no edge traverses to nothing — the failure V5 exists for."""
    firm = {"company_domain": "aiirconsulting.com", "company_name": "AIIR",
            "segment": "coaching_leadership"}
    org = profile_graph.build_organization(firm)
    item = profile_graph.build_news_item("https://n/a", "AIIR raises", org)
    assert [o.name for o in item.about_orgs] == ["AIIR"]
    assert isinstance(item, ontology.ContentItem)


def test_the_ontology_edge_defaults_empty():
    """Additive and default-empty, so every other ContentItem is unaffected."""
    assert ontology.ContentItem(url="u", title="t").about_orgs == []


# --- the signal row shape -----------------------------------------------------


def test_a_signal_row_carries_its_parent_and_the_title_as_excerpt():
    from agents.outreach.profile import _signal_row
    firm = {"parent": "discovery", "id": 7}
    item = {"source_kind": "news_rss", "url": "https://n/a",
            "dedup_key": "https://n/a", "title": "AIIR raises a round"}
    row = _signal_row(firm, item)
    assert row["parent"] == "discovery" and row["parent_id"] == 7
    assert row["excerpt"] == "AIIR raises a round"
