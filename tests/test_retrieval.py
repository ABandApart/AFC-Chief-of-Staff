"""Unit tests for the scoped retrieval wrapper (Phase 3.8 / B1 read-side).

The guarantees that matter are structural: the default scope is the *closed*
one, scopes never union, `agent` is mandatory, a scope is always resolved to a
concrete non-empty dataset list (never a full-graph search), and Track O's
TARGET traversal stays bounded and never reaches the completion path. The cognee
calls and the telemetry context are mocked — no cognee, no DB.
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import contextmanager

import pytest

from agents._lib import retrieval
from agents._lib.retrieval import Scope


@contextmanager
def _fake_labeled(*a, **k):
    yield "run-test"


@pytest.fixture
def gc(mocker):
    """Patch the single cognee call site + the telemetry context (no cognee/DB)."""
    from unittest.mock import AsyncMock

    mocker.patch.object(retrieval, "labeled", _fake_labeled)
    m = AsyncMock(return_value="the synthesized answer")
    mocker.patch.object(retrieval, "_graph_completion", m)
    return m


# --- structure: closed default, no union, disjoint datasets -----------------


def test_default_scope_is_untrusted():
    # A forgotten scope= must yield the LESS privileged scope, not more.
    assert inspect.signature(retrieval.recall).parameters["scope"].default is Scope.UNTRUSTED


def test_scopes_are_exactly_three_no_union_member():
    # Mixing trusted+untrusted must be unrepresentable — no combined enum member.
    assert set(Scope) == {Scope.UNTRUSTED, Scope.TRUSTED, Scope.TARGET}


def test_untrusted_and_trusted_datasets_are_disjoint():
    u = set(retrieval.DATASETS[Scope.UNTRUSTED])
    t = set(retrieval.DATASETS[Scope.TRUSTED])
    assert u and t and u.isdisjoint(t)
    assert retrieval.DATASETS[Scope.TRUSTED] == ("playbooks",)


# --- recall() behavior ------------------------------------------------------


def test_agent_is_required():
    # Omitting agent is a synchronous TypeError (keyword-only, no default).
    with pytest.raises(TypeError):
        retrieval.recall("q")  # type: ignore[call-arg]


def test_recall_defaults_to_untrusted_datasets(gc):
    res = asyncio.run(retrieval.recall("what do we know about X?", agent="tartt"))
    assert res.scope_used is Scope.UNTRUSTED
    assert res.answer == "the synthesized answer"
    query, datasets = gc.await_args.args
    assert query == "what do we know about X?"
    assert datasets == ("capture", "granola", "content", "outreach_public")


def test_recall_trusted_scopes_to_playbooks_only(gc):
    res = asyncio.run(retrieval.recall("which playbook?", scope=Scope.TRUSTED, agent="keeley"))
    assert res.scope_used is Scope.TRUSTED
    assert gc.await_args.args[1] == ("playbooks",)


def test_recall_returns_recallresult_with_provenance_fields(gc):
    res = asyncio.run(retrieval.recall("q", agent="a"))
    assert isinstance(res, retrieval.RecallResult)
    assert res.answer and res.scope_used is Scope.UNTRUSTED and res.sources == ()


def test_empty_dataset_scope_refuses_rather_than_searching_all(gc, mocker):
    # Defensive guard: a misconfigured scope with no datasets must NOT fall
    # through to a full-graph search (the exact B1 hole this wrapper closes).
    mocker.patch.dict(retrieval.DATASETS, {Scope.TRUSTED: ()})
    with pytest.raises(ValueError, match="no datasets"):
        asyncio.run(retrieval.recall("q", scope=Scope.TRUSTED, agent="a"))


# --- Scope.TARGET: bounded traversal (Track O, 35- §7 / H4) ------------------


@pytest.fixture
def bt(mocker):
    """Patch the traversal call site + telemetry context (no cognee, no DB)."""
    from unittest.mock import AsyncMock

    mocker.patch.object(retrieval, "labeled", _fake_labeled)
    nodes = [
        {"id": "n1", "name": "Cadence Health", "type": "Organization",
         "dataset": "outreach_public", "source_url": "https://cadence.health/careers"},
        {"id": "n2", "name": "Marcus Oyelaran", "type": "Person", "dataset": "granola"},
    ]
    m = AsyncMock(return_value=(nodes, []))
    mocker.patch.object(retrieval, "_bounded_traversal", m)
    return m


def _target(**kw):
    kw.setdefault("agent", "outreach")
    kw.setdefault("root_node_id", "root-1")
    return asyncio.run(retrieval.recall("", scope=Scope.TARGET, **kw))


def test_target_traversal_never_calls_the_completion_path(gc, bt):
    # The whole point of H4: no embedding query, no synthesis, no LLM.
    res = _target()
    bt.assert_awaited_once()
    gc.assert_not_awaited()
    assert res.scope_used is Scope.TARGET


def test_target_returns_nodes_and_source_refs(bt):
    res = _target()
    assert [n["name"] for n in res.nodes] == ["Cadence Health", "Marcus Oyelaran"]
    assert res.sources == ("https://cadence.health/careers",)
    assert "Cadence Health" in res.answer  # rendered for display, not synthesized


def test_target_requires_a_root_node_id(bt):
    with pytest.raises(ValueError, match="root_node_id"):
        asyncio.run(retrieval.recall("", scope=Scope.TARGET, agent="a", root_node_id=None))


def test_target_defaults_to_bounded_hops(bt):
    _target()
    assert bt.await_args.args[1] == retrieval.DEFAULT_TARGET_HOPS


@pytest.mark.parametrize("bad", [0, -1, retrieval.MAX_TARGET_HOPS + 1])
def test_target_refuses_unbounded_or_nonsense_hops(bt, bad):
    # An unbounded walk on a connected graph is a full-graph read in disguise.
    with pytest.raises(ValueError, match="hops"):
        _target(hops=bad)
    bt.assert_not_awaited()


def test_target_never_reaches_playbooks(bt):
    # H3: the traversal must not pull trusted instruction content into a packet.
    assert "playbooks" not in retrieval.DATASETS[Scope.TARGET]


def test_target_drops_nodes_from_non_permitted_datasets(mocker):
    from unittest.mock import AsyncMock

    mocker.patch.object(retrieval, "labeled", _fake_labeled)
    mocker.patch.object(retrieval, "_bounded_traversal", AsyncMock(return_value=([
        {"id": "ok", "name": "Public", "dataset": "outreach_public"},
        {"id": "no", "name": "Client Secret", "dataset": "client_acme"},
        {"id": "pb", "name": "A Playbook", "dataset": "playbooks"},
    ], [])))
    res = _target()
    assert [n["id"] for n in res.nodes] == ["ok"]


def test_target_keeps_nodes_whose_dataset_is_unknown(mocker):
    # Fail-open is deliberate and documented: dropping unlabeled nodes would
    # empty every packet on a provider that omits the field. The flag records it.
    from unittest.mock import AsyncMock

    mocker.patch.object(retrieval, "labeled", _fake_labeled)
    mocker.patch.object(retrieval, "_bounded_traversal", AsyncMock(return_value=([
        {"id": "n1", "name": "No dataset field"},
    ], [])))
    res = _target()
    assert [n["id"] for n in res.nodes] == ["n1"]
    assert res.nodes[0]["dataset_known"] is False


def test_target_empty_traversal_renders_no_result(mocker):
    from unittest.mock import AsyncMock

    mocker.patch.object(retrieval, "labeled", _fake_labeled)
    mocker.patch.object(retrieval, "_bounded_traversal", AsyncMock(return_value=([], [])))
    res = _target()
    assert res.nodes == () and res.answer == retrieval.NO_RESULT


# --- node normalization / rendering (pure) -----------------------------------


def test_node_field_reads_dicts_and_objects():
    class N:
        name = "obj-name"

    assert retrieval._node_field({"name": "dict-name"}, "name") == "dict-name"
    assert retrieval._node_field(N(), "name") == "obj-name"
    assert retrieval._node_field({}, "missing") is None
    # Falls through a present-but-None first choice to the next candidate.
    assert retrieval._node_field({"name": None, "title": "t"}, "name", "title") == "t"


def test_normalize_nodes_dedups_source_refs():
    raw = [
        {"id": "a", "source_url": "https://x/1", "dataset": "granola"},
        {"id": "b", "source_url": "https://x/1", "dataset": "granola"},
    ]
    nodes, sources = retrieval.normalize_nodes(raw, ("granola",))
    assert len(nodes) == 2 and sources == ("https://x/1",)


def test_render_nodes_is_plain_and_includes_provenance():
    out = retrieval.render_nodes((
        {"id": "n1", "name": "Acme", "type": "Organization",
         "text": "a fintech", "source_ref": "https://acme.com"},
    ))
    assert out == "- Acme [Organization]: a fintech (https://acme.com)"


def test_render_nodes_empty_is_no_result():
    assert retrieval.render_nodes(()) == retrieval.NO_RESULT
