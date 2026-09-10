"""Separation invariant: `#tasks` shows only content-accepted work.

The `#tasks` checklist renders `tasks WHERE status='open'`. That surface stays
free of outreach items *by construction* only if the sole writer of `tasks` rows
is the Task Tinder accept path (`agents/_lib/task_tinder.promote`) — outreach
state lives in `outreach_touches` / `outreach_targets`, never in `tasks`. This
grep-guard fails if any other module learns to insert `tasks`, so a future
outreach (or other) path cannot silently leak into `#tasks`.
"""
from __future__ import annotations

import re
from pathlib import Path

_AGENTS = Path(__file__).resolve().parents[1] / "agents"
# `INSERT INTO tasks` but not `task_candidates` / `tasks_*` — a word boundary.
_INSERT = re.compile(r"INSERT\s+INTO\s+tasks\b", re.IGNORECASE)


def test_only_task_tinder_promote_inserts_tasks():
    offenders = []
    for path in _AGENTS.rglob("*.py"):
        if _INSERT.search(path.read_text(encoding="utf-8")):
            offenders.append(path.relative_to(_AGENTS.parent).as_posix())
    assert offenders == ["agents/_lib/task_tinder.py"], (
        "tasks rows must be inserted only by task_tinder.promote so #tasks cannot "
        f"leak non-content work; also insert(s) found in: {offenders}"
    )
