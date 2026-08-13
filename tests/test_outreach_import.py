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
    "trigger_kind": "req_open_45d",
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


@pytest.mark.parametrize("missing", list(ROW))
def test_parse_row_requires_every_required_column(missing):
    row = {**ROW, missing: ""}
    with pytest.raises(ValueError, match="missing required"):
        outreach_import.parse_row(row, line=7)


def test_parse_row_error_names_the_line():
    with pytest.raises(ValueError, match="line 42"):
        outreach_import.parse_row({**ROW, "company_name": ""}, line=42)


def test_parse_row_rejects_an_unknown_stage():
    # A typo'd stage silently breaks S2 scoring and the Selector grid lookup.
    with pytest.raises(ValueError, match="stage"):
        outreach_import.parse_row({**ROW, "stage": "series-a"}, line=2)


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
                  + "Acme,acme.com,seed,funding_round,2026-05-01\n"
                  + "Cadence,cadence.health,series_a,req_open_45d,2026-06-17\n")
    assert [t["company_name"] for t in outreach_import.read_targets(path)] == ["Acme", "Cadence"]


def test_read_targets_rejects_an_empty_file(tmp_path):
    with pytest.raises(ValueError, match="no data rows"):
        outreach_import.read_targets(_write(tmp_path, HEADER))


def test_read_targets_fails_the_whole_file_on_one_bad_row(tmp_path):
    # All-or-nothing: a half-applied import leaves the operator guessing which
    # rows landed.
    path = _write(tmp_path, HEADER
                  + "Acme,acme.com,seed,funding_round,2026-05-01\n"
                  + "Bad,bad.com,NOT_A_STAGE,funding_round,2026-05-01\n")
    with pytest.raises(ValueError, match="line 3"):
        outreach_import.read_targets(path)
