"""Unit tests for the reconcile backstop (W1.3). Pure — no DB."""

from __future__ import annotations

import pytest

from cli.reconcile import ReconcileLine, format_report, overall_ok, reconcile


def _ledger():
    return {
        "anthropic": {"runs": 40, "usd": 4.00},
        "gemini": {"runs": 37, "usd": 0.007},
    }


def test_reconcile_lines_cover_ledger_and_actuals():
    lines = reconcile(_ledger(), {"anthropic": 4.20, "gemini": None}, 0.15)
    by = {lp.provider: lp for lp in lines}
    assert by["anthropic"].ledger_usd == 4.00
    assert by["anthropic"].actual_usd == 4.20
    assert by["gemini"].actual_usd is None  # no actual → ledger-only line


def test_within_tolerance_true_and_false():
    # 4.00 ledger vs 4.20 actual = 5% delta, within 15%
    good = ReconcileLine("anthropic", 4.00, 4.20, 40)
    assert good.within(0.15) is True
    # 4.00 vs 6.00 = 33% delta, over 15%
    bad = ReconcileLine("anthropic", 4.00, 6.00, 40)
    assert bad.within(0.15) is False
    # no actual → None
    assert ReconcileLine("anthropic", 4.00, None, 40).within(0.15) is None


def test_absolute_floor_forgives_tiny_deltas():
    # 0.007 vs 0.010 is 43% but only $0.003 absolute → within (noise)
    line = ReconcileLine("gemini", 0.007, 0.010, 37)
    assert line.within(0.15) is True


def test_overall_ok_flags_only_supplied_divergence():
    lines = [
        ReconcileLine("anthropic", 4.00, 6.00, 40),  # over
        ReconcileLine("gemini", 0.007, None, 37),     # no actual
    ]
    assert overall_ok(lines, 0.15) is False
    ok_lines = [
        ReconcileLine("anthropic", 4.00, 4.20, 40),   # within
        ReconcileLine("gemini", 0.007, None, 37),     # no actual
    ]
    assert overall_ok(ok_lines, 0.15) is True


def test_delta_sign():
    assert ReconcileLine("anthropic", 4.00, 4.20, 40).delta == pytest.approx(0.20)
    assert ReconcileLine("anthropic", 4.20, 4.00, 40).delta == pytest.approx(-0.20)
    assert ReconcileLine("anthropic", 4.00, None, 40).delta is None


def test_format_report_ledger_only_prints_dashboards():
    lines = reconcile(_ledger(), {"anthropic": None, "gemini": None}, 0.15)
    report = format_report("month to date", lines, 0.15)
    assert "ledger side only" in report
    assert "console.anthropic.com" in report
    assert "4.00" in report  # ledger total shown


def test_format_report_flags_over_tolerance():
    lines = reconcile(_ledger(), {"anthropic": 6.00, "gemini": None}, 0.15)
    report = format_report("month to date", lines, 0.15)
    assert "OVER TOLERANCE" in report
