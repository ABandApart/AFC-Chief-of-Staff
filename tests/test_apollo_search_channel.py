"""Tests for the ICP-refined Apollo search channel (Track O Part 0).

The refinement IS the value here, so the gates are what the tests pin: a candidate
must have a domain, be in-country, be in the headcount band, and carry an on-ICP
industry (with `staffing & recruiting` always dropped). The channel wiring
(dedupe, fail-open, error-skip) is pinned with a mocked search.
"""

from __future__ import annotations

from agents.outreach import apollo
from agents.outreach.discovery import apollo_search

_GATES = {
    "country": "United States",
    "headcount_min": 10,
    "headcount_max": 250,
    "exclude_industries": ["staffing & recruiting"],
}
_INDUSTRIES = ["professional training & coaching", "management consulting"]


def _org(**over):
    base = {
        "name": "AIIR Consulting",
        "primary_domain": "aiir.co",
        "country": "United States",
        "estimated_num_employees": 85,
        "industry": "professional training & coaching",
        "website_url": "https://aiir.co",
    }
    base.update(over)
    return base


# --- classify: the refinement gates -------------------------------------------


def test_classify_passes_an_on_icp_firm_and_shapes_the_candidate():
    c = apollo_search.classify(_org(), "coaching_leadership", _GATES, _INDUSTRIES)
    assert c["company_name"] == "AIIR Consulting"
    assert c["company_url"] == "https://aiir.co"
    assert c["segment"] == "coaching_leadership"
    assert c["headcount_band"] == "85"          # point estimate icp.parse_headcount reads
    assert c["discovered_via"] == "apollo_search"


def test_classify_drops_domainless_row():
    assert apollo_search.classify(_org(primary_domain=None), "s", _GATES, _INDUSTRIES) is None
    assert apollo_search.classify(_org(primary_domain=""), "s", _GATES, _INDUSTRIES) is None


def test_classify_drops_out_of_country():
    assert apollo_search.classify(
        _org(country="United Kingdom"), "s", _GATES, _INDUSTRIES) is None


def test_classify_drops_out_of_headcount_band():
    assert apollo_search.classify(
        _org(estimated_num_employees=800), "s", _GATES, _INDUSTRIES) is None
    # Unknown headcount is NOT dropped — missing data is not evidence of a bad fit.
    assert apollo_search.classify(
        _org(estimated_num_employees=None), "s", _GATES, _INDUSTRIES) is not None


def test_classify_drops_excluded_industry_even_if_keyword_matched():
    # The dominant noise: a recruiter that uses "executive coaching" as a keyword.
    assert apollo_search.classify(
        _org(industry="staffing & recruiting"), "s", _GATES, _INDUSTRIES) is None


def test_classify_drops_industry_off_the_segment_allowlist():
    assert apollo_search.classify(
        _org(industry="information technology & services"), "s", _GATES, _INDUSTRIES) is None


def test_classify_empty_allowlist_keeps_any_non_excluded_industry():
    # A segment with no allowlist falls back to the global exclude only.
    assert apollo_search.classify(
        _org(industry="whatever"), "s", _GATES, []) is not None


# --- find: channel wiring -----------------------------------------------------

_CONFIG = {
    "gates": _GATES,
    "segments": {"coaching_leadership": {
        "keyword_tags": ["executive coaching"],
        "industries": _INDUSTRIES,
    }},
}


def test_find_refines_and_dedupes_by_domain(mocker):
    page = {"organizations": [
        _org(name="A", primary_domain="a.com"),
        _org(name="A dup", primary_domain="a.com"),           # same domain → deduped
        _org(name="Recruiter", primary_domain="r.com", industry="staffing & recruiting"),
        _org(name="Generic IT", primary_domain="it.com",
             industry="information technology & services"),   # off allowlist → dropped
    ], "pagination": {"total_pages": 1}}
    mocker.patch.object(apollo_search.apollo, "search_organizations", return_value=page)

    out = apollo_search.find("coaching_leadership", config=_CONFIG, api_key="k")
    assert [c["company_url"] for c in out] == ["https://a.com"]  # only the on-ICP, deduped


def test_find_unconfigured_segment_is_empty(mocker):
    search = mocker.patch.object(apollo_search.apollo, "search_organizations")
    assert apollo_search.find("nope", config=_CONFIG, api_key="k") == []
    search.assert_not_called()


def test_find_skips_a_tag_that_hits_the_rate_cap(mocker):
    mocker.patch.object(
        apollo_search.apollo, "search_organizations",
        side_effect=apollo.ApolloRateLimitError(apollo.APOLLO_SEARCH_URL, "30"),
    )
    # A rate cap on the only tag → no candidates, but no raise (fail-open channel).
    assert apollo_search.find("coaching_leadership", config=_CONFIG, api_key="k") == []


def test_find_without_a_key_is_a_noop_not_a_raise(mocker):
    mocker.patch.object(apollo_search.creds, "keychain_get",
                        side_effect=RuntimeError("no key"))
    assert apollo_search.find("coaching_leadership", config=_CONFIG) == []


# --- the shipped config encodes the operator's 2026-08-28 decisions ------------


def test_shipped_config_matches_operator_decisions():
    cfg = apollo_search.load_config()
    segments = cfg["segments"]
    # Only product_design kept of the unscored three; engineering + msp_it dropped.
    assert set(segments) == {
        "coaching_leadership", "corporate_l_and_d",
        "instructional_design", "product_design_agency",
    }
    # Scored segments tightened to core — no management consulting / human resources.
    for seg in ("coaching_leadership", "corporate_l_and_d", "instructional_design"):
        inds = segments[seg]["industries"]
        assert "management consulting" not in inds
        assert "human resources" not in inds
    # Global noise drop is still in place.
    assert "staffing & recruiting" in cfg["gates"]["exclude_industries"]
