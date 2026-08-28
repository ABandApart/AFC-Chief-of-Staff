"""Unit tests for the Apollo firmographic lane (Track O Part 3, §3.3 V2 probe).

The probe's whole job is to COUNT non-null spine coverage on real targets, so the
mapping and the counting are what must be exactly right — a field mapped to the
wrong Apollo key would silently mis-measure coverage and send the provider
decision the wrong way. These tests fixture Apollo's real response shape (docs
read 2026-08-28) so the mapping is locked to the contract, and drive
`enrich_organization` through an injected fetch so no network or API key is
touched. The probe writes nothing, so there is nothing else to assert about state.
"""

from __future__ import annotations

import json

from agents.outreach import apollo

# A full Apollo organization object, trimmed to the keys the mapping reads plus a
# few it must ignore — the shape from https://api.apollo.io/api/v1/organizations/enrich.
_FULL_ORG = {
    "id": "abc123",
    "name": "AIIR Consulting",
    "industry": "professional training & coaching",
    "estimated_num_employees": 85,
    "founded_year": 2011,
    "total_funding": 12500000,
    "latest_funding_round_date": "2021-05-01",
    "latest_funding_stage": "Series A",
    "funding_events": [
        {"date": "2021-05-01", "investors": ["Acme Ventures", "Beta Capital"]},
        {"date": "2019-01-01", "investors": []},
    ],
    "city": "Philadelphia",
    "state": "Pennsylvania",
    "country": "United States",
    "raw_address": "1650 Arch St, Philadelphia, PA 19103",
    "publicly_traded_symbol": None,
    "annual_revenue": 9000000,  # present but not a spine field — must be ignored
}


# --- normalize_domain ---------------------------------------------------------


def test_normalize_domain_strips_scheme_www_at_and_path():
    assert apollo.normalize_domain("https://www.aiirconsulting.com/team") == "aiirconsulting.com"
    assert apollo.normalize_domain("HTTP://AIIR.CO") == "aiir.co"
    assert apollo.normalize_domain("@aiir.co") == "aiir.co"
    assert apollo.normalize_domain("  aiir.co  ") == "aiir.co"


# --- map_organization ---------------------------------------------------------


def test_map_full_org_covers_every_field_it_can():
    m = apollo.map_organization(_FULL_ORG)
    assert m["sector"] == "professional training & coaching"
    assert m["headcount"] == 85
    assert m["total_raised_usd"] == 12500000
    assert m["last_round_at"] == "2021-05-01"
    assert m["last_round_type"] == "Series A"
    assert m["lead_investor"] == "Acme Ventures"
    assert m["founded_year"] == 2011
    assert m["hq_location"] == "1650 Arch St, Philadelphia, PA 19103"


def test_headcount_asof_is_always_none_apollo_gives_no_date():
    # The observation date is ours to stamp at storage time, never Apollo's.
    assert apollo.map_organization(_FULL_ORG)["headcount_asof"] is None
    assert apollo.map_organization({"estimated_num_employees": 10})["headcount_asof"] is None


def test_map_sparse_org_is_all_none_where_absent():
    m = apollo.map_organization({"name": "Quiet Co"})
    assert all(m[f] is None for f in apollo.SPINE_FIELDS)


def test_ownership_type_is_public_only_when_traded_not_guessed_from_funding():
    # A funding stage is NOT taken as proof of a VC cap table.
    assert apollo.map_organization(_FULL_ORG)["ownership_type"] is None
    public = dict(_FULL_ORG, publicly_traded_symbol="AIIR")
    assert apollo.map_organization(public)["ownership_type"] == "public"


def test_lead_investor_handles_list_string_and_empty():
    as_list = {"funding_events": [{"investors": ["First", "Second"]}]}
    as_string = {"funding_events": [{"investors": "Solo Fund, Other"}]}
    assert apollo._lead_investor(as_list) == "First"
    assert apollo._lead_investor(as_string) == "Solo Fund"
    assert apollo._lead_investor({"funding_events": [{"investors": []}]}) is None
    assert apollo._lead_investor({}) is None


def test_hq_location_prefers_raw_then_falls_back_to_parts():
    partial = {"city": "Philadelphia", "country": "United States"}
    assert apollo._hq_location({"raw_address": "1 A St"}) == "1 A St"
    assert apollo._hq_location(partial) == "Philadelphia, United States"
    assert apollo._hq_location({}) is None


# --- coverage -----------------------------------------------------------------


def test_coverage_counts_non_null_non_empty_per_field():
    rows = [
        apollo.map_organization(_FULL_ORG),
        apollo.map_organization({"industry": "coaching"}),  # only sector
        apollo.map_organization({}),                         # nothing
    ]
    counts = apollo.coverage(rows)
    assert counts["sector"] == 2
    assert counts["headcount"] == 1
    assert counts["headcount_asof"] == 0  # never covered by Apollo
    assert counts["founded_year"] == 1


def test_coverage_treats_empty_string_as_absent():
    assert apollo.coverage([{"sector": ""}])["sector"] == 0


# --- enrich_organization (injected fetch, no network/key) ---------------------


def test_enrich_builds_correct_url_and_header_and_unwraps_org():
    seen = {}

    def fake_fetch(url, headers):
        seen["url"] = url
        seen["headers"] = headers
        return json.dumps({"organization": _FULL_ORG}).encode()

    org = apollo.enrich_organization("https://www.AIIR.co/x", "sekret", fetch=fake_fetch)
    assert org["name"] == "AIIR Consulting"
    assert seen["url"] == apollo.APOLLO_ENRICH_URL + "?domain=aiir.co"  # normalized
    assert seen["headers"]["x-api-key"] == "sekret"


def test_enrich_returns_none_when_apollo_has_no_org():
    org = apollo.enrich_organization(
        "nobody.example", "k", fetch=lambda u, h: json.dumps({}).encode()
    )
    assert org is None
