"""Control-plane loader — parse and validate loop and playbook manifests.

The control plane is the set of authored, git-tracked instruction files
(skills, loops, playbooks; see `architecture/25-target-state.md`). This module
reads the machine-consumed ones — `loops/*.md` and `playbooks/*.md` — validates
them against their schemas, and exposes typed accessors. The scheduler daemon
(next Track-A step) consumes `discover().loops`; `cli/control.py` exposes
`list` / `validate`.

Skills (`.claude/skills/`) are loaded by the harness, not here.

Parsing is deliberately strict: a control-plane file is trusted and executed, so
a malformed one should fail loudly at validate time (CI), not silently at run
time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# 5-field cron: minute hour day-of-month month day-of-week. Permissive token
# charset (numbers, *, /, ,, -) — enough to catch shape errors without
# reimplementing a full cron validator.
_CRON_FIELD = r"[0-9*/,\-]+"
_CRON_RE = re.compile(rf"^{_CRON_FIELD}( {_CRON_FIELD}){{4}}$")


class ControlPlaneError(ValueError):
    """A manifest failed to parse or violated its schema."""


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split a `---` YAML frontmatter block from its markdown body.

    Returns (metadata, body). Raises ControlPlaneError if the frontmatter fence
    is missing or the YAML is not a mapping.
    """
    if not text.startswith("---"):
        raise ControlPlaneError("missing '---' frontmatter fence")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ControlPlaneError("unterminated frontmatter (need a closing '---')")
    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError as e:
        raise ControlPlaneError(f"invalid YAML frontmatter: {e}") from e
    if meta is None:
        meta = {}
    if not isinstance(meta, dict):
        raise ControlPlaneError("frontmatter is not a mapping")
    return meta, parts[2].lstrip("\n")


@dataclass
class Playbook:
    name: str
    description: str
    applies_to: list[str] = field(default_factory=list)
    publish_to_memory: bool = False
    tags: list[str] = field(default_factory=list)
    body: str = ""
    path: Path | None = None


@dataclass
class Loop:
    name: str
    schedule: str
    enabled: bool
    trigger_kind: str = "scheduled"
    agent: str | None = None
    playbook: str | None = None
    command: str | None = None
    description: str = ""
    path: Path | None = None

    @property
    def target(self) -> str:
        """Human-readable one-liner of what the loop runs."""
        if self.command:
            return f"command: {self.command}"
        pb = f" → {self.playbook}" if self.playbook else ""
        return f"agent: {self.agent}{pb}"


def load_playbook(path: Path) -> Playbook:
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    name = meta.get("name")
    if not name:
        raise ControlPlaneError(f"{path.name}: missing 'name'")
    if name != path.stem:
        raise ControlPlaneError(f"{path.name}: name '{name}' != filename stem '{path.stem}'")
    desc = meta.get("description")
    if not desc or not isinstance(desc, str):
        raise ControlPlaneError(f"{path.name}: missing/invalid 'description'")
    publish = meta.get("publish_to_memory", False)
    if not isinstance(publish, bool):
        raise ControlPlaneError(f"{path.name}: 'publish_to_memory' must be a boolean")
    applies_to = meta.get("applies_to", []) or []
    tags = meta.get("tags", []) or []
    if not isinstance(applies_to, list) or not isinstance(tags, list):
        raise ControlPlaneError(f"{path.name}: 'applies_to' and 'tags' must be lists")
    return Playbook(
        name=name, description=desc, applies_to=list(applies_to),
        publish_to_memory=publish, tags=list(tags), body=body, path=path,
    )


def load_loop(path: Path, known_playbooks: set[str] | None = None) -> Loop:
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    name = meta.get("name")
    if not name:
        raise ControlPlaneError(f"{path.name}: missing 'name'")
    if name != path.stem:
        raise ControlPlaneError(f"{path.name}: name '{name}' != filename stem '{path.stem}'")

    schedule = meta.get("schedule")
    if not schedule or not isinstance(schedule, str) or not _CRON_RE.match(schedule.strip()):
        raise ControlPlaneError(f"{path.name}: 'schedule' must be a 5-field cron string")

    enabled = meta.get("enabled")
    if not isinstance(enabled, bool):
        raise ControlPlaneError(f"{path.name}: 'enabled' must be a boolean")

    agent = meta.get("agent")
    command = meta.get("command")
    playbook = meta.get("playbook")
    if bool(agent) == bool(command):
        raise ControlPlaneError(
            f"{path.name}: exactly one of 'agent' or 'command' is required"
        )
    if command and playbook:
        raise ControlPlaneError(f"{path.name}: 'command' loops cannot reference a 'playbook'")

    # A referenced playbook must exist — but only enforce for enabled loops, so a
    # disabled placeholder for a future agent/playbook doesn't fail validation.
    if playbook and enabled and known_playbooks is not None and playbook not in known_playbooks:
        raise ControlPlaneError(
            f"{path.name}: references playbook '{playbook}' which does not exist"
        )

    return Loop(
        name=name, schedule=schedule.strip(), enabled=enabled,
        trigger_kind=meta.get("trigger_kind", "scheduled"),
        agent=agent, playbook=playbook, command=command,
        description=meta.get("description", ""), path=path,
    )


@dataclass
class ControlPlane:
    loops: list[Loop] = field(default_factory=list)
    playbooks: list[Playbook] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def enabled_loops(self) -> list[Loop]:
        return [lp for lp in self.loops if lp.enabled]


def repo_root() -> Path:
    """Repo root, inferred from this file's location (agents/_lib/…)."""
    return Path(__file__).resolve().parents[2]


def discover(root: Path | None = None) -> ControlPlane:
    """Load and validate all playbooks then loops under `root`.

    Never raises for a bad manifest — collects one error string per bad file so
    the CLI can report them all. Parse errors on one file don't hide the rest.
    """
    root = root or repo_root()
    cp = ControlPlane()

    pb_dir = root / "playbooks"
    for path in sorted(pb_dir.glob("*.md")) if pb_dir.is_dir() else []:
        if path.name == "README.md":
            continue
        try:
            cp.playbooks.append(load_playbook(path))
        except ControlPlaneError as e:
            cp.errors.append(str(e))

    known = {pb.name for pb in cp.playbooks}
    loop_dir = root / "loops"
    for path in sorted(loop_dir.glob("*.md")) if loop_dir.is_dir() else []:
        if path.name == "README.md":
            continue
        try:
            cp.loops.append(load_loop(path, known_playbooks=known))
        except ControlPlaneError as e:
            cp.errors.append(str(e))

    return cp
