"""Unit tests for the docent export (no network, no DB)."""

from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.docent import export as ex

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 12, 7, 30, tzinfo=UTC)
SEG = ("Coaching and leadership", 4, 3, 3, 4, 5, 4, None)


class FakeCursor:
    def __init__(self, cols=(), rows=(), log=None):
        self.description = [SimpleNamespace(name=c) for c in cols]
        self._rows = list(rows)
        self.log = log if log is not None else []

    def fetchall(self):
        return self._rows

    def execute(self, sql, params=None):
        self.log.append((sql, params))

    def executemany(self, sql, seq):
        self.log.append((sql, list(seq)))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeSource:
    def __init__(self, results):
        self.results = results  # sql fragment -> (cols, rows)

    def execute(self, sql, params=None):
        for frag, (cols, rows) in self.results.items():
            if frag in sql:
                return FakeCursor(cols, rows)
        raise AssertionError(sql)


class FakeDocent:
    def __init__(self):
        self.log = []
        self.in_tx = False

    @contextmanager
    def transaction(self):
        self.in_tx = True
        yield

    def cursor(self):
        assert self.in_tx, "writes must run inside the transaction"
        return FakeCursor(log=self.log)


def _source(themes=(), segments=(SEG,), theme_cols=None):
    return FakeSource({
        "export.themes": (theme_cols or ex.COLUMNS["themes"], list(themes)),
        "export.market_segments": (ex.COLUMNS["market_segments"], list(segments)),
    })


def _deny(*names):
    return FakeSource({"FROM people": (("name",), [(n,) for n in names])})


# --- the allowlist is pinned ---------------------------------------------------------


def test_columns_are_pinned():
    # Changing what docent receives must be a reviewed edit to this test.
    assert ex.COLUMNS == {
        "themes": ("topic_label", "weight", "last_reinforced_at"),
        "market_segments": ("label", "market_size", "market_growth", "firm_profitability",
                            "ability_to_pay", "urgency_pain", "offering_fit", "rated_at"),
    }


def test_column_drift_fails_the_run():
    src = _source(theme_cols=("topic_label", "weight", "last_reinforced_at", "origin"))
    with pytest.raises(ex.ExportError, match="do not match"):
        ex.read_rows(src)


def test_docent_tables_match_columns():
    sql = (ROOT / "migrations/docent/0001_richmond_schema.sql").read_text()
    for table, cols in ex.COLUMNS.items():
        body = re.search(rf"CREATE TABLE IF NOT EXISTS richmond\.{table} \((.*?)\n\);", sql, re.S)
        assert body, table
        declared = tuple(line.split()[0] for line in body.group(1).strip().splitlines())
        assert declared == (*cols, "exported_at")


# --- PII gate -------------------------------------------------------------------------


@pytest.mark.parametrize("value, kind", [
    ("Write to jane@acme.co about it", "email"),
    ("Call +1 (312) 555-0199", "phone"),
    ("See https://example.org/x", "url"),
    ("Founders at acme.com", "url"),
    ("Coaching with Jane Doe", "known name"),
])
def test_pii_gate_flags(value, kind):
    findings = ex.find_pii({"themes": [(value, 1.0, None)], "market_segments": []},
                           {"jane doe"})
    assert len(findings) == 1 and kind in findings[0]
    assert value not in findings[0]  # findings never repeat the value


def test_pii_gate_matches_whole_words_only():
    rows = {"themes": [("Annual planning", 1.0, None)], "market_segments": [SEG]}
    assert ex.find_pii(rows, {"ann"}) == []


def test_deny_terms_skips_blank_and_short():
    assert ex.deny_terms(_deny("Jane Doe", None, "  ", "Al")) == {"jane doe"}


# --- run ------------------------------------------------------------------------------


def test_run_pushes_in_one_transaction():
    docent = FakeDocent()

    @contextmanager
    def connect():
        yield docent

    out = ex.run_once(source_ro=_source(themes=[("AI agents", 0.8, NOW)]),
                      source_rw=_deny("Jane Doe"), docent=connect, now=NOW)
    assert out == "pushed themes 1, market_segments 1"
    sqls = [s for s, _ in docent.log]
    assert sqls[0] == "DELETE FROM richmond.themes"
    assert sqls[2] == "DELETE FROM richmond.market_segments"
    assert "INSERT INTO richmond.export_runs" in sqls[-1]
    assert docent.log[1][1] == [("AI agents", 0.8, NOW, NOW)]


def test_run_still_clears_docent_when_source_is_empty():
    docent = FakeDocent()

    @contextmanager
    def connect():
        yield docent

    ex.run_once(source_ro=_source(themes=[], segments=[]), source_rw=_deny(),
                docent=connect, now=NOW)
    assert [s for s, _ in docent.log][:2] == ["DELETE FROM richmond.themes",
                                              "DELETE FROM richmond.market_segments"]


def test_pii_hit_writes_nothing():
    def connect():
        raise AssertionError("must not connect to docent")

    with pytest.raises(ex.ExportError, match="nothing written"):
        ex.run_once(source_ro=_source(themes=[("Jane Doe's pipeline", 1.0, None)]),
                    source_rw=_deny("Jane Doe"), docent=connect, now=NOW)


def test_dry_run_reports_without_docent():
    out = ex.run_once(source_ro=_source(), source_rw=_deny(), docent=None, now=NOW)
    assert out == "dry run: themes 0, market_segments 1; PII check: clean"


# --- docent grants ---------------------------------------------------------------------


def test_docent_readers_never_get_write_grants():
    sql = (ROOT / "migrations/docent/0001_richmond_schema.sql").read_text()
    for grant in re.findall(r"^GRANT (.+?) TO (.+?);", sql, re.M | re.S):
        privileges, grantees = grant
        if re.search(r"\b(anon|authenticated)\b", grantees):
            assert re.fullmatch(r"(SELECT|USAGE) ON .*", privileges, re.S), grant
