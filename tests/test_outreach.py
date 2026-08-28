"""Unit tests for the outreach shared core (Track O, `_lib/outreach.py`).

Pure logic tested directly; DB writes tested with a mocked connection, asserting
on the SQL/params the callers depend on. The rules that must not regress:

  * **D1** — an import may refresh firmographics but never overwrites the
    operator's judgement columns (`s2`–`s5`, function_state, status,
    stalled_reason), and `trigger_date` only moves forward.
  * **Domain normalization** — the dedup key that stops one company becoming
    two rows against the capacity cap (R8).
  * **Free-mail rejection** — two gmail leads must not collide onto one target.
  * **Close-detection is scoped** and only ever runs on a confirmed poll.
"""

from __future__ import annotations

from datetime import date

import pytest

from agents._lib import outreach

# --- derive_function (pure, migration 0015) -----------------------------------


@pytest.mark.parametrize("title,expected", [
    ("VP Revenue", "revenue"),
    ("VP of Revenue", "revenue"),
    ("Vice President, Marketing", "marketing"),
    ("Head of Marketing", "marketing"),
    ("Director of Finance", "finance"),
    ("Senior Director, People Operations", "people operations"),
    ("Chief Revenue Officer", "revenue"),
    ("Global VP, Customer Success", "customer success"),
    ("Interim Head of Product", "product"),
])
def test_derive_function_strips_the_leadership_level(title, expected):
    assert outreach.derive_function(title) == expected


@pytest.mark.parametrize("acronym,expected", [
    ("CRO", "revenue"), ("CMO", "marketing"), ("CFO", "finance"),
    ("COO", "operations"), ("CTO", "technology"),
])
def test_derive_function_resolves_unambiguous_acronyms(acronym, expected):
    assert outreach.derive_function(acronym) == expected


def test_cpo_is_deliberately_not_guessed():
    # Chief Product Officer or Chief People Officer, depending on the company.
    # Guessing wrong puts the wrong noun in a sentence about what is unled.
    assert outreach.derive_function("CPO") is None


@pytest.mark.parametrize("title", [
    "Account Executive", "Sales Consultant", "Change Management Consultant",
    "Senior Marketing Manager", "QA Engineering Lead", "Client Success Associate",
    "General Application", None, "", "   ",
])
def test_derive_function_refuses_non_leadership_titles(title):
    # THE restriction that matters. T10's mechanic is that an *executive* search
    # runs 90-120 days; `35-` separates open_role (S4, leadership gap) from
    # ic_hire (S5, team-build-below). Deriving "revenue" from an open AE req
    # would claim the function is unled when they are hiring a rep.
    assert outreach.derive_function(title) is None


def test_derive_function_returns_none_when_nothing_survives_stripping():
    assert outreach.derive_function("Senior Vice President") is None
    assert outreach.derive_function("Chief of Staff") == "staff"


# --- suggest_first_name (pure, display only) ---------------------------------


@pytest.mark.parametrize("full,expected", [
    ("Jane Smith", "Jane"),
    ("Dr. Jane Smith", "Jane"),
    ("Prof Jane Smith", "Jane"),
    ("Jean-Pierre Dupont", "Jean-Pierre"),
    ("Jane van der Berg", "Jane"),
])
def test_suggest_first_name(full, expected):
    assert outreach.suggest_first_name(full) == expected


@pytest.mark.parametrize("full", ["J. Smith", "", None, "Dr."])
def test_suggest_first_name_declines_rather_than_guessing(full):
    # A bare initial or an honorific alone tells us nothing, and this feeds the
    # greeting — the first line a cold prospect reads. None is the right answer.
    assert outreach.suggest_first_name(full) is None


# --- backfill_function --------------------------------------------------------


def test_backfill_function_only_ever_fills_a_null(mocker):
    conn, cur = _conn(mocker)
    cur.rowcount = 1
    assert outreach.backfill_function(conn, 7, ["VP Revenue"]) == "revenue"
    sql, params = cur.execute.call_args.args
    # The guard is in the STATEMENT, not in Python — an operator's correction
    # survives every later poll regardless of what the board says next.
    assert "function IS NULL" in sql
    assert params == ("revenue", 7)


def test_backfill_function_reports_nothing_written_when_already_set(mocker):
    conn, cur = _conn(mocker)
    cur.rowcount = 0                      # the WHERE matched no row
    assert outreach.backfill_function(conn, 7, ["VP Revenue"]) is None


def test_backfill_function_skips_ic_titles_entirely(mocker):
    conn, cur = _conn(mocker)
    assert outreach.backfill_function(conn, 7, ["Account Executive", "Sales Rep"]) is None
    cur.execute.assert_not_called()       # nothing derivable → no write attempted


def test_backfill_function_uses_the_first_derivable_leadership_title(mocker):
    conn, cur = _conn(mocker)
    cur.rowcount = 1
    result = outreach.backfill_function(conn, 7, ["Account Executive", "Head of Finance"])
    assert result == "finance"


# --- normalize_domain (pure) --------------------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("acme.com", "acme.com"),
    ("ACME.com", "acme.com"),
    ("https://acme.com", "acme.com"),
    ("http://www.acme.com/careers", "acme.com"),
    ("  https://WWW.Acme.com/jobs?x=1  ", "acme.com"),
    ("acme.com.", "acme.com"),
])
def test_normalize_domain_collapses_url_forms(raw, expected):
    # All of these are ONE company; a duplicate row would inflate the live count
    # against the 15-target capacity cap (R8).
    assert outreach.normalize_domain(raw) == expected


# --- company_domain_from_email (pure) -----------------------------------------


def test_company_domain_from_work_email():
    assert outreach.company_domain_from_email("marcus@cadence.health") == "cadence.health"
    assert outreach.company_domain_from_email("A.B@WWW.Acme.com") == "acme.com"


@pytest.mark.parametrize("email", [
    None, "", "not-an-email", "someone@gmail.com", "someone@icloud.com",
    "someone@proton.me", "someone@localhost",
])
def test_company_domain_rejects_free_and_unparseable(email):
    # A gmail address is not a company identity — two unrelated leads would
    # otherwise collide onto one target row via the domain dedup key.
    assert outreach.company_domain_from_email(email) is None


# --- clean_field (H2) ---------------------------------------------------------


def test_clean_field_strips_invisible_characters():
    # A zero-width char in a job title survives copy-paste into a real email.
    assert outreach.clean_field("VP​Revenue") == "VPRevenue"


def test_clean_field_truncates_and_passes_through_none():
    assert outreach.clean_field("x" * 900, max_chars=500) == "x" * 500
    assert outreach.clean_field(None) is None
    assert outreach.clean_field("   ") is None


# --- evidence_row (pure) ------------------------------------------------------


FACT = {
    "fact_kind": "open_role",
    "dedup_key": "greenhouse:4567",
    "payload": {"title": "VP Revenue", "location": "Remote", "team": None},
    "source_kind": "careers_page",
    "source_url": "https://x/4567",
    "source_excerpt": "VP Revenue",
}


def test_evidence_row_sets_both_dates_to_today_on_first_sight():
    row = outreach.evidence_row(FACT, target_id=7, today=date(2026, 8, 12))
    assert row["first_seen_at"] == date(2026, 8, 12)
    assert row["last_seen_at"] == date(2026, 8, 12)
    assert row["target_id"] == 7 and row["dedup_key"] == "greenhouse:4567"


def test_evidence_row_drops_empty_payload_fields():
    row = outreach.evidence_row(FACT, target_id=1, today=date.today())
    assert row["payload"] == {"title": "VP Revenue", "location": "Remote"}


def test_evidence_row_bounds_the_excerpt_to_the_column_check():
    fact = {**FACT, "source_excerpt": "x" * 900}
    row = outreach.evidence_row(fact, target_id=1, today=date.today())
    # The column has a 500-char CHECK — truncate so a long title is stored,
    # not rejected mid-poll.
    assert len(row["source_excerpt"]) == outreach.MAX_EXCERPT_CHARS


def test_evidence_row_hardens_payload_values():
    fact = {**FACT, "payload": {"title": "VP​Revenue"}}
    row = outreach.evidence_row(fact, target_id=1, today=date.today())
    assert row["payload"]["title"] == "VPRevenue"


# --- upsert_target: the D1 rules ----------------------------------------------


def _conn(mocker, fetch=None):
    cur = mocker.MagicMock()
    cur.fetchone.return_value = fetch if fetch is not None else {"id": 1, "was_inserted": True}
    cur.rowcount = 0
    conn = mocker.MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


TARGET = {
    "company_name": "Cadence Health",
    "company_domain": "https://WWW.Cadence.Health/",
    "stage": "series_a",
    "trigger_kind": "request_open_past_45_days",
    "trigger_date": date(2026, 6, 17),
}


def test_upsert_target_normalizes_the_dedup_key(mocker):
    conn, cur = _conn(mocker)
    outreach.upsert_target(conn, dict(TARGET))
    params = cur.execute.call_args.args[1]
    assert params["company_domain"] == "cadence.health"


def test_upsert_target_never_writes_judgement_columns(mocker):
    # THE D1 rule: human observation outranks a spreadsheet. A re-import must not
    # undo the two-tab diagnostic or reset a target's pipeline status.
    conn, cur = _conn(mocker)
    outreach.upsert_target(conn, dict(TARGET))
    sql = cur.execute.call_args.args[0]
    update_clause = sql.split("DO UPDATE SET", 1)[1]
    for protected in ("s2_stage_fit", "s3_sector_match", "s4_leadership_gap",
                      "s5_team_build_below", "function_state", "status",
                      "stalled_reason"):
        assert protected not in update_clause, f"import must never overwrite {protected}"


def test_upsert_target_moves_trigger_date_forward_only(mocker):
    conn, cur = _conn(mocker)
    outreach.upsert_target(conn, dict(TARGET))
    sql = cur.execute.call_args.args[0]
    # GREATEST(): a stale CSV row cannot rewind the anchor every touch window
    # is measured from.
    assert "trigger_date = GREATEST(outreach_targets.trigger_date, EXCLUDED.trigger_date)" in sql


def test_upsert_target_is_never_a_blind_insert(mocker):
    conn, cur = _conn(mocker)
    outreach.upsert_target(conn, dict(TARGET))
    assert "ON CONFLICT (company_domain) DO UPDATE" in cur.execute.call_args.args[0]


def test_upsert_target_refreshable_columns_coalesce(mocker):
    # A sparse import (only a careers_url, say) must not blank existing data.
    conn, cur = _conn(mocker)
    outreach.upsert_target(conn, dict(TARGET))
    sql = cur.execute.call_args.args[0]
    assert "sector = COALESCE(EXCLUDED.sector, outreach_targets.sector)" in sql


def test_upsert_target_hardens_free_text(mocker):
    conn, cur = _conn(mocker)
    outreach.upsert_target(conn, {**TARGET, "company_name": "Cadence​Health"})
    assert cur.execute.call_args.args[1]["company_name"] == "CadenceHealth"


# --- firmographic + contact enrichment writes (Part 3) ------------------------


def test_update_firmographics_writes_only_present_columns(mocker):
    conn, cur = _conn(mocker)
    outreach.update_firmographics(
        conn, 5, {"sector": "coaching", "headcount": 85, "founded_year": 2011},
        today=date(2026, 8, 28),
    )
    sql, params = cur.execute.call_args.args
    assert "sector = %(sector)s" in sql
    assert "headcount = %(headcount)s" in sql
    assert "founded_year = %(founded_year)s" in sql
    # Absent columns are never assigned — a sparse response cannot blank them.
    assert "ownership_type" not in sql
    assert "hq_location" not in sql
    assert params["id"] == 5


def test_update_firmographics_stamps_headcount_asof_when_headcount_written(mocker):
    conn, cur = _conn(mocker)
    outreach.update_firmographics(conn, 5, {"headcount": 85}, today=date(2026, 8, 28))
    sql, params = cur.execute.call_args.args
    assert "headcount_asof = %(headcount_asof)s" in sql
    assert params["headcount_asof"] == date(2026, 8, 28)


def test_update_firmographics_no_asof_without_headcount(mocker):
    conn, cur = _conn(mocker)
    outreach.update_firmographics(conn, 5, {"sector": "coaching"}, today=date(2026, 8, 28))
    assert "headcount_asof" not in cur.execute.call_args.args[0]


def test_update_firmographics_noop_selects_rather_than_empty_update(mocker):
    # An all-null response must not fire a content-free UPDATE (empty audit diff).
    conn, cur = _conn(mocker)
    outreach.update_firmographics(conn, 5, {"headcount": None}, today=date(2026, 8, 28))
    assert cur.execute.call_args.args[0].strip().startswith("SELECT")


def test_update_contact_guards_operator_verified_email(mocker):
    conn, cur = _conn(mocker)
    outreach.update_contact(conn, 5, {"contact_title": "VP People",
                                      "contact_email": "jane@aiir.co"})
    sql = cur.execute.call_args.args[0]
    assert "contact_role = %(contact_role)s" in sql
    # A provider email must never overwrite one the operator confirmed by hand.
    assert "email_confidence = 'operator_verified'" in sql
    assert "ELSE %(contact_email)s END" in sql


# --- evidence upsert / close --------------------------------------------------


def test_upsert_evidence_reports_new_vs_confirmed(mocker):
    conn, cur = _conn(mocker)
    cur.fetchone.return_value = (True,)
    row = outreach.evidence_row(FACT, target_id=1, today=date.today())
    assert outreach.upsert_evidence(conn, row) is True
    cur.fetchone.return_value = (False,)
    assert outreach.upsert_evidence(conn, row) is False


def test_upsert_evidence_never_rewrites_first_seen_at(mocker):
    # Posting age is the whole point; overwriting first_seen_at on a confirming
    # poll would reset every req's age to zero, silently.
    conn, cur = _conn(mocker)
    cur.fetchone.return_value = (False,)
    outreach.upsert_evidence(conn, outreach.evidence_row(FACT, target_id=1, today=date.today()))
    update_clause = cur.execute.call_args.args[0].split("DO UPDATE SET", 1)[1]
    assert "first_seen_at" not in update_clause
    assert "last_seen_at   = EXCLUDED.last_seen_at" in update_clause


def test_upsert_evidence_reopens_a_returned_fact(mocker):
    conn, cur = _conn(mocker)
    cur.fetchone.return_value = (False,)
    outreach.upsert_evidence(conn, outreach.evidence_row(FACT, target_id=1, today=date.today()))
    assert "closed_at      = NULL" in cur.execute.call_args.args[0]


def test_close_absent_evidence_is_scoped_to_one_kind_and_the_unseen(mocker):
    conn, cur = _conn(mocker)
    cur.rowcount = 2
    n = outreach.close_absent_evidence(
        conn, target_id=5, fact_kind="open_role",
        seen_keys=["greenhouse:1"], today=date(2026, 8, 12),
    )
    sql, params = cur.execute.call_args.args
    assert n == 2
    # Scoped by kind: a poll that only saw open_roles must not close
    # leadership_member facts it never looked at.
    assert "fact_kind = %(fact_kind)s" in sql
    assert "NOT (dedup_key = ANY(%(seen_keys)s))" in sql
    assert "closed_at IS NULL" in sql          # already-closed rows aren't re-dated
    assert params["seen_keys"] == ["greenhouse:1"]


def test_pollable_targets_excludes_terminal_states(mocker):
    conn, cur = _conn(mocker)
    cur.fetchall.return_value = []
    outreach.pollable_targets(conn)
    sql = cur.execute.call_args.args[0]
    assert "careers_url IS NOT NULL" in sql
    assert "'archived'" in sql and "'dropped'" in sql and "'engaged'" in sql
