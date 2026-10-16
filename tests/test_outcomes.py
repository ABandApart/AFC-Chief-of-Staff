"""Unit tests for the /outcome helpers (pure, no Discord / DB).

The full slash-command → modal → write flow is validated by the barry-agent
smoke test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.discord_bot.cogs.outcomes import (
    CONTRACT_TYPES,
    MAX_CHOICES,
    OUTCOME_TYPES,
    CompanyRef,
    company_choices,
    parse_company,
    parse_value,
    requires_company,
)

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


# --- types ----------------------------------------------------------------


def test_outcome_types_present_and_bounded():
    # Discord allows max 25 choices; we have a known small set.
    assert 0 < len(OUTCOME_TYPES) <= 25
    assert "engagement_signed" in OUTCOME_TYPES


# --- contract types (D3) ----------------------------------------------------


def test_contract_types_require_company():
    for t in ("engagement_signed", "engagement_renewed", "maintenance_converted"):
        assert requires_company(t)


def test_other_types_do_not_require_company():
    for t in set(OUTCOME_TYPES) - CONTRACT_TYPES:
        assert not requires_company(t)


def test_contract_types_match_migration_0031():
    # The DB CHECK and the handler must agree, or one refuses what the other allows.
    sql = (Path(__file__).parent.parent / "migrations"
           / "0031_outcome_client_required.sql").read_text()
    for t in CONTRACT_TYPES:
        assert f"'{t}'" in sql


# --- parse_company ------------------------------------------------------------


def test_parse_client():
    assert parse_company("client:7") == CompanyRef("client", "7")


def test_parse_target_normalizes_domain():
    assert parse_company("target:Acme.com") == CompanyRef("target", "acme.com")


def test_parse_new_name_has_no_domain():
    assert parse_company("new:Acme Co") == CompanyRef("new", "Acme Co", domain=None)


def test_parse_new_domain_is_normalized():
    ref = parse_company("new:https://www.Acme.com/")
    assert ref.kind == "new"
    assert ref.domain == "acme.com"


@pytest.mark.parametrize("value", ["", "Acme", "client:", "client:abc", "other:x"])
def test_parse_unpicked_text_refused(value):
    with pytest.raises(ValueError):
        parse_company(value)


# --- company_choices ------------------------------------------------------------


def _client(i, name, domain=None):
    return {"id": i, "name": name, "domain": domain}


def _target(name, domain):
    return {"company_name": name, "company_domain": domain}


def test_choices_clients_before_targets():
    choices = company_choices(
        "ac", [_client(1, "Acme", "acme.com")], [_target("Acorn", "acorn.io")]
    )
    assert [v for _, v in choices][:2] == ["client:1", "target:acorn.io"]


def test_choices_drop_targets_already_clients():
    choices = company_choices(
        "acme", [_client(1, "Acme", "acme.com")], [_target("Acme", "acme.com")]
    )
    assert [v for _, v in choices] == ["client:1"]


def test_choices_offer_new_only_without_exact_match():
    exact = company_choices("acme", [_client(1, "Acme", "acme.com")], [])
    assert not any(v.startswith("new:") for _, v in exact)
    partial = company_choices("acm", [_client(1, "Acme", "acme.com")], [])
    assert partial[-1] == ("➕ New client: acm", "new:acm")


def test_choices_no_new_for_empty_term():
    assert company_choices("", [], []) == []


def test_choices_capped_and_new_kept():
    clients = [_client(i, f"Firm {i}") for i in range(40)]
    choices = company_choices("firm", clients, [])
    assert len(choices) == MAX_CHOICES
    assert choices[-1][1] == "new:firm"


def test_choices_truncated_to_discord_limit():
    long = "x" * 150
    choices = company_choices(long, [], [_target(long, f"{long}.com")])
    assert all(len(n) <= 100 and len(v) <= 100 for n, v in choices)
