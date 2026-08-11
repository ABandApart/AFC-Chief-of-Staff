"""Unit tests for the MCP tool catalog + dispatch (Track I, Task 3).

The transport-free half: the catalog is complete and well-formed, dispatch routes
each name to the right `brain_tools` function (awaiting async ones) and rejects
unknown tools, and the JSON fallback serializes DB row types. The `mcp` SDK and
the server shim are out of scope here — validated by the Claude Code drive.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from agents._lib.brain_tools import InvocationContext, ToolError
from agents.mcp import tools

CTX = InvocationContext(caller="test", transport="mcp_stdio")

EXPECTED_TOOLS = {
    "recall", "list_open_followups", "list_pending_task_candidates", "get_prospect",
    "list_new_prospects", "spend_summary", "get_playbook", "ingest_note", "enqueue_approval",
}


def test_catalog_is_complete_and_unique():
    names = [t.name for t in tools.TOOLS]
    assert set(names) == EXPECTED_TOOLS
    assert len(names) == len(set(names)) == 9


def test_every_input_schema_is_a_closed_object():
    for t in tools.TOOLS:
        s = t.input_schema
        assert s["type"] == "object"
        assert s["additionalProperties"] is False
        assert isinstance(s["properties"], dict)


def test_dispatch_unknown_tool_raises_not_found():
    with pytest.raises(ToolError) as e:
        asyncio.run(tools.dispatch("does_not_exist", {}, CTX))
    assert e.value.code == "not_found"


def test_dispatch_routes_sync_read(mocker):
    m = mocker.patch("agents._lib.brain_tools.list_open_followups", return_value={"rows": []})
    out = asyncio.run(tools.dispatch("list_open_followups", {"limit": 5}, CTX))
    assert out == {"rows": []}
    assert m.call_args.args[0] == 5            # limit passed through
    assert m.call_args.kwargs["ctx"] is CTX    # invocation context threaded


def test_dispatch_awaits_async_recall(mocker):
    m = mocker.patch(
        "agents._lib.brain_tools.recall",
        new=AsyncMock(return_value={"answer": "A", "scope": "untrusted"}),
    )
    out = asyncio.run(tools.dispatch("recall", {"query": "q"}, CTX))
    assert out["answer"] == "A"
    assert m.await_args.args[0] == "q"


def test_dispatch_enqueue_passes_positional_args(mocker):
    m = mocker.patch(
        "agents._lib.brain_tools.enqueue_approval",
        return_value={"approval_id": 1, "status": "pending"},
    )
    asyncio.run(
        tools.dispatch(
            "enqueue_approval",
            {"item_type": "noop_echo", "summary": "s", "payload": {"x": 1}},
            CTX,
        )
    )
    a = m.call_args.args
    assert a[0] == "noop_echo" and a[1] == "s" and a[2] == {"x": 1}


def test_json_default_serializes_db_row_types():
    assert tools.json_default(dt.datetime(2026, 8, 11, 9, 0)).startswith("2026-08-11")
    assert tools.json_default(dt.date(2026, 8, 11)) == "2026-08-11"
    assert tools.json_default(Decimal("0.25")) == "0.25"
