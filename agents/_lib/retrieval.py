"""Scoped retrieval wrapper — B1 as a mechanism, not a convention (Phase 3.8).

B1's teeth depend on agents *remembering* to scope their cognee searches. One
forgotten dataset argument silently mixes untrusted `capture`/`granola` content
into a trusted-playbook retrieval, and the failure looks exactly like a good
answer. This system already solved this shape once — agents never import a
provider SDK; every LLM call goes through the cost helper — which made spend
tracking structural instead of aspirational. Retrieval gets the same treatment:

> **Rule: agents do not call `cognee.search` / `cognee.SearchType` directly.
> All retrieval goes through this module.** Enforced by `tests/test_no_raw_retrieval.py`
> (a build-failing grep), the same way the SDK-import rule is enforced.

Design (see `30-memory-layer.md` §retrieval_wrapper):
  - **Defaults closed to `UNTRUSTED`** — a forgotten `scope=` yields a *weaker*
    answer, never a boundary crossing. Wanting playbooks means saying `TRUSTED`,
    a word that greps in review.
  - **Scopes never union** — there is no `TRUSTED | UNTRUSTED`; mixing is the
    failure being prevented, so it is unrepresentable. An agent needing both makes
    two calls and keeps the results distinguishable (which it also needs for
    provenance).
  - **`agent` is required** — it sets the `labeled()` telemetry context so
    retrieval spend attributes correctly (the gap M1 closed for cognify).
  - **Returns `RecallResult`**, not a bare string — `answer` + `sources` +
    `scope_used`, so callers get provenance by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from agents._lib.telemetry_context import labeled

NO_RESULT = "No matching facts."


class Scope(StrEnum):
    """The retrieval trust scopes. UNTRUSTED is the closed default."""

    UNTRUSTED = "untrusted"  # capture, granola, outreach_public  ← DEFAULT
    TRUSTED = "trusted"      # playbooks only
    TARGET = "target"        # bounded traversal from one node id (Track O)


# scope → the exact cognee datasets it may read. Never unioned across scopes.
DATASETS: dict[Scope, tuple[str, ...]] = {
    # All untrusted ingest channels — capture (Discord), granola (meetings),
    # content (Tartt news, Phase 4), outreach_public (scraped company/job text).
    Scope.UNTRUSTED: ("capture", "granola", "content", "outreach_public"),
    Scope.TRUSTED: ("playbooks",),
    Scope.TARGET: (),  # resolved from the root node's own dataset, not a fixed set
}


@dataclass(frozen=True)
class RecallResult:
    """A retrieval answer with its provenance and the scope it was read under."""

    answer: str
    scope_used: Scope
    sources: tuple[str, ...] = field(default_factory=tuple)


def normalize_answer(results: object) -> str:
    """Map cognee.search output (str / list / None / other) to a display string.

    Pure — unit-tested. Empty/whitespace-only output collapses to NO_RESULT.
    """
    if results is None:
        return NO_RESULT
    if isinstance(results, str):
        text = results.strip()
    elif isinstance(results, list | tuple):
        text = "\n\n".join(s for r in results if (s := str(r).strip()))
    else:
        text = str(results).strip()
    return text or NO_RESULT


async def _graph_completion(query: str, datasets: tuple[str, ...]) -> object:
    """The one place `cognee.search` is called — isolated so the orchestration in
    `recall()` is unit-testable without cognee installed, and so the CI grep has a
    single legal call site. `datasets` scopes the search to those datasets only;
    an empty tuple would search everything, so callers must never pass one here.

    cognee is imported lazily (optional dep). `configure_cognee()` must have run.
    """
    import cognee
    from cognee import SearchType

    return await cognee.search(
        query_type=SearchType.GRAPH_COMPLETION,
        query_text=query,
        datasets=list(datasets),
    )


async def recall(
    query: str,
    *,
    scope: Scope = Scope.UNTRUSTED,
    agent: str,
    hops: int | None = None,
    root_node_id: str | None = None,
    trigger_kind: str = "event",
) -> RecallResult:
    """Answer a recall query from the graph, scoped to `scope`'s datasets.

    `agent` (required) labels the telemetry so retrieval spend attributes to the
    caller. Returns a `RecallResult` carrying the synthesized answer, its sources,
    and the scope actually used.

    `Scope.TARGET` (bounded N-hop traversal from `root_node_id`, no embedding
    query, no synthesis) is the Track O outreach-packet path; it is not built yet
    and raises until Track O lands.
    """
    if scope is Scope.TARGET:
        raise NotImplementedError(
            "Scope.TARGET (bounded traversal) is Track O — not implemented yet "
            f"(root_node_id={root_node_id!r}, hops={hops!r})."
        )

    datasets = DATASETS[scope]
    if not datasets:
        # Defensive: a scope with no datasets would fall through to a full-graph
        # search — exactly the B1 hole this wrapper exists to close.
        raise ValueError(f"scope {scope!r} resolves to no datasets; refusing to search all.")

    with labeled(agent, "infrastructure", trigger_kind=trigger_kind):
        results = await _graph_completion(query, datasets)
    return RecallResult(answer=normalize_answer(results), scope_used=scope)
