"""Tests for the organizations/search pagination probe (Track O Part 0 sourcing).

The live caps (per_page honored, page ceiling, rate limit) are barry-agent's to
measure; here the request shape, the response reduction, and the WALK's stop
conditions are pinned with an injected search function — no network, no key. The
walk's job is to stop correctly (empty page, last page, rate cap, plan gate) and
to dedupe domains, so those are what the tests assert.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from agents.outreach import apollo
from cli import apollo_search_probe as probe

# --- search_organizations (request shape + guards) ----------------------------


def test_search_organizations_posts_filters_page_perpage_and_returns_body():
    seen = {}

    def fake_post(url, headers, body):
        seen["url"] = url
        seen["headers"] = headers
        seen["body"] = json.loads(body)
        return json.dumps({"organizations": [{"primary_domain": "a.com"}],
                           "pagination": {"page": 2, "per_page": 100,
                                          "total_entries": 33000, "total_pages": 330}}).encode()

    resp = apollo.search_organizations(
        {"q_organization_keyword_tags": ["coaching"]},
        page=2, per_page=100, api_key="k", fetch=fake_post,
    )
    assert seen["url"] == apollo.APOLLO_SEARCH_URL
    assert seen["headers"]["x-api-key"] == "k"
    assert seen["body"]["page"] == 2 and seen["body"]["per_page"] == 100
    assert seen["body"]["q_organization_keyword_tags"] == ["coaching"]
    assert resp["pagination"]["total_entries"] == 33000


def test_search_page_summary_counts_and_filters_domainless_rows():
    resp = {"organizations": [{"primary_domain": "a.com"}, {"primary_domain": None},
                              {"name": "no domain key"}],
            "pagination": {"total_entries": 5}}
    s = apollo.search_page_summary(resp)
    assert s["returned"] == 3
    assert s["with_domain"] == 1
    assert s["domains"] == ["a.com"]
    assert s["pagination"]["total_entries"] == 5


def test_429_becomes_rate_limit_with_retry_after():
    def raise_429(*_a, **_k):
        raise urllib.error.HTTPError(
            "u", 429, "Too Many Requests", {"Retry-After": "42"}, io.BytesIO(b"")
        )
    with pytest.raises(apollo.ApolloRateLimitError) as exc:
        apollo.search_organizations({}, page=1, per_page=100, api_key="k", fetch=raise_429)
    assert exc.value.retry_after == "42"


# --- the pagination walk ------------------------------------------------------


def _pager(pages):
    """A search_fn that returns canned responses by page number."""
    def search_fn(page, per_page):
        return pages[page]
    return search_fn


def _resp(n, *, total_entries=1000, total_pages=10, start=0):
    orgs = [{"primary_domain": f"d{start + i}.com"} for i in range(n)]
    return {"organizations": orgs,
            "pagination": {"page": 1, "per_page": 100,
                           "total_entries": total_entries, "total_pages": total_pages}}


def test_walk_dedupes_domains_across_pages():
    pages = {1: _resp(2, start=0), 2: _resp(2, start=1)}  # d1 overlaps
    rep = probe.walk_pages(_pager(pages), per_page=100, max_pages=2)
    assert rep["unique_domains"] == 3          # d0, d1, d2 — the repeat is deduped
    assert rep["pages"][1]["new_domains"] == 1  # only d2 is new on page 2


def test_walk_stops_on_empty_page():
    pages = {1: _resp(2), 2: _resp(0)}
    rep = probe.walk_pages(_pager(pages), per_page=100, max_pages=5)
    assert rep["stopped"] == "empty_page"
    assert len(rep["pages"]) == 2


def test_walk_stops_at_total_pages():
    pages = {1: _resp(2, total_pages=1)}
    rep = probe.walk_pages(_pager(pages), per_page=100, max_pages=5)
    assert rep["stopped"] == "reached_total_pages"


def test_walk_records_rate_limit_as_the_finding():
    def search_fn(page, per_page):
        if page == 1:
            return _resp(2)
        raise apollo.ApolloRateLimitError(apollo.APOLLO_SEARCH_URL, "30")
    rep = probe.walk_pages(search_fn, per_page=100, max_pages=5)
    assert rep["stopped"].startswith("rate_limited@page2")
    assert "retry_after=30s" in rep["stopped"]


def test_walk_records_plan_gate():
    def search_fn(page, per_page):
        raise apollo.ApolloPlanError(apollo.APOLLO_SEARCH_URL)
    rep = probe.walk_pages(search_fn, per_page=100, max_pages=3)
    assert rep["stopped"] == "plan_gated"
    assert rep["pages"] == []
