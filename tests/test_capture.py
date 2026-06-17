"""Unit tests for the capture cog's pure helpers.

`parse_facts` and `format_summary` are pure (no I/O), so these run with no
mocks and no network. The full capture flow (Discord events, LLM calls, DB
writes) is exercised by the live smoke test in the barry-agent session.
"""

from __future__ import annotations

import pytest

from agents.discord_bot.cogs.capture import format_summary, parse_facts


# --- parse_facts: happy paths --------------------------------------------


def test_parse_well_formed_json():
    raw = '{"facts": [{"content": "Barry prefers async standups.", "domain": "preference", "confidence": 0.9}]}'
    facts = parse_facts(raw)
    assert len(facts) == 1
    assert facts[0]["content"] == "Barry prefers async standups."
    assert facts[0]["domain"] == "preference"
    assert facts[0]["confidence"] == 0.9


def test_parse_strips_markdown_fence():
    raw = '```json\n{"facts": [{"content": "Q3 newsletter theme is AI for SMB.", "domain": "decision", "confidence": 1.0}]}\n```'
    facts = parse_facts(raw)
    assert len(facts) == 1
    assert facts[0]["domain"] == "decision"


def test_parse_strips_bare_fence():
    raw = '```\n{"facts": []}\n```'
    assert parse_facts(raw) == []


def test_parse_empty_facts_list():
    assert parse_facts('{"facts": []}') == []


def test_parse_multiple_facts():
    raw = '{"facts": [{"content": "A", "domain": "x", "confidence": 1.0}, {"content": "B", "domain": "y", "confidence": 0.5}]}'
    facts = parse_facts(raw)
    assert [f["content"] for f in facts] == ["A", "B"]


# --- parse_facts: validation / normalization ------------------------------


def test_confidence_clamped_to_unit_interval():
    raw = '{"facts": [{"content": "X", "domain": "d", "confidence": 5.0}, {"content": "Y", "domain": "d", "confidence": -1}]}'
    facts = parse_facts(raw)
    assert facts[0]["confidence"] == 1.0
    assert facts[1]["confidence"] == 0.0


def test_confidence_defaults_to_one_when_missing_or_bad():
    raw = '{"facts": [{"content": "X", "domain": "d"}, {"content": "Y", "domain": "d", "confidence": "high"}]}'
    facts = parse_facts(raw)
    assert facts[0]["confidence"] == 1.0
    assert facts[1]["confidence"] == 1.0


def test_blank_content_items_skipped():
    raw = '{"facts": [{"content": "   ", "domain": "d", "confidence": 1.0}, {"content": "real", "domain": "d", "confidence": 1.0}]}'
    facts = parse_facts(raw)
    assert len(facts) == 1
    assert facts[0]["content"] == "real"


def test_missing_domain_becomes_none():
    raw = '{"facts": [{"content": "X", "confidence": 1.0}]}'
    facts = parse_facts(raw)
    assert facts[0]["domain"] is None


def test_non_dict_items_skipped():
    raw = '{"facts": ["just a string", {"content": "real", "domain": "d", "confidence": 1.0}]}'
    facts = parse_facts(raw)
    assert len(facts) == 1


# --- parse_facts: error paths (graceful-degrade signals) ------------------


def test_malformed_json_raises_value_error():
    with pytest.raises(ValueError):
        parse_facts("this is not json at all")


def test_missing_facts_key_raises_value_error():
    with pytest.raises(ValueError):
        parse_facts('{"items": []}')


def test_facts_not_a_list_raises_value_error():
    with pytest.raises(ValueError):
        parse_facts('{"facts": "nope"}')


# --- format_summary -------------------------------------------------------


def test_summary_single_fact():
    facts = [{"content": "Barry likes tea.", "domain": "personal", "confidence": 1.0}]
    s = format_summary(facts)
    assert "Captured 1 fact" in s
    assert "personal" in s
    assert "Barry likes tea." in s


def test_summary_multiple_facts_lists_domains():
    facts = [
        {"content": "A", "domain": "business", "confidence": 1.0},
        {"content": "B", "domain": "project", "confidence": 1.0},
    ]
    s = format_summary(facts)
    assert "Captured 2 facts" in s
    assert "business" in s and "project" in s
    assert "• A" in s and "• B" in s


def test_summary_truncates_beyond_five():
    facts = [{"content": f"F{i}", "domain": None, "confidence": 1.0} for i in range(7)]
    s = format_summary(facts)
    assert "Captured 7 facts" in s
    assert "…and 2 more" in s
