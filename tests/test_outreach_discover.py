"""Unit tests for discovery sourcing, verification, and the daily run.

Network-free: every fetch is patched. The guarantees that must hold:

  * **Verification never raises** — a failed probe is data, not a crash, and
    "could not look" must never read as "verified".
  * **LinkedIn is never fetched** (R14 is Policy). The kind records that a URL is
    on file, nothing more.
  * **An empty ATS board is not evidence** — it is AIIR's exact situation.
  * **A thin firm is recorded, not skipped**, so the next run dedups instead of
    re-probing forever, and it stays eligible under R0.19's upward-only rule.
  * **Nothing is fabricated to hit a number** — the daily 25 is a review ceiling,
    not a sourcing quota.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agents.outreach import discover, verify
from agents.outreach.discovery import seed_list

# --- verification -------------------------------------------------------------


def test_linkedin_check_never_fetches(mocker):
    """R14 is Policy. A network call here would be both a breach and unreliable."""
    opened = mocker.patch.object(verify.urllib.request, "urlopen")
    ok, note = verify.check_linkedin_url("https://www.linkedin.com/company/acme")
    assert ok is True
    assert "not fetched" in note
    opened.assert_not_called()


def test_a_non_company_linkedin_url_does_not_count():
    ok, _ = verify.check_linkedin_url("https://www.linkedin.com/in/someone")
    assert ok is False


def test_missing_fields_fail_closed():
    assert verify.check_linkedin_url(None)[0] is False
    assert verify.check_live_site(None)[0] is False
    assert verify.check_open_reqs(None)[0] is False


def test_an_unreachable_site_is_a_result_not_an_exception(mocker):
    mocker.patch.object(verify.urllib.request, "urlopen",
                        side_effect=OSError("boom"))
    ok, note = verify.check_live_site("https://nope.example")
    assert ok is False and "unreachable" in note


def test_an_empty_board_is_not_evidence(mocker):
    """A detected but empty board is AIIR's situation — live, and telling us
    nothing about whether the firm is operating."""
    mocker.patch.object(verify.adapters, "fetch_open_roles",
                        return_value=verify.adapters.BoardResult(
                            ok=True, roles=[], provider="workable"))
    ok, note, provider = verify.check_open_reqs("https://apply.workable.com/acme/")
    assert ok is False
    assert "empty" in note and provider == "workable"


def test_an_unreadable_board_is_not_evidence(mocker):
    mocker.patch.object(verify.adapters, "fetch_open_roles",
                        return_value=verify.adapters.BoardResult(
                            ok=False, reason="404"))
    assert verify.check_open_reqs("https://boards.greenhouse.io/gone")[0] is False


def test_open_reqs_supply_the_careers_url_for_the_evidence_poller(mocker):
    mocker.patch.object(verify.adapters, "fetch_open_roles",
                        return_value=verify.adapters.BoardResult(
                            ok=True, roles=[{"id": 1}], provider="greenhouse"))
    result = verify.verify({
        "company_url": "https://acme.example",
        "careers_url": "https://boards.greenhouse.io/acme",
    }, fetch=True)
    assert verify.OPEN_REQ in result.kinds
    assert result.careers_url == "https://boards.greenhouse.io/acme"


def test_offline_verification_makes_no_network_call(mocker):
    opened = mocker.patch.object(verify.urllib.request, "urlopen")
    board = mocker.patch.object(verify.adapters, "fetch_open_roles")
    result = verify.verify({
        "company_url": "https://acme.example",
        "company_linkedin_url": "https://www.linkedin.com/company/acme",
        "careers_url": "https://boards.greenhouse.io/acme",
    }, fetch=False)
    opened.assert_not_called()
    board.assert_not_called()
    assert result.kinds == [verify.LINKEDIN_URL_PRESENT]


def test_a_supplied_citation_counts_as_third_party_evidence():
    result = verify.verify({"third_party_citation": "Inc. 5000 2025, #412"},
                           fetch=False)
    assert verify.THIRD_PARTY_DATED in result.kinds


def test_two_kinds_are_needed_to_pass():
    thin = verify.verify({"company_linkedin_url":
                          "https://www.linkedin.com/company/acme"}, fetch=False)
    assert thin.passes(2) is False
    thick = verify.verify({
        "company_linkedin_url": "https://www.linkedin.com/company/acme",
        "third_party_citation": "Training Industry Top 20, 2025",
    }, fetch=False)
    assert thick.passes(2) is True


# --- the seed channel ---------------------------------------------------------


def _seeds(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "seeds.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


def test_a_missing_seed_file_is_normal_not_an_error(tmp_path):
    assert seed_list.load_seeds(tmp_path / "absent.yaml") == {}


def test_a_seed_without_a_url_is_skipped(tmp_path):
    path = _seeds(tmp_path, {"msp_it_consultancy": [{"name": "No URL Co"}]})
    assert seed_list.find("msp_it_consultancy", path) == []


def test_a_seed_becomes_a_candidate_with_provenance(tmp_path):
    path = _seeds(tmp_path, {"engineering_consultancy": [
        {"name": "Acme Engineering", "url": "https://acme.example",
         "citation": "Inc. 5000 2025", "headcount": "30-50"},
    ]})
    found = seed_list.find("engineering_consultancy", path)
    assert len(found) == 1
    candidate = found[0]
    assert candidate["discovered_via"] == "seed_list"
    assert candidate["discovery_query"].endswith("engineering_consultancy")
    assert candidate["segment"] == "engineering_consultancy"
    assert candidate["third_party_citation"] == "Inc. 5000 2025"


def test_seeds_default_to_the_us(tmp_path):
    path = _seeds(tmp_path, {"product_design_agency": [
        {"name": "Studio", "url": "https://studio.example"}]})
    assert seed_list.find("product_design_agency", path)[0]["country"] == "US"


def test_the_shipped_seed_file_parses_and_covers_every_segment():
    """It ships empty, but a malformed control-plane file would break the loop."""
    from agents.outreach import icp
    seeds = seed_list.load_seeds()
    assert set(seeds) == set(icp.ALL_SEGMENTS)
    assert all(entries == [] for entries in seeds.values())


# --- the run ------------------------------------------------------------------


def test_a_failing_channel_does_not_stop_the_others(mocker):
    from agents.outreach import discovery
    mocker.patch.dict(discovery.CHANNELS, {
        "broken": mocker.Mock(side_effect=RuntimeError("down")),
        "working": mocker.Mock(return_value=[
            {"company_url": "https://ok.example", "company_name": "OK"}]),
    }, clear=True)
    assert len(discovery.find_all("msp_it_consultancy")) == 1


def test_channels_dedup_on_company_url(mocker):
    from agents.outreach import discovery
    same = {"company_url": "https://dup.example/", "company_name": "Dup"}
    mocker.patch.dict(discovery.CHANNELS, {
        "a": mocker.Mock(return_value=[same]),
        "b": mocker.Mock(return_value=[dict(same, company_url="https://dup.example")]),
    }, clear=True)
    assert len(discovery.find_all("msp_it_consultancy")) == 1


def test_build_row_stamps_the_score_and_its_model_version():
    result = verify.Verification(kinds=["live_site", "open_req"],
                                 notes=["ok"], careers_url="https://c.example")
    row = discover.build_row({
        "company_name": "Acme", "company_url": "https://acme.example",
        "segment": "engineering_consultancy", "headcount_band": "30-50",
        "discovered_via": "seed_list",
    }, result)
    assert row["icp_model_version"] == "v1"
    assert row["icp_fit_score"] > 0
    assert row["verified_on"] == ["live_site", "open_req"]
    assert row["careers_url"] == "https://c.example"


def test_a_thin_firm_is_recorded_rather_than_skipped(mocker):
    """R0.19: recording it stops the next run re-probing forever, and leaves it
    eligible to surface later if evidence improves."""
    from agents.outreach import discovery
    mocker.patch.dict(discovery.CHANNELS, {"seed": mocker.Mock(return_value=[{
        "company_name": "Thin Co", "company_url": "https://thin.example",
        "segment": "msp_it_consultancy", "country": "US",
        "discovered_via": "seed_list",
    }])}, clear=True)
    mocker.patch.object(discover.verify, "verify",
                        return_value=verify.Verification(kinds=["live_site"],
                                                         notes=["only one"]))
    inserted = mocker.patch.object(discover.outreach_discovery,
                                   "insert_discovery", return_value=1)
    conn = mocker.MagicMock()
    cm = mocker.MagicMock()
    cm.__enter__.return_value = conn
    mocker.patch.object(discover.db, "connection", return_value=cm)
    mocker.patch.object(discover.outreach_discovery, "known_domains",
                        return_value=set())

    counts = discover.run(fetch=False)
    # The mock returns the same firm for every segment, so this also pins the
    # cross-segment dedup: verified and inserted once, skipped as a duplicate in
    # the five later segments rather than re-probed.
    assert counts["thin"] == 1
    assert counts["inserted"] == 1
    assert counts["duplicate"] == 5
    assert inserted.call_count == 1


def test_out_of_scope_countries_are_counted_not_inserted(mocker):
    from agents.outreach import discovery
    mocker.patch.dict(discovery.CHANNELS, {"seed": mocker.Mock(return_value=[{
        "company_name": "Overseas", "company_url": "https://abroad.example",
        "segment": "coaching_leadership", "country": "UK",
        "discovered_via": "seed_list",
    }])}, clear=True)
    inserted = mocker.patch.object(discover.outreach_discovery,
                                   "insert_discovery", return_value=1)
    conn = mocker.MagicMock()
    cm = mocker.MagicMock()
    cm.__enter__.return_value = conn
    mocker.patch.object(discover.db, "connection", return_value=cm)
    mocker.patch.object(discover.outreach_discovery, "known_domains",
                        return_value=set())

    counts = discover.run(fetch=False)
    assert counts["out_of_scope"] == 6 and counts["inserted"] == 0
    inserted.assert_not_called()


def test_every_segment_in_the_pinned_vocabulary_is_swept():
    from agents.outreach import icp
    assert set(discover.SEGMENTS) == set(icp.ALL_SEGMENTS)
    assert len(discover.SEGMENTS) == 6
