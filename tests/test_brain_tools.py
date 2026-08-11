"""Unit tests for the gated core (`brain_tools`, Track I Task 2).

What matters is the gates, not the DB/cognee: scope mapping + closed set,
bound clamping, RO reads shaped through the views, `source_type='tool'` pinning,
`enqueue_approval` rejecting unregistered item_types, path-traversal refusal on
`get_playbook`, and an audit row on every call (ok and error). All I/O (RO query,
retrieval, ingest, approvals, audit write) is mocked — no DB, no cognee.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from agents._lib import brain_tools as bt
from agents._lib import retrieval

CTX = bt.InvocationContext(caller="test", transport="mcp_stdio")


@pytest.fixture(autouse=True)
def no_audit_db(mocker):
    """Capture audit writes instead of hitting the DB (every test needs this)."""
    return mocker.patch.object(bt, "_write_audit")


@pytest.fixture
def ro(mocker):
    return mocker.patch.object(bt, "_ro_query", return_value=[])


# --- recall (scope gate) ----------------------------------------------------


def _recall_mock(mocker, scope):
    result = retrieval.RecallResult(answer="A", scope_used=scope)
    return mocker.patch.object(bt.retrieval, "recall", new=AsyncMock(return_value=result))


def test_recall_maps_playbooks_scope_to_trusted(mocker):
    rec = _recall_mock(mocker, retrieval.Scope.TRUSTED)
    out = asyncio.run(bt.recall("q", scope="playbooks", ctx=CTX))
    assert out == {"answer": "A", "scope": "playbooks"}
    assert rec.await_args.kwargs["scope"] is retrieval.Scope.TRUSTED
    assert rec.await_args.kwargs["trigger_kind"] == "interactive"


def test_recall_defaults_untrusted(mocker):
    rec = _recall_mock(mocker, retrieval.Scope.UNTRUSTED)
    asyncio.run(bt.recall("q", ctx=CTX))
    assert rec.await_args.kwargs["scope"] is retrieval.Scope.UNTRUSTED


def test_recall_rejects_unknown_scope():
    with pytest.raises(bt.ToolError) as e:
        asyncio.run(bt.recall("q", scope="trusted", ctx=CTX))  # not an exposed name
    assert e.value.code == "schema"


# --- read bounds ------------------------------------------------------------


def test_list_open_followups_clamps_limit_to_max(ro):
    bt.list_open_followups(limit=1000, ctx=CTX)
    assert ro.call_args.args[1] == (bt.MAX_LIMIT,)


def test_list_new_prospects_clamps_since_hours_and_limit(ro):
    bt.list_new_prospects(since_hours=99999, limit=0, ctx=CTX)
    hours, limit = ro.call_args.args[1]
    assert hours == bt.MAX_SINCE_HOURS and limit == 1  # limit floored to 1


def test_get_prospect_not_found_raises(ro):
    with pytest.raises(bt.ToolError) as e:
        bt.get_prospect(42, ctx=CTX)
    assert e.value.code == "not_found"


def test_spend_summary_rejects_bad_window():
    with pytest.raises(bt.ToolError) as e:
        bt.spend_summary("all-time", ctx=CTX)
    assert e.value.code == "schema"


def test_spend_summary_totals_by_function(mocker):
    mocker.patch.object(
        bt, "_ro_query",
        return_value=[{"function_label": "recall", "calls": 3, "usd": "0.20"},
                      {"function_label": "cognify", "calls": 1, "usd": "0.05"}],
    )
    out = bt.spend_summary("7d", ctx=CTX)
    assert out["total"] == "0.25" and len(out["by_function"]) == 2


# --- get_playbook (B4 read, traversal guard) --------------------------------


def test_get_playbook_reads_a_real_trusted_file():
    out = bt.get_playbook("daily-briefing", ctx=CTX)  # exists in playbooks/
    assert out["name"] == "daily-briefing" and out["text"]


def test_get_playbook_rejects_path_traversal():
    for bad in ("../secrets", "a/b", "..", "Foo", "x.y"):
        with pytest.raises(bt.ToolError) as e:
            bt.get_playbook(bad, ctx=CTX)
        assert e.value.code in {"bad_request", "not_found"}


# --- ingest_note (one-way; source_type pinned) ------------------------------


def test_ingest_note_pins_source_type_tool_and_maps_status(mocker):
    ing = mocker.patch.object(bt.ingest, "ingest_note", new=AsyncMock(return_value="captured"))
    out = asyncio.run(bt.ingest_note("a real note", "ref-1", ctx=CTX))
    assert out["status"] == "ingested" and len(out["note_hash"]) == 64
    assert ing.await_args.kwargs["source_type"] == "tool"


def test_ingest_note_duplicate_maps_to_duplicate(mocker):
    mocker.patch.object(bt.ingest, "ingest_note", new=AsyncMock(return_value="repost"))
    out = asyncio.run(bt.ingest_note("dup", "ref-1", ctx=CTX))
    assert out["status"] == "duplicate"


def test_ingest_note_too_large_rejected():
    with pytest.raises(bt.ToolError) as e:
        asyncio.run(bt.ingest_note("x" * (bt.INGEST_MAX_BYTES + 1), "ref", ctx=CTX))
    assert e.value.code == "too_large"


# --- enqueue_approval (B2 gate) ---------------------------------------------


def test_enqueue_approval_rejects_unregistered_item_type():
    with pytest.raises(bt.ToolError) as e:
        bt.enqueue_approval("definitely_not_registered", "s", {}, ctx=CTX)
    assert e.value.code == "bad_request"


def test_enqueue_approval_registered_type_enqueues(mocker):
    # noop_echo is registered by agents._lib.approvals at import.
    req = mocker.patch.object(bt.approvals, "request_approval", return_value=7)
    out = bt.enqueue_approval("noop_echo", "do the thing", {"text": "hi"}, ctx=CTX)
    assert out == {"approval_id": 7, "status": "pending"}
    assert req.call_args.kwargs["item_type"] == "noop_echo"


# --- audit is always written ------------------------------------------------


def test_audit_row_written_on_success(no_audit_db, ro):
    bt.list_open_followups(ctx=CTX)
    assert no_audit_db.call_args.args[3] == "ok"  # outcome


def test_audit_row_written_on_error(no_audit_db):
    with pytest.raises(bt.ToolError):
        bt.spend_summary("nope", ctx=CTX)
    # outcome='error', error_code carries the ToolError code.
    assert no_audit_db.call_args.args[3] == "error"
    assert no_audit_db.call_args.args[4] == "schema"
