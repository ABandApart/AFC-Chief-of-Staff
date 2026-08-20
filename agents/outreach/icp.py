"""ICP fit scoring for discovered firms (Track O, Part 0 · R0.8).

**v1 is the operator's own arithmetic, not a new model.** The workbook
`Education_LD_Leads_CRM_(current).xlsx` already carries a weighted-criteria table
the operator authored and can defend — six criteria, weights summing to 1.0, and a
score per segment. This module transcribes it and adds the firm-level modifiers
that are observable at discovery time. Nothing here is learned; Part 4 is where
learning happens, and it will sit *beside* v1 rather than replacing it, which is
why every score records `MODEL_VERSION`.

Two rules from the spec constrain what may be used as an input:

  * **Pain layer is not an input** (R0.14). The taxonomy is recorded and the
    column exists, but scoring against it before the market is understood would
    bake in the assumption this whole exercise exists to test.
  * **Every score explains itself** (Part 4 outcome 3). `explain()` returns the
    components, and they sum to the score. A number the operator cannot take
    apart is one he cannot tell is wrong.

THE UNSCORED-SEGMENT PRIOR — a judgement call, flagged rather than buried
-------------------------------------------------------------------------
Three of the six segments (engineering consultancies, product design agencies,
MSP/IT consultancies) are new and carry no workbook score. The spec says they
"start with no weight and no history" and does not say what v1 should do with
them, which makes it an open decision rather than licence to pick quietly.

v1 scores them at the **mean of the three scored segments** (4.03 of 5). The
reasoning: the operator scored the three segments he knows, and a segment he has
not scored is not evidence of being worse than the worst one. The alternative —
the midpoint of the 1-5 scale, 3.0 — would place every new segment below every
established one, so none would ever reach the daily 20, and the wider net the
revision asks for would be nominal. The 25% exploration reserve (R0.17) now
mitigates that, but it does not remove the question: the reserve guarantees a new
segment is SEEN, while the prior decides where it ranks once it is.

Surfaced as OQ-K in the PRD (§0.7), where the recommendation is that the operator
score the three new segments on the same six criteria and remove the prior
altogether. Until then, changing it is a one-line change to
`UNSCORED_SEGMENT_PRIOR` plus a model version bump.
"""

from __future__ import annotations

import re
from typing import Any

# Bump on ANY change to the weights, the modifiers, or the prior. The version
# travels with the score into `outreach_discoveries.icp_model_version` so a
# historical score stays attributable to the arithmetic that produced it.
MODEL_VERSION = "v1"

# The workbook's "Segment Scoring" sheet, criteria weights row. Sums to 1.0.
# Ability to Pay is weighted highest at 0.25 because a fractional CPO practice
# is sold to firms that can afford one; the sheet says so in its own notes.
CRITERIA_WEIGHTS: dict[str, float] = {
    "market_size": 0.10,
    "market_growth": 0.10,
    "firm_profitability": 0.15,
    "ability_to_pay": 0.25,
    "urgency_pain": 0.20,
    "offering_fit": 0.20,
}

# The workbook's per-segment scores, 1-5, transcribed verbatim. The weighted
# results it computes (4.6 / 4.5 / 3.0) are reproduced exactly by these numbers,
# which is the check that the transcription is right.
SEGMENT_CRITERIA: dict[str, dict[str, int]] = {
    "coaching_leadership": {
        "market_size": 4, "market_growth": 4, "firm_profitability": 5,
        "ability_to_pay": 5, "urgency_pain": 4, "offering_fit": 5,
    },
    "corporate_l_and_d": {
        "market_size": 5, "market_growth": 4, "firm_profitability": 4,
        "ability_to_pay": 4, "urgency_pain": 5, "offering_fit": 5,
    },
    "instructional_design": {
        "market_size": 3, "market_growth": 3, "firm_profitability": 2,
        "ability_to_pay": 2, "urgency_pain": 5, "offering_fit": 3,
    },
}

ALL_SEGMENTS = (
    "corporate_l_and_d",
    "coaching_leadership",
    "instructional_design",
    "engineering_consultancy",
    "product_design_agency",
    "msp_it_consultancy",
)


def _weighted(criteria: dict[str, int]) -> float:
    return sum(criteria[k] * w for k, w in CRITERIA_WEIGHTS.items())


# See the module docstring. The mean of what the operator has actually scored.
UNSCORED_SEGMENT_PRIOR: float = sum(
    _weighted(c) for c in SEGMENT_CRITERIA.values()
) / len(SEGMENT_CRITERIA)

# Point budget. Segment carries most of it because at discovery time it is most
# of what is known; the modifiers are the little that firm-level observation adds.
SEGMENT_POINTS = 70
HEADCOUNT_POINTS = 20
TRIGGER_POINTS = 10

# The ICP is a boutique: "Founder/CEO of an expertise-driven, services-heavy
# education business" (the workbook's Guide sheet). Below ~10 people there is no
# budget for a fractional CPO; above ~100 the buyer is usually not the founder.
# Unknown scores mid rather than low — missing data is not evidence of a bad fit,
# and punishing it would make un-enriched firms invisible before Part 3 lands.
_HEADCOUNT_BANDS: tuple[tuple[int, int, int], ...] = (
    (10, 100, 20),
    (101, 250, 10),
    (1, 9, 5),
)
_HEADCOUNT_UNKNOWN = 10
_HEADCOUNT_TOO_LARGE = 0


def criteria_for(segment: str, entered: dict[str, dict[str, int]] | None = None
                 ) -> dict[str, int] | None:
    """The six criteria for a segment: operator-entered first, workbook second.

    R0.20. `entered` is the `outreach_segment_scores` table, passed in rather than
    read here so this module stays pure and testable without a database - the
    caller loads it once per run instead of once per candidate.
    """
    if entered and segment in entered:
        return entered[segment]
    return SEGMENT_CRITERIA.get(segment)


def segment_score(segment: str,
                  entered: dict[str, dict[str, int]] | None = None) -> float:
    """The weighted score for a segment, 1-5, or the prior if it has none."""
    criteria = criteria_for(segment, entered)
    if criteria is None:
        return UNSCORED_SEGMENT_PRIOR
    return _weighted(criteria)


def is_scored(segment: str,
              entered: dict[str, dict[str, int]] | None = None) -> bool:
    """Has this segment been rated at all? Drives the unscored bucket (R0.18)."""
    return criteria_for(segment, entered) is not None


def parse_headcount(band: str | None) -> tuple[int | None, int | None]:
    """Read the workbook's headcount shapes into a (low, high) pair.

    The column is estimates written by hand - `80-100`, `11-50`, `~30`, `50+`.
    Returns (None, None) when nothing parses, which scores as unknown rather than
    as zero: a failed parse must never look like a one-person company.
    """
    if not band:
        return (None, None)
    text = str(band).strip().replace("–", "-").replace("—", "-")
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    if not numbers:
        return (None, None)
    if len(numbers) >= 2:
        return (min(numbers[0], numbers[1]), max(numbers[0], numbers[1]))
    only = numbers[0]
    if "+" in text:
        return (only, None)
    # `~50` and a bare `50` are both a point estimate.
    return (only, only)


def _headcount_points(band: str | None) -> tuple[int, str]:
    low, high = parse_headcount(band)
    if low is None and high is None:
        return (_HEADCOUNT_UNKNOWN, "headcount unknown")
    # Judge on the midpoint of the band; an open-ended `50+` judges on its floor.
    midpoint = low if high is None else (low + high) // 2
    for lo, hi, points in _HEADCOUNT_BANDS:
        if lo <= midpoint <= hi:
            return (points, f"headcount ~{midpoint}")
    return (_HEADCOUNT_TOO_LARGE, f"headcount ~{midpoint}, above the ICP band")


def explain(candidate: dict[str, Any],
            entered: dict[str, dict[str, int]] | None = None
            ) -> list[tuple[str, int, str]]:
    """Return the score's components as (factor, points, note).

    The points sum to exactly what `score()` returns - asserted by a test, since
    an explanation that does not add up is worse than none.
    """
    segment = candidate.get("segment") or ""
    weighted = segment_score(segment, entered)
    seg_points = round(weighted / 5 * SEGMENT_POINTS)
    if entered and segment in entered:
        seg_note = f"operator-rated {weighted:.2f}/5"
    elif segment in SEGMENT_CRITERIA:
        seg_note = f"workbook weighted score {weighted:.2f}/5"
    else:
        seg_note = f"unrated; prior {weighted:.2f}/5 (mean of rated segments)"

    head_points, head_note = _headcount_points(candidate.get("headcount_band"))

    has_trigger = bool(candidate.get("trigger_kind"))
    trig_points = TRIGGER_POINTS if has_trigger else 0
    trig_note = (
        f"trigger observed: {candidate.get('trigger_kind')}"
        if has_trigger
        else "no trigger observed yet"
    )

    return [
        (f"segment · {segment or 'unset'}", seg_points, seg_note),
        ("headcount", head_points, head_note),
        ("trigger", trig_points, trig_note),
    ]


def score(candidate: dict[str, Any],
          entered: dict[str, dict[str, int]] | None = None) -> int:
    """ICP fit, 0-100. Deterministic, and the same inputs always give the same
    number - there is no clock and no randomness here, so a re-score only moves
    when the candidate's own fields move."""
    total = sum(points for _, points, _ in explain(candidate, entered))
    return max(0, min(100, total))
