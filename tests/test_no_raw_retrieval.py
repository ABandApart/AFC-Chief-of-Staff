"""B1 read-side enforcement: no raw `cognee.search` outside the wrapper.

The scoped retrieval rule (Phase 3.8, `30-memory-layer.md` §retrieval_wrapper) is
only real if it's checked. This fails the build if `cognee.search` or
`cognee.SearchType` appears anywhere under `agents/` or `cli/` except the one
legal call site (`agents/_lib/retrieval.py`) — the same shape as the existing
SDK-import rule. Pure Python (no `rg` dependency) so it runs anywhere the suite does.

`agents/test/` is excluded: those are cognee *diagnostic* probes (smoke /
ontology-shape harnesses) that intentionally exercise cognee directly and are not
agent retrieval paths.
"""

from __future__ import annotations

import re
from pathlib import Path

_PATTERN = re.compile(r"cognee\.(search|SearchType)")
_ROOT = Path(__file__).resolve().parent.parent
_SEARCH_DIRS = ("agents", "cli")
_ALLOW_FILES = {_ROOT / "agents" / "_lib" / "retrieval.py"}
_ALLOW_DIRS = (_ROOT / "agents" / "test",)


def _offenders() -> list[str]:
    hits: list[str] = []
    for d in _SEARCH_DIRS:
        base = _ROOT / d
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            if py in _ALLOW_FILES or any(ad in py.parents for ad in _ALLOW_DIRS):
                continue
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if _PATTERN.search(line):
                    hits.append(f"{py.relative_to(_ROOT)}:{i}: {line.strip()}")
    return hits


def test_no_raw_cognee_search_outside_wrapper():
    offenders = _offenders()
    assert not offenders, (
        "Raw cognee.search / SearchType found outside agents/_lib/retrieval.py. "
        "Route retrieval through agents/_lib/retrieval.recall() (B1 scoping). "
        "Offenders:\n  " + "\n  ".join(offenders)
    )
