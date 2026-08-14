"""Unit tests for the gaps/exception report (Track O, `cli/outreach_gaps.py`).

`find_gaps` is pure, so every case is a dict. The ones that matter are the quiet
failures — a target that will never accrue evidence looks exactly like a healthy
one until its packet turns out empty, and that is the failure this report exists
to make loud.
"""

from __future__ import annotations

from cli import outreach_gaps

HEALTHY = {
    "id": 1, "company_name": "Acme", "status": "candidate",
    "careers_url": "https://jobs.lever.co/acme",
    "stage": "series_a", "trigger_kind": "funding_announced",
    "contact_name": "Jane Smith", "contact_email": "jane@acme.com",
    "unscored": False, "open_roles": 2, "days_since_confirmed": 0,
}


def _severities(row, **kw):
    return [s for s, _ in outreach_gaps.find_gaps(row, **kw)]


def test_a_complete_profile_has_no_gaps():
    assert outreach_gaps.find_gaps(HEALTHY) == []


# --- blockers -----------------------------------------------------------------


def test_missing_stage_blocks_sequencing():
    gaps = outreach_gaps.find_gaps({**HEALTHY, "stage": None})
    assert ("blocker" in [s for s, _ in gaps])
    assert any("cannot enter a sequence" in m for _, m in gaps)


def test_missing_contact_fields_block():
    assert "blocker" in _severities({**HEALTHY, "contact_name": None})
    assert "blocker" in _severities({**HEALTHY, "contact_email": None})


# --- evidence acquisition ------------------------------------------------------


def test_no_careers_url_is_an_evidence_gap():
    gaps = outreach_gaps.find_gaps({**HEALTHY, "careers_url": None})
    assert any(s == "evidence" and "cannot be backfilled" in m for s, m in gaps)


def test_unsupported_platform_is_flagged_with_the_url():
    # The four real cases: Hireology, BreatheHR, SaaSHR, and custom careers sites.
    gaps = outreach_gaps.find_gaps(
        {**HEALTHY, "careers_url": "https://hr.breathehr.com/recruitment/vacancies"}
    )
    assert any(s == "evidence" and "unsupported platform" in m for s, m in gaps)


def test_a_dead_board_handle_is_only_reported_when_actually_checked():
    # ELM Learning's case: detected as lever:elearningmind, but the handle 404s.
    row = {**HEALTHY, "open_roles": 0}
    checked = outreach_gaps.find_gaps(row, board_ok=False)
    assert any("unreachable" in m for _, m in checked)

    # Without --check-boards we did NOT look, and "did not look" must never be
    # reported as "the board works" — nor as a failure.
    unchecked = outreach_gaps.find_gaps(row, board_ok=None)
    assert not any("unreachable" in m for _, m in unchecked)


# --- integrity ----------------------------------------------------------------


def test_trigger_asserting_an_open_req_with_no_evidence():
    # Roffey Park's live case. The angle's whole mechanic is quoting the posting
    # date, and there is no posting to quote.
    gaps = outreach_gaps.find_gaps(
        {**HEALTHY, "trigger_kind": "request_open_past_45_days", "open_roles": 0}
    )
    assert any(s == "integrity" and "cannot quote the posting date" in m for s, m in gaps)


def test_the_same_trigger_with_evidence_is_fine():
    gaps = outreach_gaps.find_gaps(
        {**HEALTHY, "trigger_kind": "request_open_past_45_days", "open_roles": 1}
    )
    assert "integrity" not in [s for s, _ in gaps]


def test_a_trigger_that_implies_nothing_about_reqs_is_not_flagged():
    gaps = outreach_gaps.find_gaps(
        {**HEALTHY, "trigger_kind": "product_launch", "open_roles": 0}
    )
    assert "integrity" not in [s for s, _ in gaps]


# --- staleness (R19) -----------------------------------------------------------


def test_stale_evidence_is_flagged_past_the_tier_boundary():
    assert "staleness" not in _severities({**HEALTHY, "days_since_confirmed": 14})
    assert "staleness" in _severities({**HEALTHY, "days_since_confirmed": 15})


def test_no_evidence_at_all_is_not_reported_as_stale():
    # days_since_confirmed is NULL when nothing has ever been observed; that is
    # an evidence gap, not a staleness one, and conflating them would hide it.
    gaps = outreach_gaps.find_gaps({**HEALTHY, "days_since_confirmed": None,
                                    "open_roles": 0})
    assert "staleness" not in [s for s, _ in gaps]


# --- incomplete ----------------------------------------------------------------


def test_unscored_target_cannot_reach_the_intake_gate():
    gaps = outreach_gaps.find_gaps({**HEALTHY, "unscored": True})
    assert any(s == "incomplete" and "intake gate" in m for s, m in gaps)


def test_supported_board_with_no_roles_is_noted_as_not_an_error():
    gaps = outreach_gaps.find_gaps({**HEALTHY, "open_roles": 0})
    assert any("not an error" in m for _, m in gaps)


def test_severities_are_reported_in_cost_order():
    row = {**HEALTHY, "stage": None, "unscored": True, "days_since_confirmed": 30}
    order = [outreach_gaps.SEVERITIES.index(s) for s, _ in outreach_gaps.find_gaps(row)]
    assert order == sorted(order)
