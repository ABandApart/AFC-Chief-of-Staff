"""Unit tests for the Apollo storing adapter (`agents/outreach/enrich.py`).

The firmographic write path is verified end to end on the real DB by barry-agent;
here the pure funding-fact mapping and the per-target orchestration are pinned,
with the DB write helpers and Apollo calls mocked. The load-bearing guarantees:

  * a funding round becomes evidence dated at the ROUND's date, not today (R1.4);
  * undated rounds are dropped, not stamped with today;
  * contacts are attempted only when asked, and a plan gate is caught, not raised —
    so the firmographic write that already happened stands (the onramp).
"""

from __future__ import annotations

from datetime import date

from agents.outreach import apollo, enrich

_ORG = {
    "industry": "coaching",
    "estimated_num_employees": 85,
    "founded_year": 2011,
    "total_funding": 13600000,
    "funding_events": [
        {"id": "fe_1", "date": "2023-04-01", "type": "Series A",
         "amount": "13.6M", "investors": "Foo Capital"},
        {"id": "fe_2", "date": None, "type": "Seed"},          # undated → dropped
    ],
}


# --- funding_facts (pure) -----------------------------------------------------


def test_funding_facts_dates_each_round_and_drops_undated():
    facts = enrich.funding_facts(_ORG)
    assert len(facts) == 1                      # the undated Seed is dropped
    f = facts[0]
    assert f["event_date"] == date(2023, 4, 1)  # the round's own date → first_seen_at
    assert f["fact_kind"] == enrich.FUNDING_FACT_KIND
    assert f["source_kind"] == "apollo"
    assert f["dedup_key"] == "apollo:fe_1"
    assert f["payload"] == {"round_type": "Series A", "amount": "13.6M",
                            "investors": "Foo Capital"}


def test_funding_facts_falls_back_to_date_type_key_without_an_id():
    facts = enrich.funding_facts(
        {"funding_events": [{"date": "2020-02-02", "type": "Seed"}]}
    )
    assert facts[0]["dedup_key"] == "apollo:2020-02-02:Seed"


def test_funding_facts_empty_when_no_events():
    assert enrich.funding_facts({}) == []


# --- enrich_target (orchestration, mocked writes) -----------------------------


def _patch_writes(mocker):
    mocker.patch.object(enrich.apollo, "enrich_organization", return_value=_ORG)
    fw = mocker.patch.object(enrich.outreach, "update_firmographics")
    ue = mocker.patch.object(enrich.outreach, "upsert_evidence", return_value=True)
    uc = mocker.patch.object(enrich.outreach, "update_contact")
    return fw, ue, uc


def test_enrich_writes_firmographics_and_funding_evidence(mocker):
    fw, ue, uc = _patch_writes(mocker)
    res = enrich.enrich_target(
        None, {"id": 5, "company_domain": "aiir.co", "contact_name": "Jane"},
        "k", today=date(2026, 8, 28),
    )
    # firmographics mapped from the org and written
    assert fw.call_args.args[1] == 5
    assert fw.call_args.args[2]["sector"] == "coaching"
    # one dated funding round became evidence, dated at the round (2023-04-01)
    assert ue.call_count == 1
    assert ue.call_args.args[1]["first_seen_at"] == date(2023, 4, 1)
    assert res["funding_new"] == 1
    # contacts NOT attempted without with_contacts
    uc.assert_not_called()
    assert res["contacts"] == "skipped"


def test_enrich_skips_a_target_apollo_cannot_find(mocker):
    mocker.patch.object(enrich.apollo, "enrich_organization", return_value=None)
    fw = mocker.patch.object(enrich.outreach, "update_firmographics")
    res = enrich.enrich_target(None, {"id": 9, "company_domain": "gone.example"}, "k")
    assert res == {"target_id": 9, "org": False}
    fw.assert_not_called()


def test_contact_onramp_catches_the_plan_gate_and_keeps_firmographics(mocker):
    fw, ue, uc = _patch_writes(mocker)
    mocker.patch.object(
        enrich.apollo, "match_person",
        side_effect=apollo.ApolloPlanError(apollo.APOLLO_MATCH_URL),
    )
    res = enrich.enrich_target(
        None, {"id": 5, "company_domain": "aiir.co", "contact_name": "Jane Doe"},
        "k", with_contacts=True, today=date(2026, 8, 28),
    )
    # The gate is swallowed: firmographics still written, contact write never called.
    fw.assert_called_once()
    uc.assert_not_called()
    assert res["contacts"] == "plan_gated"


def test_contact_onramp_writes_when_reachable(mocker):
    fw, ue, uc = _patch_writes(mocker)
    person = {"title": "VP People", "email": "jane@aiir.co", "email_status": "verified",
              "linkedin_url": "https://linkedin.com/in/jane"}
    mocker.patch.object(enrich.apollo, "match_person", return_value=person)
    res = enrich.enrich_target(
        None, {"id": 5, "company_domain": "aiir.co", "contact_name": "Jane Doe"},
        "k", with_contacts=True,
    )
    uc.assert_called_once()
    assert uc.call_args.args[2]["contact_title"] == "VP People"
    assert res["contacts"] == "written"
