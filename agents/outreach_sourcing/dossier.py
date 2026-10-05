"""The candidate dossier: the one thing a research worker may return.

PRD-outreach-autonomous-sourcing §6.5. Workers have no write tools; this schema,
delivered through a `strict: true` client tool, is their only output. Every
field is required (nullable where the agent may legitimately not know), so a
dossier is either complete or visibly says what is missing.
"""

from __future__ import annotations

from typing import Any

# The eight market triggers from `35-` §3 (the vocabulary in 0023/0029).
MARKET_TRIGGERS = (
    "executive_departure", "request_open_past_45_days", "new_executive_hire",
    "second_raise", "funding_announced", "restructuring_or_layoffs",
    "market_or_region_expansion", "product_launch",
)
SOURCE_KINDS = ("own_site", "job_post", "press", "directory", "filing",
                "search_result", "other")
CONTACT_METHODS = ("published", "pattern_inferred", "generic_inbox", "none_found")
STAGES = ("seed", "series_a", "series_b_plus", "mature")
FUNCTION_STATES = ("self_covered", "under_led", "vacant_seat")
OWNERSHIP = ("vc_backed", "pe_backed", "bootstrapped", "founder_owned", "public")
SCORE_VALUES = (1, 3, 5)

SUBMIT_TOOL = "submit_dossier"


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    if "enum" in schema:
        return {"anyOf": [schema, {"type": "null"}]}
    return {**schema, "type": [schema["type"], "null"]}


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props),
            "additionalProperties": False}


_STR = {"type": "string"}
_SCORE = {"type": "integer", "enum": list(SCORE_VALUES)}

DOSSIER_SCHEMA: dict[str, Any] = _obj({
    "no_candidate_reason": _nullable(_STR),
    "company_name": _STR,
    "domain": _STR,
    "company_url": _STR,
    "country": _STR,
    "hq_location": _nullable(_STR),
    "headcount_estimate": _nullable(_STR),
    "ownership_type": _nullable({"type": "string", "enum": list(OWNERSHIP)}),
    "segment_key": _STR,
    "summary": _STR,
    "why_now": _STR,
    "trigger": _obj({
        "kind": _nullable({"type": "string", "enum": list(MARKET_TRIGGERS)}),
        "date": _nullable(_STR),
        "source_url": _nullable(_STR),
    }),
    "evidence": {"type": "array", "items": _obj({
        "claim": _STR,
        "url": _STR,
        "source_kind": {"type": "string", "enum": list(SOURCE_KINDS)},
        "date": _nullable(_STR),
    })},
    "contact": _obj({
        "name": _nullable(_STR),
        "title": _nullable(_STR),
        "email": _nullable(_STR),
        "method": {"type": "string", "enum": list(CONTACT_METHODS)},
        "source_url": _nullable(_STR),
    }),
    "proposed": _obj({
        "s2_stage_fit": _SCORE,
        "s3_sector_match": _SCORE,
        "s4_leadership_gap": _SCORE,
        "s5_team_build_below": _SCORE,
        "stage": {"type": "string", "enum": list(STAGES)},
        "function_state": {"type": "string", "enum": list(FUNCTION_STATES)},
        "reasons": _STR,
    }),
    "suggested_angle": _STR,
})

SUBMIT_TOOL_DEF: dict[str, Any] = {
    "name": SUBMIT_TOOL,
    "description": (
        "Submit the finished candidate dossier. Call this exactly once, at the end. "
        "If no suitable firm could be found or verified, set no_candidate_reason "
        "and fill the other fields with empty strings or nulls."
    ),
    "input_schema": DOSSIER_SCHEMA,
    "strict": True,
}

# Email confidence on the discovery row, from how the worker found the contact.
EMAIL_CONFIDENCE = {
    "published": "public",
    "pattern_inferred": "inferred_pattern",
    "generic_inbox": "general_inbox",
    "none_found": None,
}


def proposed_scores(dossier: dict[str, Any]) -> dict[str, Any]:
    """The JSON stored in `outreach_discoveries.proposed_scores` (pure).

    Holds the judgement columns approval writes to the target, the reasons, and
    the cited market trigger, which `outreach_discovery.trigger_for` reads.
    """
    proposed = dict(dossier.get("proposed") or {})
    trigger = dossier.get("trigger") or {}
    if trigger.get("kind"):
        proposed["trigger"] = {"kind": trigger["kind"], "date": trigger.get("date"),
                               "source_url": trigger.get("source_url")}
    return proposed
