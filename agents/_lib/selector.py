"""The outreach Selector — template lookup and placeholder partition (Track O).

Two jobs, both **deterministic and LLM-free** (`35-` §5, `40-action-layer.md`
Outreach_loops):

1. **Parse the template pack.** `config/outreach/templates/*.md` are the
   operator's own files, copied in **verbatim** so re-syncing is a plain copy.
   Everything machine-readable is derived by parsing their consistent structure
   (`**Section:**`, `**Subject:**`, a fenced body, `## Failure Mode`) rather than
   by editing them — which is why the metadata lives in `selector.yaml` beside
   them and not in front matter inside them.

2. **Resolve `(stage, slot, evidence) → template`,** first-matching-rule-wins,
   returning the reason alongside the choice so the intake card can show *why*
   this angle and offer the alternates.

**The placeholder partition is the load-bearing part.** `35-` §7 splits
placeholders three ways, and the packet's `ready` flag — the cheapest guard
against R1, a literal `[Client 1]` reaching a founder — depends on classifying
them correctly:

  - **`auto`** — substituted unattended from columns and dates.
  - **`observed`** — the sentence the operator writes from evidence at Gate 2.
    Expected to be open in the packet; does **not** block `ready`.
  - **`operator`** — from the operator's own book. Ungeneratable, and blocks
    `ready` until resolved.

Unknown tokens default to **`operator`**, deliberately. The error is asymmetric:
calling an operator token `auto` ships a literal placeholder to a prospect;
calling an auto token `operator` merely makes a human look at it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from agents._lib.control_plane import repo_root

CONFIG_DIR = "config/outreach"

# `[First Name]`, `[trigger: …]`. Non-greedy, no nesting — the pack has none.
_PLACEHOLDER_RE = re.compile(r"\[([^\[\]]+)\]")
_SECTION_RE = re.compile(r"^\*\*Section:\*\*\s*(.+)$", re.M)
_WHEN_RE = re.compile(r"^\*\*When to use it:\*\*\s*(.+)$", re.M)
_SUBJECT_RE = re.compile(r"^\*\*Subject:\*\*\s*(.+)$", re.M)
_BODY_RE = re.compile(r"```\n(.*?)```", re.S)
_FAILURE_RE = re.compile(r"^## Failure Mode\s*\n+(.+?)(?=\n## |\Z)", re.M | re.S)
_TITLE_RE = re.compile(r"^#\s*(.+)$", re.M)

AUTO = "auto"
OBSERVED = "observed"
OPERATOR = "operator"


class SelectorError(ValueError):
    """The template pack or selector config is malformed. Raised loudly: this is
    trusted, git-authored config (B4), so a bad file is a build problem."""


@dataclass(frozen=True)
class Template:
    """One parsed template file."""

    code: str                    # the filename stem — what `outreach_touches.template_code` stores
    title: str
    section: str
    when_to_use: str
    subject: str
    body: str
    failure_mode: str            # shown verbatim on the packet (`35-` §7)
    path: Path | None = None

    @property
    def placeholders(self) -> tuple[str, ...]:
        """Every distinct placeholder token in the subject and body, in order."""
        seen: list[str] = []
        for token in _PLACEHOLDER_RE.findall(f"{self.subject}\n{self.body}"):
            if token not in seen:
                seen.append(token)
        return tuple(seen)


@dataclass(frozen=True)
class Choice:
    """A resolved template, with why it was chosen and what else was available."""

    template_code: str
    because: str
    slot: int
    slot_name: str
    alternates: tuple[dict[str, str], ...] = field(default_factory=tuple)


# --- parsing ------------------------------------------------------------------


def _one(pattern: re.Pattern[str], text: str, *, field_name: str, path: Path) -> str:
    match = pattern.search(text)
    if match is None:
        raise SelectorError(f"{path.name}: no {field_name} found")
    return match.group(1).strip()


def parse_template(text: str, *, code: str, path: Path | None = None) -> Template:
    """Parse one template file (pure).

    Raises `SelectorError` naming the file if a required section is missing —
    a template with no `## Failure Mode` would silently produce a packet missing
    the one field that tells the operator how this message goes wrong.
    """
    p = path or Path(code)
    return Template(
        code=code,
        title=_one(_TITLE_RE, text, field_name="title", path=p),
        section=_one(_SECTION_RE, text, field_name="**Section:**", path=p),
        when_to_use=_one(_WHEN_RE, text, field_name="**When to use it:**", path=p),
        subject=_one(_SUBJECT_RE, text, field_name="**Subject:**", path=p),
        body=_one(_BODY_RE, text, field_name="fenced template body", path=p),
        failure_mode=_one(_FAILURE_RE, text, field_name="## Failure Mode", path=p),
        path=path,
    )


def load_templates(root: Path | None = None) -> dict[str, Template]:
    """Parse every template in `config/outreach/templates/` (keyed by code)."""
    directory = (root or repo_root()) / CONFIG_DIR / "templates"
    if not directory.is_dir():
        raise SelectorError(f"template directory missing: {directory}")
    templates: dict[str, Template] = {}
    for path in sorted(directory.glob("*.md")):
        templates[path.stem] = parse_template(
            path.read_text(encoding="utf-8"), code=path.stem, path=path
        )
    if not templates:
        raise SelectorError(f"no templates found in {directory}")
    return templates


def load_config(root: Path | None = None) -> dict[str, Any]:
    path = (root or repo_root()) / CONFIG_DIR / "selector.yaml"
    if not path.is_file():
        raise SelectorError(f"selector config missing: {path}")
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise SelectorError(f"{path.name}: not a mapping")
    return config


@lru_cache(maxsize=1)
def _cached() -> tuple[dict[str, Template], dict[str, Any]]:
    return load_templates(), load_config()


# --- placeholder classification ----------------------------------------------


def classify_placeholder(token: str, config: dict[str, Any]) -> str:
    """Classify one placeholder token into `auto` / `observed` / `operator`.

    Order matters. The observed marker is checked BEFORE the operator list so a
    descriptive prompt that happens to contain a listed word — "[trigger: the
    first board meeting where someone asks for the number]" contains "number" —
    is not misfiled as an operator slot and left blocking `ready` forever.
    """
    placeholders = config.get("placeholders") or {}
    marker = placeholders.get("observed_marker") or ":"
    if marker in token:
        return OBSERVED
    if token in set(placeholders.get("auto") or ()):
        return AUTO
    # Everything else — listed operator token or unrecognised — needs a human.
    return OPERATOR


def partition_placeholders(
    template: Template, config: dict[str, Any]
) -> dict[str, tuple[str, ...]]:
    """Split a template's placeholders into the three classes (`35-` §7)."""
    buckets: dict[str, list[str]] = {AUTO: [], OBSERVED: [], OPERATOR: []}
    for token in template.placeholders:
        buckets[classify_placeholder(token, config)].append(token)
    return {k: tuple(v) for k, v in buckets.items()}


# --- selection ----------------------------------------------------------------


def _matches(when: dict[str, Any], facts: dict[str, Any]) -> bool:
    """Evaluate one rule's conditions against the target's facts (pure).

    Conditions are deliberately few and all mechanically checkable from data the
    system actually holds — the evidence table and the target row. A condition
    that needed the operator's memory would not belong in an auto-selected rule;
    those templates are `alternates`.
    """
    if kinds := when.get("trigger_kind"):
        if facts.get("trigger_kind") not in kinds:
            return False
    if (min_age := when.get("open_role_age_min")) is not None:
        age = facts.get("open_role_age_days")
        if age is None or age < min_age:
            return False
    if (max_days := when.get("days_since_trigger_max")) is not None:
        since = facts.get("days_since_trigger")
        if since is None or since > max_days:
            return False
    return True


def select(
    slot: int, stage: str | None, facts: dict[str, Any], config: dict[str, Any] | None = None
) -> Choice:
    """Resolve the template for one slot. First matching rule wins.

    `stage` may be None (genuinely unknown — migration 0014 permits it), in
    which case slot 1 has no stage-specific rule set and this raises. Slots 2–5
    are stage-independent, so they resolve regardless.
    """
    config = config if config is not None else _cached()[1]
    slots = config.get("slots") or {}
    slot_config = slots.get(slot)
    if slot_config is None:
        raise SelectorError(f"no rules configured for slot {slot}")

    rules_by_stage = slot_config.get("rules") or {}
    # "all" = stage-independent (slots 2-5). Otherwise look up the stage.
    if "all" in rules_by_stage:
        rules = rules_by_stage["all"]
        alternates = (slot_config.get("alternates") or {}).get("all") or []
    else:
        if stage is None:
            raise SelectorError(
                f"slot {slot} is stage-specific and this target has no stage — "
                "set one before sequencing (outreach_targets_seq_ck enforces this)"
            )
        # `mature` aliases onto series_b_plus rather than duplicating its rules
        # (operator, 2026-08-14) — duplication would let the two diverge.
        key = (config.get("stage_aliases") or {}).get(stage, stage)
        if key not in rules_by_stage:
            raise SelectorError(f"slot {slot}: no rules for stage {stage!r}")
        rules = rules_by_stage[key]
        alternates = (slot_config.get("alternates") or {}).get(key) or []

    for rule in rules:
        if "default" in rule:
            return Choice(
                template_code=rule["default"], because=rule.get("because", "default"),
                slot=slot, slot_name=slot_config.get("name", str(slot)),
                alternates=tuple(alternates),
            )
        if _matches(rule.get("when") or {}, facts):
            return Choice(
                template_code=rule["template"], because=rule.get("because", "rule matched"),
                slot=slot, slot_name=slot_config.get("name", str(slot)),
                alternates=tuple(alternates),
            )
    raise SelectorError(
        f"slot {slot} / stage {stage!r}: no rule matched and no default — "
        "every rule list must end with a `default`"
    )


def resolve_stage(stage: str | None, config: dict[str, Any]) -> str | None:
    """Map a target's stage onto the pack's section (e.g. `mature` → Series B+)."""
    if stage is None:
        return None
    return (config.get("stage_sections") or {}).get(stage)


def select_sequence(stage: str | None, facts: dict[str, Any]) -> list[Choice]:
    """The five touches for one target, in slot order — what intake materialises."""
    config = _cached()[1]
    return [select(slot, stage, facts, config) for slot in (1, 2, 3, 4, 5)]
