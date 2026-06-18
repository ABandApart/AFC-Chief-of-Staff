"""Unit tests for the recall CLI's pure helpers.

`format_results`, `format_result_line`, and `_vector_literal` are pure (no
I/O). The live hybrid-search round-trip (capture in 3.2 → recall) is exercised
by the barry-agent smoke test.
"""

from __future__ import annotations

from cli.recall import _vector_literal, format_result_line, format_results


def test_vector_literal_format():
    assert _vector_literal([0.1, 0.2, -0.3]) == "[0.1,0.2,-0.3]"


def test_format_result_line_with_domain():
    line = format_result_line("Q3 theme is AI for SMB.", "decision", 0.7123)
    assert line == "  [0.71] (decision) Q3 theme is AI for SMB."


def test_format_result_line_without_domain():
    line = format_result_line("Some fact.", None, 0.5)
    assert line == "  [0.50] Some fact."


def test_format_results_empty():
    out = format_results("newsletter plan", [])
    assert out == 'No matching facts for: "newsletter plan"'


def test_format_results_single():
    rows = [{"content": "A.", "domain": "decision", "score": 0.81}]
    out = format_results("q", rows)
    assert "1 fact for:" in out
    assert "[0.81] (decision) A." in out


def test_format_results_multiple_ordered():
    rows = [
        {"content": "Top.", "domain": "x", "score": 0.9},
        {"content": "Next.", "domain": None, "score": 0.4},
    ]
    out = format_results("q", rows)
    assert "2 facts for:" in out
    lines = out.splitlines()
    # header + 2 result lines, in given order
    assert lines[1] == "  [0.90] (x) Top."
    assert lines[2] == "  [0.40] Next."


def test_score_formatting_two_decimals():
    rows = [{"content": "X.", "domain": None, "score": 0.123456}]
    out = format_results("q", rows)
    assert "[0.12]" in out
