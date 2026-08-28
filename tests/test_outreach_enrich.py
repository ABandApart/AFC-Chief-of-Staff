"""Tests for the Apollo V2 probe CLI (`cli.outreach_enrich`).

The pure mapping/coverage live in `test_outreach_apollo`. Here we drive the CLI's
two output branches with creds, DB, and the Apollo call all mocked, so the object
the operator actually runs is exercised — not just its helpers.
"""

from __future__ import annotations

import json

from cli import outreach_enrich

_TARGETS = [
    {"id": 18, "company_name": "AIIR Consulting", "company_domain": "aiir.co"},
    {"id": 22, "company_name": "LifeLabs Learning", "company_domain": "lifelabslearning.com"},
]
_ORG = {"name": "X", "industry": "coaching", "estimated_num_employees": 40,
        "funding_events": [{"investors": "Foo Capital, Bar Fund"}]}


def _patch(mocker, org):
    mocker.patch.object(outreach_enrich.creds, "keychain_get", return_value="k")
    mocker.patch.object(outreach_enrich.db, "connection")
    mocker.patch.object(outreach_enrich.db, "close_pool")
    mocker.patch.object(outreach_enrich, "_select_targets", return_value=_TARGETS)
    mocker.patch.object(outreach_enrich.apollo, "enrich_organization", return_value=org)


def test_raw_mode_emits_only_json_with_the_full_payload(mocker, capsys):
    _patch(mocker, _ORG)
    rc = outreach_enrich.run_probe(ids=None, limit=5, raw=True)
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)  # the whole output must be valid JSON, nothing else
    assert [r["id"] for r in parsed] == [18, 22]
    assert parsed[0]["organization"]["industry"] == "coaching"
    # lead investor is inside funding_events -> the adapter can see it in the dump
    assert parsed[1]["organization"]["funding_events"][0]["investors"] == "Foo Capital, Bar Fund"


def test_probe_table_mode_reports_coverage(mocker, capsys):
    _patch(mocker, _ORG)
    rc = outreach_enrich.run_probe(ids=None, limit=5, raw=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Field coverage across 2 target(s)" in out
    assert "sector" in out and "2/2" in out  # both orgs carry industry


def test_missing_key_exits_2_not_a_traceback(mocker, capsys):
    mocker.patch.object(
        outreach_enrich.creds, "keychain_get",
        side_effect=RuntimeError("Keychain item 'apollo-api-key' not found."),
    )
    rc = outreach_enrich.run_probe(ids=None, limit=5)
    assert rc == 2
    assert "apollo-api-key" in capsys.readouterr().err


_CONTACTS = [
    {"id": 18, "company_name": "AIIR Consulting", "company_domain": "aiir.co",
     "contact_name": "Jane Doe"},
]
_PERSON = {"title": "VP People", "email": "jane@aiir.co", "email_status": "verified",
           "linkedin_url": "https://linkedin.com/in/janedoe"}


def test_people_probe_reports_coverage_without_printing_raw_values(mocker, capsys):
    mocker.patch.object(outreach_enrich.creds, "keychain_get", return_value="k")
    mocker.patch.object(outreach_enrich.db, "connection")
    mocker.patch.object(outreach_enrich, "_select", return_value=_CONTACTS)
    mocker.patch.object(outreach_enrich.apollo, "match_person", return_value=_PERSON)

    rc = outreach_enrich.run_people_probe(ids=None, limit=5)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Contact coverage across 1 contact(s)" in out
    assert "title present     1/1" in out
    assert "revealed 1" in out
    # Privacy: the raw email address and LinkedIn URL must NOT appear in output.
    assert "jane@aiir.co" not in out
    assert "linkedin.com/in/janedoe" not in out


def test_apply_stops_cleanly_on_credit_exhaustion_with_resumable_summary(mocker, capsys):
    mocker.patch.object(outreach_enrich.creds, "keychain_get", return_value="k")
    conn = mocker.MagicMock()
    mocker.patch.object(outreach_enrich.db, "connection")
    outreach_enrich.db.connection.return_value.__enter__.return_value = conn
    mocker.patch.object(outreach_enrich, "_select", return_value=[
        {"id": 1, "company_name": "First", "company_domain": "a.com", "contact_name": None},
        {"id": 2, "company_name": "Second", "company_domain": "b.com", "contact_name": None},
        {"id": 3, "company_name": "Third", "company_domain": "c.com", "contact_name": None},
    ])
    # First firm writes; second hits the credit wall.
    mocker.patch.object(
        outreach_enrich.enrich, "enrich_target",
        side_effect=[{"target_id": 1, "org": True, "firmographic_fields": 4,
                      "funding_new": 0, "contacts": "skipped"},
                     outreach_enrich.apollo.ApolloCreditsError(
                         outreach_enrich.apollo.APOLLO_ENRICH_URL)],
    )
    rc = outreach_enrich.run_apply(ids=None, limit=None, with_contacts=False)
    assert rc == 4
    err = capsys.readouterr().err
    assert "1 written, 2 remaining" in err
    assert "Second" in err and "Third" in err   # the resumable list
    assert "idempotent" in err


def test_people_probe_reports_plan_gate_as_exit_3_not_a_traceback(mocker, capsys):
    mocker.patch.object(outreach_enrich.creds, "keychain_get", return_value="k")
    mocker.patch.object(outreach_enrich.db, "connection")
    mocker.patch.object(outreach_enrich, "_select", return_value=_CONTACTS)
    mocker.patch.object(
        outreach_enrich.apollo, "match_person",
        side_effect=outreach_enrich.apollo.ApolloPlanError(
            outreach_enrich.apollo.APOLLO_MATCH_URL),
    )
    rc = outreach_enrich.run_people_probe(ids=None, limit=5)
    assert rc == 3
    err = capsys.readouterr().err
    assert "paid" in err.lower()
