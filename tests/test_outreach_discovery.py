"""Unit tests for Gate 0 — discovery, ICP v1, and the review decision core.

Pure rules tested directly; the DB path with `db.connection` mocked, matching
`test_outreach_intake`. The guarantees that must hold:

  * **ICP v1 reproduces the operator's own workbook arithmetic exactly.** It is
    a transcription, not a new model (R0.8) — if the weighted scores drift from
    4.6 / 4.5 / 3.0 the transcription is wrong.
  * **Every score explains itself and the components sum to it** — an
    explanation that does not add up is worse than none (Part 4 outcome 3).
  * **A rejection cannot exist without a reason** (R0.7). The database enforces
    it too (0018); this covers the early, friendly copy.
  * **A deferral records NO label** (OQ-H) — treating it as a weak reject would
    teach Part 4 that a firm with a missing field is a poor fit.
  * **Promotion refuses without an observed trigger** (R0.3) — the failure the
    live 2026-06-10 trigger_date already demonstrates.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agents._lib import outreach_discovery as gate0
from agents.outreach import icp

BOUTIQUE = {"segment": "corporate_l_and_d", "headcount_band": "80-100"}


# --- ICP v1: the workbook transcription ---------------------------------------


@pytest.mark.parametrize(
    "segment,expected",
    [("coaching_leadership", 4.6), ("corporate_l_and_d", 4.5),
     ("instructional_design", 3.0)],
)
def test_segment_scores_reproduce_the_workbook(segment, expected):
    """The workbook computes these three itself; drift means a bad transcription."""
    assert icp.segment_score(segment) == pytest.approx(expected)


def test_criteria_weights_sum_to_one():
    assert sum(icp.CRITERIA_WEIGHTS.values()) == pytest.approx(1.0)


def test_unscored_segments_get_the_mean_prior_not_the_floor():
    """R0.1 + the module's flagged judgement call.

    A segment the operator has not scored is not evidence of being worse than the
    worst one. Scoring new segments at the scale midpoint would rank them below
    every established segment, so none would ever reach the daily 20 and the
    wider net would be nominal.
    """
    prior = icp.UNSCORED_SEGMENT_PRIOR
    assert icp.segment_score("engineering_consultancy") == prior
    assert icp.segment_score("msp_it_consultancy") == prior
    assert icp.segment_score("instructional_design") < prior < icp.segment_score(
        "corporate_l_and_d"
    )


def test_explanation_components_sum_to_the_score():
    for candidate in (
        BOUTIQUE,
        {"segment": "coaching_leadership", "headcount_band": "~30"},
        {"segment": "product_design_agency", "headcount_band": None},
        {"segment": "instructional_design", "headcount_band": "5000"},
        {**BOUTIQUE, "trigger_kind": "funding_announced"},
    ):
        assert sum(p for _, p, _ in icp.explain(candidate)) == icp.score(candidate)


def test_score_is_deterministic_and_bounded():
    assert icp.score(BOUTIQUE) == icp.score(dict(BOUTIQUE))
    assert 0 <= icp.score({"segment": "instructional_design",
                           "headcount_band": "9000"}) <= 100


def test_pain_layer_is_never_an_input():
    """R0.14: recorded, but held out of the score until the market is understood."""
    without = icp.score(BOUTIQUE)
    for layer in ("L1", "L2", "L3"):
        assert icp.score({**BOUTIQUE, "pain_layer": layer}) == without


@pytest.mark.parametrize(
    "band,expected",
    [("80-100", (80, 100)), ("11-50", (11, 50)), ("~30", (30, 30)),
     ("50+", (50, None)), ("25–50", (25, 50)), (None, (None, None)),
     ("", (None, None)), ("unknown", (None, None))],
)
def test_headcount_parsing_handles_the_workbook_shapes(band, expected):
    assert icp.parse_headcount(band) == expected


def test_unparseable_headcount_scores_as_unknown_not_as_tiny():
    """A failed parse must never look like a one-person company."""
    unknown = icp.score({"segment": "corporate_l_and_d", "headcount_band": "n/a"})
    tiny = icp.score({"segment": "corporate_l_and_d", "headcount_band": "3"})
    assert unknown > tiny


# --- decision validation (R0.7) -----------------------------------------------


def test_reject_without_a_reason_is_refused():
    with pytest.raises(ValueError, match="reason"):
        gate0.validate_decision("reject", None, None)


def test_reject_with_an_unknown_reason_is_refused():
    with pytest.raises(ValueError, match="unknown reject reason"):
        gate0.validate_decision("reject", "vibes", None)


def test_other_requires_a_note():
    with pytest.raises(ValueError, match="needs a note"):
        gate0.validate_decision("reject", "other", "   ")
    gate0.validate_decision("reject", "other", "competitor of an existing client")


def test_accept_must_not_carry_a_reject_reason():
    with pytest.raises(ValueError, match="must not carry"):
        gate0.validate_decision("accept", "too_small", None)


def test_every_reason_routes_somewhere_and_the_vocabulary_is_pinned():
    """R4.4 reads these; a reason the database rejects would be an unusable label."""
    assert "poor_contact_path" in gate0.REJECT_REASONS
    assert "wrong_segment" in gate0.REJECT_REASONS
    assert len(gate0.REJECT_REASONS) == 9


# --- decide() -----------------------------------------------------------------


_DEFAULT = object()


def _patch(mocker, updated=_DEFAULT):
    """Mock the DB so `decide`/`promote` run their logic without a database.

    `updated` takes a sentinel default so a test can pass an explicit None -
    the "UPDATE matched zero rows" case - without it collapsing into the
    default row.
    """
    conn = mocker.MagicMock()
    cm = mocker.MagicMock()
    cm.__enter__.return_value = conn
    cm.__exit__.return_value = False
    mocker.patch.object(gate0.db, "connection", return_value=cm)

    cur = mocker.MagicMock()
    cur.fetchone.return_value = updated if updated is not _DEFAULT else {
        "id": 3, "company_name": "Factor 8", "segment": "corporate_l_and_d",
        "review_decision": "reject", "reject_reason": "too_small",
    }
    conn.cursor.return_value.__enter__.return_value = cur
    conn.transaction.return_value.__enter__.return_value = None
    conn.transaction.return_value.__exit__.return_value = False
    return cur


def test_defer_records_no_label_and_touches_no_row(mocker):
    """OQ-H. A deferral usually means a missing field, not a judgement."""
    cur = _patch(mocker)
    result = gate0.decide(3, "defer")
    assert result["labelled"] is False
    cur.execute.assert_not_called()


def test_reject_records_the_reason_as_the_label(mocker):
    cur = _patch(mocker)
    result = gate0.decide(3, "reject", reason="too_small")
    assert result["labelled"] is True
    sql, params = cur.execute.call_args[0]
    assert "reviewed_at IS NULL" in sql, "the double-submit guard must be in the SQL"
    assert params[0] == "reject" and params[1] == "too_small"


def test_a_second_submit_changes_nothing(mocker):
    """Idempotency is the database's: the guarded UPDATE matches zero rows."""
    _patch(mocker, updated=None)
    assert gate0.decide(3, "reject", reason="too_small") is None


def test_accept_writes_no_reason(mocker):
    cur = _patch(mocker, updated={
        "id": 4, "company_name": "Sales Gravy", "segment": "corporate_l_and_d",
        "review_decision": "accept", "reject_reason": None,
    })
    gate0.decide(4, "accept")
    _, params = cur.execute.call_args[0]
    assert params[1] is None


# --- promote() (R0.3) ---------------------------------------------------------


REAL_TRIGGER = {
    "trigger_kind": "funding_announced",
    "trigger_date": date(2026, 8, 1),
    "trigger_source_url": "https://example.com/round",
}


@pytest.mark.parametrize("missing", ["trigger_kind", "trigger_date",
                                     "trigger_source_url"])
def test_promotion_refuses_a_partial_trigger(missing):
    trigger = {k: v for k, v in REAL_TRIGGER.items() if k != missing}
    with pytest.raises(gate0.NotPromotableError, match=missing):
        gate0.promote(1, trigger)


def test_promotion_refuses_no_trigger_at_all():
    """The whole point of R0.3: no fabricated trigger, ever."""
    with pytest.raises(gate0.NotPromotableError):
        gate0.promote(1, {})


def test_promotion_refuses_a_future_trigger_date():
    with pytest.raises(gate0.NotPromotableError, match="future"):
        gate0.promote(1, {**REAL_TRIGGER,
                          "trigger_date": date.today() + timedelta(days=1)})


def test_promotion_refuses_an_unaccepted_discovery(mocker):
    _patch(mocker, updated={
        "id": 5, "company_name": "Maestro", "review_decision": None,
        "promoted_target_id": None, "company_domain": "maestro.example",
    })
    with pytest.raises(gate0.NotPromotableError, match="unreviewed"):
        gate0.promote(5, REAL_TRIGGER)


def test_promotion_is_idempotent(mocker):
    _patch(mocker, updated={
        "id": 6, "company_name": "ELM", "review_decision": "accept",
        "promoted_target_id": 42, "company_domain": "elm.example",
    })
    assert gate0.promote(6, REAL_TRIGGER) == {"id": 6, "target_id": 42,
                                              "created": False}


# --- the window (R0.11) -------------------------------------------------------


def test_the_daily_window_is_twenty_and_demands_two_verification_kinds():
    assert gate0.DAILY_WINDOW == 20
    assert gate0.MIN_VERIFICATION_KINDS == 2


def _candidate(cid, segment, score, surfaced=None):
    return {"id": cid, "segment": segment, "icp_fit_score": score,
            "discovered_at": cid, "surfaced_at": surfaced}


def test_the_reserve_is_a_quarter_of_the_window():
    assert gate0.EXPLORATION_RESERVE_SHARE == 0.25
    assert round(gate0.DAILY_WINDOW * gate0.EXPLORATION_RESERVE_SHARE) == 5


def test_reserve_round_robins_across_under_sampled_segments():
    """R0.17: each pass takes each segment's best-ranked remaining candidate."""
    candidates = [
        _candidate(1, "coaching_leadership", 84),
        _candidate(2, "coaching_leadership", 84),
        _candidate(3, "corporate_l_and_d", 83),
        _candidate(4, "msp_it_consultancy", 76),
    ]
    picks = gate0._reserve_picks(candidates, {}, 3)
    # Worst-ranked segment first: the reserve exists to reach what ranking will
    # not, so serving the top-ranked segment first would defeat it.
    assert [p["segment"] for p in picks] == [
        "msp_it_consultancy", "corporate_l_and_d", "coaching_leadership",
    ]


def test_a_well_sampled_segment_loses_its_claim_on_the_reserve():
    """Once a segment clears R4.2's minimum, the reserve stops protecting it -
    which is the step-down that shrinks the reserve on its own."""
    candidates = [
        _candidate(1, "coaching_leadership", 84),
        _candidate(2, "msp_it_consultancy", 76),
    ]
    labels = {"coaching_leadership": gate0.MIN_SAMPLE_FOR_RATE}
    picks = gate0._reserve_picks(candidates, labels, 2)
    assert [p["segment"] for p in picks] == ["msp_it_consultancy"]


def test_reserve_stops_when_every_segment_is_exhausted():
    """Must terminate rather than spin when slots exceed available candidates."""
    picks = gate0._reserve_picks([_candidate(1, "coaching_leadership", 84)], {}, 10)
    assert len(picks) == 1


def _window(mocker, candidates, labels=None):
    mocker.patch.object(gate0, "_eligible", return_value=candidates)
    mocker.patch.object(gate0, "segment_label_counts", return_value=labels or {})
    return gate0.list_for_review(mocker.MagicMock(), limit=4)


def test_unfillable_reserve_slots_fall_back_to_the_ranked_list(mocker):
    """A short window would waste review attention, the scarcest thing here."""
    candidates = [_candidate(i, "coaching_leadership", 84) for i in range(1, 9)]
    assert len(_window(mocker, candidates)) == 4


def test_the_reserve_pulls_in_a_lower_ranked_new_segment(mocker):
    """The whole point: without it, a no-history segment never surfaces."""
    candidates = [_candidate(i, "coaching_leadership", 84) for i in range(1, 8)]
    candidates.append(_candidate(99, "product_design_agency", 76))
    window = _window(mocker, candidates)
    assert 99 in {row["id"] for row in window}


def test_the_window_never_exceeds_its_limit_or_repeats_a_row(mocker):
    candidates = [_candidate(i, "coaching_leadership", 84) for i in range(1, 6)]
    candidates.append(_candidate(50, "msp_it_consultancy", 76))
    window = _window(mocker, candidates)
    ids = [row["id"] for row in window]
    assert len(ids) == 4 and len(set(ids)) == 4


def test_the_window_is_returned_best_first(mocker):
    """The reserve guarantees inclusion, not position."""
    candidates = [_candidate(1, "coaching_leadership", 84),
                  _candidate(2, "instructional_design", 62),
                  _candidate(3, "corporate_l_and_d", 83)]
    scores = [row["icp_fit_score"] for row in _window(mocker, candidates)]
    assert scores == sorted(scores, reverse=True)


def test_an_empty_pool_returns_an_empty_window(mocker):
    assert _window(mocker, []) == []


def test_eligible_filters_unreviewed_and_unverified(mocker):
    conn = mocker.MagicMock()
    cur = mocker.MagicMock()
    cur.fetchall.return_value = []
    conn.cursor.return_value.__enter__.return_value = cur
    gate0._eligible(conn)
    sql, params = cur.execute.call_args[0]
    assert "reviewed_at IS NULL" in sql
    assert "icp_fit_score DESC" in sql
    assert "array_length(verified_on, 1)" in sql
    assert params == (gate0.MIN_VERIFICATION_KINDS,)


def test_deferrals_are_not_counted_as_labels(mocker):
    """OQ-H, enforced in the query the reserve reads."""
    conn = mocker.MagicMock()
    cur = mocker.MagicMock()
    cur.fetchall.return_value = []
    conn.cursor.return_value.__enter__.return_value = cur
    gate0.segment_label_counts(conn)
    sql = cur.execute.call_args[0][0]
    assert "review_decision IS NOT NULL" in sql
