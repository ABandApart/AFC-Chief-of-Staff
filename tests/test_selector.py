"""Unit tests for the outreach Selector (Track O, `_lib/selector.py`).

Three things must hold, and the third is the one with teeth:

  * **Parsing** the operator's template pack, which is copied in verbatim — a
    template missing its `## Failure Mode` must fail loudly, not silently
    produce a packet lacking the field that says how this message goes wrong.
  * **Selection** is deterministic, first-matching-rule-wins, and reports why.
  * **Placeholder classification** is right, because the packet's `ready` flag
    depends on it. Misfiling an `operator` token as `auto` is R1 — a literal
    "[Client 1]" in a founder's inbox.

The config-vs-pack integrity tests run against the REAL committed files, so a
typo'd template code in `selector.yaml` fails the build rather than surfacing as
a missing template at intake.
"""

from __future__ import annotations

import pytest
import yaml

from agents._lib import selector
from agents._lib.control_plane import repo_root

# --- parsing ------------------------------------------------------------------

SAMPLE = """# Template 10: The Hiring Trigger

**Section:** Series A

**When to use it:** Use once the req has been open thirty days or more.

## Template Text

**Subject:** Saw you are hiring a [Role Title]

```
Hi [First Name],

Noticed [Company Name] is hiring a [Role Title]. Posted about [X] weeks ago.

[Your Name]
```

## Pro Tip

Referencing the posting date is what makes this land.

## Failure Mode

Positioning yourself as an alternative to the hire.
"""


def test_parse_template_extracts_every_field():
    t = selector.parse_template(SAMPLE, code="template-10-hiring-trigger")
    assert t.title == "Template 10: The Hiring Trigger"
    assert t.section == "Series A"
    assert "thirty days or more" in t.when_to_use
    assert t.subject == "Saw you are hiring a [Role Title]"
    assert "Noticed [Company Name]" in t.body
    assert t.failure_mode.startswith("Positioning yourself")
    # Pro Tip is operator guidance, not packet content — it must not leak into
    # the failure mode, which the packet renders verbatim.
    assert "Pro Tip" not in t.failure_mode
    assert "Referencing" not in t.failure_mode


def test_placeholders_are_deduped_and_ordered():
    t = selector.parse_template(SAMPLE, code="x")
    # [Role Title] appears in both subject and body — once in the list.
    assert t.placeholders == ("Role Title", "First Name", "Company Name", "X", "Your Name")


@pytest.mark.parametrize("drop,missing", [
    ("## Failure Mode\n\nPositioning yourself as an alternative to the hire.\n", "Failure Mode"),
    ("**Section:** Series A\n", "Section"),
    ("**Subject:** Saw you are hiring a [Role Title]\n", "Subject"),
])
def test_parse_template_refuses_a_malformed_file(drop, missing):
    with pytest.raises(selector.SelectorError, match=missing):
        selector.parse_template(SAMPLE.replace(drop, ""), code="broken")


# --- placeholder classification ----------------------------------------------

CONFIG = {
    "placeholders": {
        "auto": ["First Name", "Company Name", "Role Title", "X", "Your Name"],
        "operator": ["Client 1", "specific outcome with a number", "number"],
        "observed_marker": ":",
    }
}


@pytest.mark.parametrize("token,expected", [
    ("First Name", selector.AUTO),
    ("Company Name", selector.AUTO),
    ("Client 1", selector.OPERATOR),
    ("specific outcome with a number", selector.OPERATOR),
    ("New input: a pattern you have seen", selector.OBSERVED),
    ("trigger: the first board meeting", selector.OBSERVED),
])
def test_classify_placeholder(token, expected):
    assert selector.classify_placeholder(token, CONFIG) == expected


def test_unknown_tokens_default_to_operator_not_auto():
    # The asymmetry that matters: an unrecognised token blocking `ready` costs a
    # human glance; one treated as `auto` ships a literal placeholder to a
    # prospect (R1).
    assert selector.classify_placeholder("Some New Token", CONFIG) == selector.OPERATOR


def test_observed_marker_wins_over_a_contained_operator_word():
    # "[trigger: ... asks for the number]" CONTAINS "number", which is an
    # operator token. Classified as operator it would block `ready` forever on a
    # slot the operator is supposed to fill at Gate 2.
    token = "trigger: the first board meeting where someone asks for the number"
    assert selector.classify_placeholder(token, CONFIG) == selector.OBSERVED


def test_partition_splits_a_real_template():
    t = selector.parse_template(SAMPLE, code="x")
    parts = selector.partition_placeholders(t, CONFIG)
    assert set(parts[selector.AUTO]) == {"Role Title", "First Name", "Company Name",
                                         "X", "Your Name"}
    assert parts[selector.OPERATOR] == () and parts[selector.OBSERVED] == ()


# --- selection ----------------------------------------------------------------

RULES = {
    "stage_aliases": {"mature": "series_b_plus"},
    "slots": {
        1: {
            "name": "Recognition",
            "rules": {
                "series_a": [
                    {"when": {"open_role_age_min": 30}, "template": "t10",
                     "because": "req open 30+ days"},
                    {"default": "t09", "because": "no req posted"},
                ],
                "series_b_plus": [{"default": "t17", "because": "b+ default"}],
            },
            "alternates": {"series_a": [{"template": "t15", "needs": "same-sector proof"}]},
        },
        2: {
            "name": "Relevance",
            "rules": {"all": [{"default": "touch-2", "because": "the pack's touch-2"}]},
        },
    },
}


def test_evidence_drives_the_slot_1_choice():
    old = selector.select(1, "series_a", {"open_role_age_days": 45}, RULES)
    assert old.template_code == "t10" and "30+" in old.because

    fresh = selector.select(1, "series_a", {"open_role_age_days": 10}, RULES)
    assert fresh.template_code == "t09"          # below the threshold → default

    none = selector.select(1, "series_a", {}, RULES)
    assert none.template_code == "t09"           # no evidence at all → default


def test_choice_carries_the_reason_and_the_alternates():
    # The intake card shows both: the reasoning makes the pick auditable, the
    # alternates are the templates needing knowledge only the operator has.
    choice = selector.select(1, "series_a", {"open_role_age_days": 45}, RULES)
    assert choice.because
    assert choice.slot == 1 and choice.slot_name == "Recognition"
    assert choice.alternates[0]["template"] == "t15"


def test_mature_aliases_onto_series_b_plus():
    assert selector.select(1, "mature", {}, RULES).template_code == "t17"


def test_stage_independent_slots_resolve_without_a_stage():
    assert selector.select(2, None, {}, RULES).template_code == "touch-2"


def test_stage_specific_slot_without_a_stage_refuses():
    # Migration 0014 lets stage be NULL; sequencing is where it becomes
    # mandatory, and this is the code-side half of that rule.
    with pytest.raises(selector.SelectorError, match="no stage"):
        selector.select(1, None, {}, RULES)


def test_unknown_stage_refuses_rather_than_guessing():
    with pytest.raises(selector.SelectorError, match="no rules for stage"):
        selector.select(1, "pre_seed", {}, RULES)


def test_rules_without_a_default_are_a_config_error():
    broken = {"slots": {1: {"name": "R", "rules": {"seed": [
        {"when": {"open_role_age_min": 30}, "template": "t"}]}}}}
    with pytest.raises(selector.SelectorError, match="no rule matched"):
        selector.select(1, "seed", {}, broken)


def test_trigger_and_recency_conditions():
    rules = {"slots": {1: {"name": "R", "rules": {"seed": [
        {"when": {"trigger_kind": ["funding_announced"], "days_since_trigger_max": 7},
         "template": "t01", "because": "fresh funding"},
        {"default": "t07", "because": "fallback"},
    ]}}}}
    hit = {"trigger_kind": "funding_announced", "days_since_trigger": 3}
    assert selector.select(1, "seed", hit, rules).template_code == "t01"
    # Right trigger, too late.
    late = {"trigger_kind": "funding_announced", "days_since_trigger": 30}
    assert selector.select(1, "seed", late, rules).template_code == "t07"
    # In window, wrong trigger.
    wrong = {"trigger_kind": "product_launch", "days_since_trigger": 3}
    assert selector.select(1, "seed", wrong, rules).template_code == "t07"


# --- integrity of the REAL committed config + pack ---------------------------


@pytest.fixture(scope="module")
def real():
    return selector.load_templates(), selector.load_config()


def test_every_template_in_the_pack_parses(real):
    templates, _ = real
    assert len(templates) >= 40
    for code, t in templates.items():
        assert t.failure_mode, f"{code} has no failure mode"
        assert t.body.strip(), f"{code} has an empty body"


def test_every_code_referenced_by_the_config_exists(real):
    # The build-failing check: a typo'd code in selector.yaml would otherwise
    # surface at intake as a missing template, mid-sequence-materialisation.
    templates, config = real
    referenced: set[str] = set()
    for slot_config in (config.get("slots") or {}).values():
        for rules in (slot_config.get("rules") or {}).values():
            for rule in rules:
                referenced.add(rule.get("template") or rule.get("default"))
        for alts in (slot_config.get("alternates") or {}).values():
            referenced.update(a["template"] for a in alts)
    for group in (config.get("out_of_arc") or {}).values():
        referenced.update(group)

    missing = sorted(referenced - set(templates))
    assert not missing, f"selector.yaml references templates that do not exist: {missing}"


def test_every_template_in_the_pack_is_accounted_for(real):
    # The reverse direction: a template nobody can reach is dead copy. Catches a
    # newly-added file that was never wired into a rule, alternate, or
    # out_of_arc group.
    templates, config = real
    reachable: set[str] = set()
    for slot_config in (config.get("slots") or {}).values():
        for rules in (slot_config.get("rules") or {}).values():
            for rule in rules:
                reachable.add(rule.get("template") or rule.get("default"))
        for alts in (slot_config.get("alternates") or {}).values():
            reachable.update(a["template"] for a in alts)
    for group in (config.get("out_of_arc") or {}).values():
        reachable.update(group)

    orphans = sorted(set(templates) - reachable)
    assert not orphans, f"templates unreachable from selector.yaml: {orphans}"


def test_all_five_slots_resolve_for_every_stage(real):
    _, config = real
    for stage in ("seed", "series_a", "series_b_plus", "mature"):
        for slot in (1, 2, 3, 4, 5):
            choice = selector.select(slot, stage, {}, config)
            assert choice.template_code and choice.because


def test_the_real_pack_classifies_without_surprises(real):
    # Every placeholder in every arc template must land in a class. The count
    # that matters: `auto` tokens are substituted unattended, so an unexpected
    # one appearing here is worth a human noticing.
    templates, config = real
    auto_tokens: set[str] = set()
    for t in templates.values():
        parts = selector.partition_placeholders(t, config)
        auto_tokens.update(parts[selector.AUTO])
    assert auto_tokens <= set(config["placeholders"]["auto"])


def test_selector_yaml_is_valid_yaml_and_versioned():
    path = repo_root() / selector.CONFIG_DIR / "selector.yaml"
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert config["version"] == 1
