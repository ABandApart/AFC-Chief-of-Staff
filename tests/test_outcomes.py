"""Unit tests for the /outcome parse helpers (pure, no Discord / DB).

The full slash-command → modal → write flow is validated by the barry-agent
smoke test.
"""

from __future__ import annotations

import pytest

from agents.discord_bot.cogs.outcomes import OUTCOME_TYPES, parse_fact_id, parse_value


# --- parse_value ----------------------------------------------------------


def test_value_blank_is_none():
    assert parse_value("") is None
    assert parse_value("   ") is None


def test_value_plain_number():
    assert parse_value("5000") == 5000.0


def test_value_strips_dollar_and_commas():
    assert parse_value("$5,000") == 5000.0
    assert parse_value("12,500.50") == 12500.50


def test_value_bad_raises():
    with pytest.raises(ValueError):
        parse_value("lots")


# --- parse_fact_id --------------------------------------------------------


def test_fact_id_blank_is_none():
    assert parse_fact_id("") is None
    assert parse_fact_id("  ") is None


def test_fact_id_number():
    assert parse_fact_id("42") == 42


def test_fact_id_bad_raises():
    with pytest.raises(ValueError):
        parse_fact_id("abc")


# --- types ----------------------------------------------------------------


def test_outcome_types_present_and_bounded():
    # Discord allows max 25 choices; we have a known small set.
    assert 0 < len(OUTCOME_TYPES) <= 25
    assert "engagement_signed" in OUTCOME_TYPES
