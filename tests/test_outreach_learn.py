"""Unit tests for Part 4 — the selection feedback loop.

Pure arithmetic over label fixtures, so the whole loop is verifiable on the build
box (no LLM, no cognee). The guarantees that must hold:

  * **R4.2 — no rate below the minimum sample.** A cell under MIN_SAMPLE is never
    reportable, so a segment is never killed on noise.
  * **Smoothing pulls a small sample toward the prior**, so a 100%-accept cell of
    three is not treated as certain.
  * **R4.4 — reasons route.** A `poor_contact_path` reject is not counted against
    the segment; a `too_small` reject is not counted against the country.
  * **R4.5 — recency.** A recent label outweighs an old one.
  * **The score explanation sums** and v1 is exactly `model=None`.
"""

from __future__ import annotations

from datetime import date, timedelta

from agents.outreach import icp, learn

ASOF = date(2026, 8, 27)


def _label(decision, *, segment="coaching_leadership", reason=None,
           headcount="~30", country="US", via="seed_list", pain=None,
           age_days=0):
    return {
        "segment": segment, "headcount_band": headcount, "country": country,
        "discovered_via": via, "pain_layer": pain,
        "review_decision": decision, "reject_reason": reason,
        "reviewed_at": ASOF - timedelta(days=age_days),
    }


# --- R4.2 minimum sample ------------------------------------------------------


def test_a_cell_below_the_minimum_is_not_reportable():
    labels = [_label("accept") for _ in range(learn.MIN_SAMPLE - 1)]
    stats = learn._cell_stats(labels, "segment", 0.8, ASOF)
    assert stats["coaching_leadership"]["reportable"] is False


def test_a_cell_at_the_minimum_is_reportable():
    labels = [_label("accept") for _ in range(learn.MIN_SAMPLE)]
    stats = learn._cell_stats(labels, "segment", 0.8, ASOF)
    assert stats["coaching_leadership"]["reportable"] is True


# --- smoothing ----------------------------------------------------------------


def test_smoothing_pulls_a_tiny_all_accept_cell_below_one():
    """Three accepts is not certainty. Smoothed toward a 0.5 prior, it lands
    well below 1.0."""
    labels = [_label("accept") for _ in range(3)]
    stats = learn._cell_stats(labels, "segment", 0.5, ASOF)
    assert stats["coaching_leadership"]["raw_rate"] == 1.0
    assert stats["coaching_leadership"]["smoothed_rate"] < 0.85


def test_a_large_sample_overrides_the_prior():
    labels = [_label("reject", reason="wrong_segment") for _ in range(100)]
    stats = learn._cell_stats(labels, "segment", 0.9, ASOF)
    # 100 rejects should pull the rate near zero despite a high prior.
    assert stats["coaching_leadership"]["smoothed_rate"] < 0.1


# --- R4.4 reason routing ------------------------------------------------------


def test_an_off_topic_reject_is_excluded_from_a_factor(mocker):
    """A poor_contact_path reject is about Part 3, not the segment. It must not
    lower the segment's accept rate — it is dropped from the denominator."""
    labels = [_label("accept"),
              _label("reject", reason="poor_contact_path")]
    stats = learn._cell_stats(labels, "segment", 0.5, ASOF)
    # Only the accept counts for the segment → sample of 1, not 2.
    assert stats["coaching_leadership"]["sample"] == 1


def test_an_on_topic_reject_is_counted():
    labels = [_label("accept"),
              _label("reject", reason="wrong_segment")]
    stats = learn._cell_stats(labels, "segment", 0.5, ASOF)
    assert stats["coaching_leadership"]["sample"] == 2


def test_too_small_routes_to_headcount_not_country():
    labels = [_label("reject", reason="too_small")]
    country = learn._cell_stats(labels, "country", 0.5, ASOF)
    headcount = learn._cell_stats(labels, "headcount_band", 0.5, ASOF)
    assert country == {}                       # geography-only; too_small excluded
    assert headcount["10-100"]["sample"] == 1  # counted here


# --- R4.5 recency -------------------------------------------------------------


def test_a_recent_label_outweighs_an_old_one():
    recent = [_label("accept", age_days=0)]
    old = [_label("accept", age_days=learn.HALF_LIFE_DAYS * 4)]
    r = learn._cell_stats(recent, "segment", 0.0, ASOF)["coaching_leadership"]
    o = learn._cell_stats(old, "segment", 0.0, ASOF)["coaching_leadership"]
    # Same one accept, but the recent one is pulled further from the 0 prior.
    assert r["smoothed_rate"] > o["smoothed_rate"]


# --- headcount bucketing ------------------------------------------------------


def test_headcount_buckets_free_text_to_icp_bands():
    labels = [_label("accept", headcount="~25 core + facilitator network"),
              _label("accept", headcount="40-70"),
              _label("accept", headcount="~30")]
    stats = learn._cell_stats(labels, "headcount_band", 0.5, ASOF)
    # Three different raw strings collapse to one meaningful band.
    assert set(stats) == {"10-100"}
    assert stats["10-100"]["sample"] == 3


# --- pain_layer is reported but never proposed (R0.14) ------------------------


def test_pain_layer_is_reported_but_never_in_a_proposal():
    labels = [_label("accept", pain="L3") for _ in range(learn.MIN_SAMPLE)]
    rates = {"factors": {"pain_layer":
                         learn._cell_stats(labels, "pain_layer", 0.8, ASOF)}}
    # It appears in the report...
    assert rates["factors"]["pain_layer"]["L3"]["reportable"] is True
    # ...but propose_model skips REPORT_ONLY_FACTORS.
    assert "pain_layer" in learn.REPORT_ONLY_FACTORS
    assert "pain_layer" not in learn.FACTORS


# --- the v2 scorer ------------------------------------------------------------


def test_model_none_is_exactly_v1():
    cand = {"segment": "coaching_leadership", "headcount_band": "~30"}
    assert icp.score_with_model(cand, None) == icp.score(cand)


def test_a_learned_boost_raises_the_score_and_a_penalty_lowers_it():
    cand = {"segment": "coaching_leadership", "headcount_band": "~30"}
    base = icp.score(cand)
    boost = {"base_accept_rate": 0.8,
             "adjustments": {"segment": {"coaching_leadership": 0.99}}}
    penalty = {"base_accept_rate": 0.8,
               "adjustments": {"segment": {"coaching_leadership": 0.30}}}
    assert icp.score_with_model(cand, boost) > base
    assert icp.score_with_model(cand, penalty) < base


def test_an_unlearned_factor_value_scores_as_v1():
    """R4.2 fallback: a segment with no learned adjustment is untouched."""
    cand = {"segment": "engineering_consultancy", "headcount_band": "~30"}
    model = {"base_accept_rate": 0.8,
             "adjustments": {"segment": {"coaching_leadership": 0.99}}}
    assert icp.score_with_model(cand, model) == icp.score(cand)


def test_a_single_factor_cannot_dominate_the_score():
    """R4.1: v2 nudges, it does not replace the operator's weights. Even a fully
    confident factor moves the score by at most MODEL_MAX_SHIFT."""
    cand = {"segment": "coaching_leadership", "headcount_band": "~30"}
    base = icp.score(cand)
    extreme = {"base_accept_rate": 0.0,
               "adjustments": {"segment": {"coaching_leadership": 1.0}}}
    assert abs(icp.score_with_model(cand, extreme) - base) <= icp.MODEL_MAX_SHIFT
