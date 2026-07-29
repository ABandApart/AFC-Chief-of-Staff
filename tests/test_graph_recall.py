"""Unit tests for graph recall's pure output normalizer (W5).

`answer_text` maps cognee.search's output (str / list / None / other) to the
display string; the search call itself is exercised by runtime validation.
"""

from __future__ import annotations

from agents._lib.graph_recall import NO_RESULT, answer_text


def test_none_is_no_result():
    assert answer_text(None) == NO_RESULT


def test_empty_string_and_empty_list_are_no_result():
    assert answer_text("") == NO_RESULT
    assert answer_text("   ") == NO_RESULT
    assert answer_text([]) == NO_RESULT
    assert answer_text(["", "  "]) == NO_RESULT


def test_string_answer_stripped():
    assert answer_text("  the newsletter theme is AI for SMB.  ") == \
        "the newsletter theme is AI for SMB."


def test_list_joined_with_blank_lines():
    assert answer_text(["first.", "second."]) == "first.\n\nsecond."


def test_list_skips_blank_items():
    assert answer_text(["kept.", "", "  ", "also kept."]) == "kept.\n\nalso kept."


def test_other_type_coerced_to_str():
    assert answer_text(42) == "42"
