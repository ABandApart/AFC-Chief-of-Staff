"""Unit tests for the CSV target import (Track O, `cli/outreach_import.py`).

Row validation is pure. The import is all-or-nothing by construction: the whole
file is parsed before anything is written, so a bad row on line 40 does not leave
39 rows half-applied for the operator to reconcile by hand.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from cli import outreach_import

ROW = {
    "company_name": "Cadence Health",
    "company_domain": "cadence.health",
    "stage": "series_a",
    "trigger_kind": "request_open_past_45_days",
    "trigger_date": "2026-06-17",
}


def test_parse_row_minimal_required_columns():
    out = outreach_import.parse_row(dict(ROW), line=2)
    assert out["company_name"] == "Cadence Health"
    assert out["trigger_date"] == date(2026, 6, 17)
    assert "careers_url" not in out  # absent, not empty-string


def test_parse_row_keeps_optional_columns_when_present():
    row = {**ROW, "careers_url": "https://jobs.lever.co/cadence", "sector": "health"}
    out = outreach_import.parse_row(row, line=2)
    assert out["careers_url"] == "https://jobs.lever.co/cadence"
    assert out["sector"] == "health"


def test_parse_row_ignores_blank_optionals_and_trims():
    row = {**ROW, "sector": "  ", "company_name": "  Cadence Health  "}
    out = outreach_import.parse_row(row, line=2)
    assert "sector" not in out
    assert out["company_name"] == "Cadence Health"


@pytest.mark.parametrize("missing", list(outreach_import.REQUIRED))
def test_parse_row_requires_every_required_column(missing):
    row = {**ROW, missing: ""}
    with pytest.raises(ValueError, match="missing required"):
        outreach_import.parse_row(row, line=7)


def test_a_blank_stage_imports_as_null():
    # Migration 0014 made stage nullable precisely so an unknown stage can stay
    # unknown. `stage` was left in REQUIRED by mistake, which made a blank
    # un-importable and forced a fabricated value — the exact failure 0014
    # exists to prevent, reintroduced one layer up (barry-agent, 2026-08-14).
    out = outreach_import.parse_row({**ROW, "stage": ""}, line=2)
    assert out["stage"] is None


def test_a_missing_stage_column_entirely_is_also_fine():
    row = {k: v for k, v in ROW.items() if k != "stage"}
    assert outreach_import.parse_row(row, line=2)["stage"] is None


def test_stage_is_not_required():
    assert "stage" not in outreach_import.REQUIRED
    assert "stage" in outreach_import.OPTIONAL


def test_a_present_but_invalid_stage_is_still_rejected():
    # Optional does not mean unvalidated: a typo'd stage silently breaks S2.
    with pytest.raises(ValueError, match="stage"):
        outreach_import.parse_row({**ROW, "stage": "Mature"}, line=2)


def test_parse_row_error_names_the_line():
    with pytest.raises(ValueError, match="line 42"):
        outreach_import.parse_row({**ROW, "company_name": ""}, line=42)


def test_parse_row_rejects_an_unknown_stage():
    # A typo'd stage silently breaks S2 scoring and the Selector grid lookup.
    with pytest.raises(ValueError, match="stage"):
        outreach_import.parse_row({**ROW, "stage": "series-a"}, line=2)


@pytest.mark.parametrize("stage", ["seed", "series_a", "series_b_plus", "mature"])
def test_parse_row_accepts_every_stage_including_mature(stage):
    # `mature` exists because the real target list is established non-venture
    # firms; without it the importer's only options were fabricate or refuse.
    assert outreach_import.parse_row({**ROW, "stage": stage}, line=2)["stage"] == stage


@pytest.mark.parametrize("trigger", list(outreach_import.VALID_TRIGGER_KINDS))
def test_parse_row_accepts_each_of_the_eight_triggers(trigger):
    out = outreach_import.parse_row({**ROW, "trigger_kind": trigger}, line=2)
    assert out["trigger_kind"] == trigger


def test_the_eight_triggers_are_eight():
    assert len(outreach_import.VALID_TRIGGER_KINDS) == 8


def test_parse_row_rejects_trigger_vocabulary_drift():
    # `req_open_45d` was a real example in migration 0013's comments; the seeded
    # data used `request_open_past_45_days`. Unconstrained, both would coexist.
    with pytest.raises(ValueError, match="trigger_kind"):
        outreach_import.parse_row({**ROW, "trigger_kind": "req_open_45d"}, line=2)


def test_import_cannot_mint_an_inbound_trigger():
    # inbound_enquiry is Roy Kent's hand-off. A CSV must not be able to create a
    # row that claims to be an inbound lead — 36- I1 hangs off that value.
    with pytest.raises(ValueError, match="trigger_kind"):
        outreach_import.parse_row({**ROW, "trigger_kind": "inbound_enquiry"}, line=2)


def test_parse_row_rejects_a_malformed_date():
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        outreach_import.parse_row({**ROW, "trigger_date": "17/06/2026"}, line=2)


def test_parse_row_rejects_a_future_trigger_date():
    # The arc anchors on trigger_date; a future one parks every touch window
    # ahead of itself and the target silently never surfaces.
    future = (date.today() + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="future"):
        outreach_import.parse_row({**ROW, "trigger_date": future}, line=2)


# --- read_targets -------------------------------------------------------------


def _write(tmp_path, text):
    path = tmp_path / "targets.csv"
    path.write_text(text, encoding="utf-8")
    return path


HEADER = "company_name,company_domain,stage,trigger_kind,trigger_date\n"


def test_read_targets_parses_all_rows(tmp_path):
    path = _write(tmp_path, HEADER
                  + "Acme,acme.com,seed,funding_announced,2026-05-01\n"
                  + "Cadence,cadence.health,series_a,request_open_past_45_days,2026-06-17\n")
    assert [t["company_name"] for t in outreach_import.read_targets(path)] == ["Acme", "Cadence"]


def test_read_targets_rejects_an_empty_file(tmp_path):
    with pytest.raises(ValueError, match="no data rows"):
        outreach_import.read_targets(_write(tmp_path, HEADER))


def test_read_targets_fails_the_whole_file_on_one_bad_row(tmp_path):
    # All-or-nothing: a half-applied import leaves the operator guessing which
    # rows landed.
    path = _write(tmp_path, HEADER
                  + "Acme,acme.com,seed,funding_announced,2026-05-01\n"
                  + "Bad,bad.com,NOT_A_STAGE,funding_announced,2026-05-01\n")
    with pytest.raises(ValueError, match="line 3"):
        outreach_import.read_targets(path)
