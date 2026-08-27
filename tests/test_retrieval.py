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


# --- the (id, props) tuple shape (barry-agent's 2026-08-27 V5 finding) ---------
#
# cognee's get_neighborhood hands nodes back as (uuid_str, props_dict) 2-tuples.
# _node_field handled a dict and an attribute object but not a tuple, so every
# lookup missed and the packet rendered `(unnamed)` for every node — SQL fine,
# graph fine, edges fine, display broken. These lock the shape in; the shared
# function is used by recall() and the packet path alike, so the fix is
# system-wide and so is its coverage.

# The exact shape captured from a live traversal: name is EMPTY, the real value
# is under title, the id is the tuple's first element.
_TUPLE_NODE = (
    "46070574-2b1a-4c3d-aaaa-000000000001",
    {"name": "", "type": "ContentItem",
     "url": "https://news.google.com/rss/articles/CBMiabc",
     "title": "AIIR Consulting Names Dr. Joy Nissen as Managing Partner"},
)


def test_node_field_reads_the_id_from_the_tuple_not_the_props():
    assert retrieval._node_field(_TUPLE_NODE, "id", "uuid", "node_id") == \
        "46070574-2b1a-4c3d-aaaa-000000000001"


def test_node_field_reads_other_fields_from_the_tuple_props():
    assert retrieval._node_field(_TUPLE_NODE, "type", "node_type") == "ContentItem"
    assert retrieval._node_field(_TUPLE_NODE, "source_ref", "source_url", "url") \
        == "https://news.google.com/rss/articles/CBMiabc"


def test_node_field_treats_an_empty_string_as_absent():
    """A ContentItem's name is '' with the real value under title. Without the
    empty-string fall-through the packet renders a blank line for a titled node."""
    assert retrieval._node_field(_TUPLE_NODE, "name", "title") == \
        "AIIR Consulting Names Dr. Joy Nissen as Managing Partner"
    assert retrieval._node_field({"name": "", "title": "t"}, "name", "title") == "t"


def test_a_genuine_two_tuple_of_dicts_is_not_mis_split():
    """Only (scalar_id, props_dict) is a node tuple. A (dict, dict) pair is not,
    so the guard cannot swallow legitimate two-element data."""
    node = ({"name": "first"}, {"name": "second"})
    # Treated as a plain object, not an (id, props) tuple → attribute lookup
    # finds nothing, returns None, rather than reading 'second'.
    assert retrieval._node_field(node, "name") is None


def test_normalize_nodes_renders_the_tuple_shape_end_to_end():
    """The V5 acceptance check in miniature: a real tuple node yields a real
    title and URL, not `(unnamed)`."""
    nodes, sources = retrieval.normalize_nodes(
        [_TUPLE_NODE], ("outreach_public", "content"))
    assert len(nodes) == 1
    entry = nodes[0]
    assert entry["id"] == "46070574-2b1a-4c3d-aaaa-000000000001"
    assert entry["name"].startswith("AIIR Consulting Names")
    assert entry["source_ref"] == "https://news.google.com/rss/articles/CBMiabc"
    assert sources == ("https://news.google.com/rss/articles/CBMiabc",)
    rendered = retrieval.render_nodes(nodes)
    assert "AIIR Consulting Names" in rendered
    assert "(unnamed)" not in rendered
    assert "https://news.google.com/rss/articles/CBMiabc" in rendered


def test_normalize_nodes_handles_mixed_shapes_in_one_traversal():
    """A real traversal can return tuples, dicts and objects together; every one
    must render, or a consumer loses whichever shape it did not expect."""
    class Obj:
        id = "obj-1"
        title = "Object node"
        url = "https://o/1"
        dataset = "content"

    raw = [
        _TUPLE_NODE,
        {"id": "dict-1", "name": "Dict node", "source_url": "https://d/1",
         "dataset": "content"},
        Obj(),
    ]
    nodes, _ = retrieval.normalize_nodes(raw, ("outreach_public", "content"))
    names = {n["name"] for n in nodes}
    assert len(nodes) == 3
    assert {"Object node", "Dict node"} <= names
    assert any(n["name"].startswith("AIIR Consulting") for n in nodes)


def test_h3_dataset_containment_still_holds_for_the_tuple_shape():
    """The fix must not weaken H3: a tuple node whose dataset is not permitted is
    still dropped, and one whose dataset is unknown is still kept (fail-open)."""
    permitted = ("content",)
    denied = ("x", {"name": "", "title": "secret", "dataset": "client_acme",
                    "url": "https://s/1"})
    unknown = ("y", {"name": "", "title": "keep me", "url": "https://k/1"})
    allowed = ("z", {"name": "", "title": "allowed", "dataset": "content",
                     "url": "https://a/1"})
    nodes, _ = retrieval.normalize_nodes([denied, unknown, allowed], permitted)
    titles = {n["name"] for n in nodes}
    assert "secret" not in titles          # denied dataset dropped
    assert "keep me" in titles             # unknown dataset kept (fail-open)
    assert "allowed" in titles


def test_an_empty_dataset_string_is_treated_as_unknown_not_denied():
    """A behaviour change from the fix, made explicit: an empty-string dataset is
    absent, so the node is kept (unknown), not dropped as 'dataset "" not
    permitted'. Fail-open matches the documented H3 posture."""
    node = ("w", {"name": "", "title": "blank dataset", "dataset": "",
                  "url": "https://w/1"})
    nodes, _ = retrieval.normalize_nodes([node], ("content",))
    assert len(nodes) == 1 and nodes[0]["name"] == "blank dataset"
    assert nodes[0]["dataset_known"] is False


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
