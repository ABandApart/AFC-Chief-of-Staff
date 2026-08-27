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

import logging
from dataclasses import dataclass, field
from enum import StrEnum

from agents._lib.telemetry_context import labeled

logger = logging.getLogger(__name__)

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
    # TARGET reads the same untrusted-class background as UNTRUSTED — company
    # background, people, what a meeting said — but reaches it by walking edges
    # from a known node instead of by semantic search. It is a separate scope
    # (not an alias) because the *mechanism* differs and H3 requires an explicit
    # permitted set: the traversal must never reach playbooks (trusted
    # instructions) or, when they exist, client datasets.
    Scope.TARGET: ("capture", "granola", "content", "outreach_public"),
}

# Bounded means bounded (H4). Two hops reaches the target's people and the
# meetings/articles that mention it; three is the ceiling. An unbounded walk on a
# connected graph is a full-graph read wearing a traversal's clothes.
DEFAULT_TARGET_HOPS = 2
MAX_TARGET_HOPS = 3


@dataclass(frozen=True)
class RecallResult:
    """A retrieval answer with its provenance and the scope it was read under.

    `nodes` is populated only by `Scope.TARGET` (bounded traversal), where there
    is no synthesized answer to return — the caller renders the nodes itself.
    The completion path leaves it empty.
    """

    answer: str
    scope_used: Scope
    sources: tuple[str, ...] = field(default_factory=tuple)
    nodes: tuple[dict[str, object], ...] = field(default_factory=tuple)


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


# The node id lives outside the props for the tuple shape, so id lookups resolve
# to the tuple's first element rather than the props dict.
_ID_NAMES = frozenset({"id", "uuid", "node_id"})


def _split_node(node: object) -> tuple[object, object]:
    """Return `(node_id, props)` for the node shapes cognee's adapters return.

    Three shapes reach here, and the packet's rendering must not depend on which:

      * `(uuid, props_dict)` 2-tuple — cognee's `get_neighborhood`. The id is the
        first element, every other field is in the props dict. This is the shape
        that shipped rendering `(unnamed)`: `_node_field` handled a dict and an
        attribute object but not a tuple, so every lookup missed.
      * a plain dict — other search paths.
      * an attribute object.

    A 2-tuple only counts as `(id, props)` when its second element is a dict and
    its first is a scalar id, so a genuine `(dict, dict)` pair is never mis-split.
    """
    if (
        isinstance(node, tuple)
        and len(node) == 2
        and isinstance(node[1], dict)
        and not isinstance(node[0], (dict, list, tuple))
    ):
        return (node[0], node[1])
    return (None, node)


def _lookup(props: object, name: str) -> object:
    if isinstance(props, dict):
        return props.get(name)
    return getattr(props, name, None)


def _node_field(node: object, *names: str) -> object:
    """First present value among `names`, across every node shape cognee returns.

    **An empty string counts as absent, not as a hit**, so a candidate that is
    present-but-blank falls through to the next name. cognee gives a `ContentItem`
    an empty `name` with the real value under `title`; without this,
    `_node_field(node, "name", "title")` returns `''` and the packet renders a
    blank line for a node that has a perfectly good title. Pure — unit-tested,
    including the tuple shape that shipped this bug.
    """
    node_id, props = _split_node(node)
    for name in names:
        if name in _ID_NAMES and node_id is not None:
            return node_id
        value = _lookup(props, name)
        if value is not None and value != "":
            return value
    return None


def normalize_nodes(
    raw_nodes: object, permitted: tuple[str, ...]
) -> tuple[tuple[dict[str, object], ...], tuple[str, ...]]:
    """Map raw traversal nodes → (display dicts, source refs), dropping any node
    whose dataset is known and not permitted (H3). Pure — unit-tested.

    **A node whose dataset cannot be determined is kept, not dropped.** Dropping
    would empty the packet on every provider whose nodes omit the field, turning
    a containment control into an outage. The `dataset_known` flag rides along on
    each node so the caller can see the difference, and `recall()` logs a warning
    when nothing carries dataset metadata — which is the signal to revisit this
    before any client dataset exists. There are no client datasets today; this is
    the control being staged ahead of the risk, not a hole being left open.
    """
    if not raw_nodes:
        return (), ()
    if not isinstance(raw_nodes, list | tuple):
        raw_nodes = [raw_nodes]

    kept: list[dict[str, object]] = []
    sources: list[str] = []
    for node in raw_nodes:
        dataset = _node_field(node, "dataset", "dataset_name", "belongs_to_set")
        if dataset is not None and str(dataset) not in permitted:
            continue
        source = _node_field(node, "source_ref", "source_url", "url")
        entry: dict[str, object] = {
            "id": str(_node_field(node, "id", "uuid", "node_id") or ""),
            "type": str(_node_field(node, "type", "node_type", "label") or ""),
            "name": str(_node_field(node, "name", "title") or ""),
            "text": str(_node_field(node, "text", "summary", "description") or ""),
            "source_ref": str(source) if source is not None else "",
            "dataset_known": dataset is not None,
        }
        kept.append(entry)
        if source is not None and str(source) not in sources:
            sources.append(str(source))
    return tuple(kept), tuple(sources)


def render_nodes(nodes: tuple[dict[str, object], ...]) -> str:
    """Render traversal nodes as read-only display text (pure).

    Deliberately plain: this is background context a human skims beside the
    evidence, not prose to copy. No synthesis happens anywhere in this path.
    """
    if not nodes:
        return NO_RESULT
    lines: list[str] = []
    for n in nodes:
        label = n.get("name") or n.get("id") or "(unnamed)"
        kind = f" [{n['type']}]" if n.get("type") else ""
        line = f"- {label}{kind}"
        if text := str(n.get("text") or "").strip():
            line += f": {text}"
        if ref := str(n.get("source_ref") or "").strip():
            line += f" ({ref})"
        lines.append(line)
    return "\n".join(lines)


async def _bounded_traversal(root_node_id: str, hops: int) -> tuple[object, object]:
    """The one place the graph engine is walked directly — isolated for the same
    reason `_graph_completion` is: one legal call site, unit-testable orchestration.

    Deliberately NOT `cognee.search`. Its `neighborhood_depth` option routes
    through `brute_force_triplet_search`, which requires vector-search seed ids
    and ends in an LLM completion — the exact coupling H4 forbids. The graph
    engine's `get_neighborhood` is a recursive CTE over the node/edge tables:
    no embedding query, no synthesis, no provider call, no ledger row.

    cognee is imported lazily (optional dep). `configure_cognee()` must have run.
    """
    from cognee.infrastructure.databases.graph.get_graph_engine import get_graph_engine

    engine = await get_graph_engine()
    return await engine.get_neighborhood([str(root_node_id)], depth=hops)


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

    `Scope.TARGET` is the Track O outreach-packet path: a bounded N-hop traversal
    from `root_node_id` with no embedding query, no synthesis, and no LLM call
    (§7 / H4). It ignores `query`, requires `root_node_id`, and returns the
    retrieved nodes in `RecallResult.nodes` with `answer` set to a plain
    rendering of them — the caller displays them, nothing writes prose.
    """
    if scope is Scope.TARGET:
        if not root_node_id:
            raise ValueError("Scope.TARGET requires root_node_id (the traversal root).")
        depth = DEFAULT_TARGET_HOPS if hops is None else hops
        if depth < 1 or depth > MAX_TARGET_HOPS:
            raise ValueError(
                f"hops must be between 1 and {MAX_TARGET_HOPS} for Scope.TARGET "
                f"(got {depth}) — an unbounded walk is a full-graph read."
            )
        # Labeled like any other retrieval so the call is visible in the ledger's
        # shape, even though a traversal spends nothing (no provider call).
        with labeled(agent, "infrastructure", trigger_kind=trigger_kind):
            raw_nodes, _edges = await _bounded_traversal(root_node_id, depth)
        nodes, sources = normalize_nodes(raw_nodes, DATASETS[Scope.TARGET])
        if nodes and not any(n["dataset_known"] for n in nodes):
            logger.warning(
                "retrieval: traversal from %s returned %d node(s) with no dataset "
                "metadata — H3 dataset filtering is inert on this provider. Revisit "
                "before any client dataset exists.",
                root_node_id, len(nodes),
            )
        return RecallResult(
            answer=render_nodes(nodes),
            scope_used=scope,
            sources=sources,
            nodes=nodes,
        )

    datasets = DATASETS[scope]
    if not datasets:
        # Defensive: a scope with no datasets would fall through to a full-graph
        # search — exactly the B1 hole this wrapper exists to close.
        raise ValueError(f"scope {scope!r} resolves to no datasets; refusing to search all.")

    with labeled(agent, "infrastructure", trigger_kind=trigger_kind):
        results = await _graph_completion(query, datasets)
    return RecallResult(answer=normalize_answer(results), scope_used=scope)
