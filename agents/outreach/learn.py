"""The selection feedback loop — learn accept rates from Gate 0 labels (Part 4).

Interpretable arithmetic, never an opaque model (R4.1). For each factor value the
operator has decided on — a segment, a headcount band, a country, a sourcing
channel — this computes the accept rate, smoothed toward the current model's
prior and weighted by recency, and refuses to report a rate below the minimum
sample. It PROPOSES; the operator disposes (outcome 4), so nothing here activates
a model or changes a score. `cli/icp_model.py` is the surface.

**No LLM, no cognee.** Pure arithmetic over `outreach_discoveries` rows, fully
verifiable on the build box.

Three rules from the spec shape the maths, and each prevents a specific wrong
answer:

  * **R4.2 — minimum sample.** A rate from three rejections would kill a segment
    on noise. Below `MIN_SAMPLE` decisions in a cell, no rate is reported.
  * **R4.4 — reasons route to different knobs.** A `poor_contact_path` reject is
    about Part 3, not the firm's segment; a `geography` reject is about the
    country list. Pooling every reject into one accept-rate discards the reason
    the operator was made to give. So a reject counts against a factor only when
    its reason is ON-TOPIC for that factor — otherwise it is excluded from that
    factor's denominator, not counted as a rejection of it.
  * **R4.5 — recency.** The operator's taste moves; a week-one label should not
    outvote a week-ten one. Each label is weighted `0.5 ** (age_days /
    HALF_LIFE_DAYS)`.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from psycopg.rows import dict_row

from agents.outreach import icp

# R4.2. Raw labelled decisions in a cell before its rate is reportable.
MIN_SAMPLE = 30

# R4.5, OQ-I. Half-life for recency weighting, in days (8 weeks).
HALF_LIFE_DAYS = 56

# Laplace smoothing strength: how many "prior" observations the smoothing is
# worth. Small, so real data dominates once the sample clears MIN_SAMPLE, but
# non-zero so a cell at exactly the minimum is still pulled off any extreme.
SMOOTHING_STRENGTH = 5.0

# The factors v2 learns on. Single factors only (R4.2) — no interactions until
# the funnel produces far more data. Each maps to the reject reasons that are
# ON-TOPIC for it (R4.4); a reject for any other reason is excluded from this
# factor's denominator rather than counted against it.
FACTORS: dict[str, frozenset[str]] = {
    # A segment reject means the operator judged the firm a poor fit for reasons
    # about the firm/segment, not about contactability or geography.
    "segment": frozenset({"wrong_segment", "no_pain_signal",
                          "competitor_or_conflict"}),
    "headcount_band": frozenset({"too_small", "too_large"}),
    "country": frozenset({"geography"}),
    # Sourcing channel: a wrong_segment reject is evidence the channel surfaced
    # the wrong kind of firm.
    "discovered_via": frozenset({"wrong_segment", "no_pain_signal"}),
}

# Reported for the operator's eye (outcome 1) but NEVER fed to the score:
# pain_layer is recorded and held out of scoring until the market is understood
# (R0.14). Any off-topic-reason routing does not matter — it is report-only.
REPORT_ONLY_FACTORS: dict[str, frozenset[str]] = {
    "pain_layer": frozenset(),
}


def _bucket_value(factor: str, value: object) -> str | None:
    """Normalize a raw field to the value a factor should LEARN on.

    Headcount is free text in the workbook (`~25 core + facilitator network`,
    `40-70`), so learning per raw string is one cell per firm and useless. It
    buckets to the ICP headcount bands `icp.py` already scores on, which is the
    grain a rate is meaningful at. Other factors pass through.
    """
    if value is None:
        return None
    if factor != "headcount_band":
        return str(value)
    low, high = icp.parse_headcount(str(value))
    if low is None and high is None:
        return "unknown"
    midpoint = low if high is None else (low + high) // 2
    if midpoint < 10:
        return "<10"
    if midpoint <= 100:
        return "10-100"
    if midpoint <= 250:
        return "101-250"
    return ">250"


def _recency_weight(reviewed_at: datetime | date, asof: date) -> float:
    reviewed = reviewed_at.date() if isinstance(reviewed_at, datetime) else reviewed_at
    age_days = max(0, (asof - reviewed).days)
    return math.pow(0.5, age_days / HALF_LIFE_DAYS)


def fetch_labels(conn: object) -> list[dict[str, Any]]:
    """Every labelled Gate 0 decision. A deferral is not a label (OQ-H), so only
    rows carrying a `review_decision` count."""
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            SELECT segment, headcount_band, country, discovered_via, pain_layer,
                   review_decision, reject_reason, reviewed_at
            FROM outreach_discoveries
            WHERE review_decision IN ('accept', 'reject')
            """
        )
        return cur.fetchall()


def _cell_stats(labels: list[dict[str, Any]], factor: str, prior: float,
                asof: date) -> dict[str, dict[str, Any]]:
    """Per-value accept stats for one factor. Recency-weighted, reason-routed."""
    on_topic = FACTORS.get(factor, REPORT_ONLY_FACTORS.get(factor, frozenset()))
    cells: dict[str, dict[str, float]] = {}
    for row in labels:
        value = _bucket_value(factor, row.get(factor))
        if value is None:
            continue
        is_accept = row["review_decision"] == "accept"
        # R4.4: an off-topic reject is not evidence about THIS factor. Skip it —
        # it neither accepts nor rejects this cell.
        if not is_accept and row.get("reject_reason") not in on_topic:
            continue
        weight = _recency_weight(row["reviewed_at"], asof)
        cell = cells.setdefault(str(value), {"accepts": 0.0, "total": 0.0, "n": 0})
        cell["total"] += weight
        cell["n"] += 1
        if is_accept:
            cell["accepts"] += weight

    out: dict[str, dict[str, Any]] = {}
    for value, cell in cells.items():
        n = int(cell["n"])
        # Laplace smoothing toward the prior accept rate.
        smoothed = ((cell["accepts"] + SMOOTHING_STRENGTH * prior)
                    / (cell["total"] + SMOOTHING_STRENGTH))
        out[value] = {
            "sample": n,
            "raw_rate": (cell["accepts"] / cell["total"]) if cell["total"] else None,
            "smoothed_rate": round(smoothed, 4),
            "reportable": n >= MIN_SAMPLE,
        }
    return out


def base_accept_rate(labels: list[dict[str, Any]]) -> float:
    """The overall accept rate — the prior every cell is smoothed toward."""
    if not labels:
        return 0.5
    accepts = sum(1 for r in labels if r["review_decision"] == "accept")
    return accepts / len(labels)


def active_model(conn: object) -> tuple[str, dict[str, Any]] | None:
    """The one active ICP model as (version, factors), or None if none is set.

    New scores read this and pass `factors` to `icp.score_with_model`, so an
    operator-activated v2 takes effect on the next scoring run with no code
    change. With only the v1 baseline active, `factors['adjustments']` is empty
    and the score is exactly v1.
    """
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute("SELECT version, factors FROM outreach_icp_models WHERE active")
        row = cur.fetchone()
    return (row["version"], row["factors"]) if row else None


def compute_rates(conn: object, asof: date | None = None) -> dict[str, Any]:
    """The full report: base rate + per-factor, per-value smoothed accept rates.

    Rates below MIN_SAMPLE are computed but flagged `reportable=False`, so the CLI
    can show "not enough data yet" rather than a number that looks authoritative.
    """
    asof = asof or date.today()
    labels = fetch_labels(conn)
    prior = base_accept_rate(labels)
    return {
        "asof": asof.isoformat(),
        "total_labels": len(labels),
        "base_accept_rate": round(prior, 4),
        "min_sample": MIN_SAMPLE,
        "factors": {
            factor: _cell_stats(labels, factor, prior, asof)
            for factor in (*FACTORS, *REPORT_ONLY_FACTORS)
        },
        # pain_layer is reported (outcome 1) but held out of any proposal (R0.14).
        "report_only": tuple(REPORT_ONLY_FACTORS),
    }


def propose_model(conn: object, version: str, asof: date | None = None,
                  ) -> dict[str, Any]:
    """Build a proposed model's `factors` JSON from the reportable rates.

    Only cells that clear MIN_SAMPLE contribute an adjustment; everything else
    falls back to v1 at score time. The proposal is data — it is written inactive
    by the CLI and takes effect only on explicit activation (outcome 4).
    """
    rates = compute_rates(conn, asof)
    adjustments: dict[str, dict[str, float]] = {}
    for factor, cells in rates["factors"].items():
        if factor in REPORT_ONLY_FACTORS:
            continue  # R0.14: pain_layer is observed, never scored
        reportable = {
            value: cell["smoothed_rate"]
            for value, cell in cells.items() if cell["reportable"]
        }
        if reportable:
            adjustments[factor] = reportable
    return {
        "version": version,
        "base_accept_rate": rates["base_accept_rate"],
        "adjustments": adjustments,
        "built_from_labels": rates["total_labels"],
        "half_life_days": HALF_LIFE_DAYS,
        "min_sample": MIN_SAMPLE,
    }
