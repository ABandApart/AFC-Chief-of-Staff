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
