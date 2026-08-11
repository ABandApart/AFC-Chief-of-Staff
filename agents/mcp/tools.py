"""Tool catalog + dispatch for the MCP tool layer (Track I, Task 3).

This module is the **transport-independent** half of the stdio MCP server: the
tool names, their JSON-Schema `inputSchema`s, and a `dispatch()` that routes a
`(name, arguments)` call to the matching `brain_tools` function. It imports **no
`mcp` SDK** — so the catalog and its routing are unit-tested in the default env,
and only the thin shim (`agents/mcp/server.py`) depends on the SDK.

Arg handling is deliberately lenient here (`arguments.get(...)`); the real
validation lives in `brain_tools` (bounds, enums, `ToolError`s), so there is one
source of truth for the gates regardless of transport. Missing required args
therefore surface as the same `ToolError('schema', …)` a bad value would.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from agents._lib import brain_tools
from agents._lib.brain_tools import InvocationContext, ToolError

# A handler adapts a plain args dict to a brain_tools call. It may return a dict
# or a coroutine (async tools); `dispatch` awaits the latter.
Handler = Callable[[dict, InvocationContext], "dict | Awaitable[dict]"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Handler


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


_LIMIT = {"type": "integer", "minimum": 1, "maximum": 25, "default": 10}

TOOLS: list[ToolSpec] = [
    ToolSpec(
        "recall",
        "Graph recall (synthesized answer). scope 'untrusted' (default) or 'playbooks'.",
        _obj(
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 2000},
                "scope": {"enum": ["untrusted", "playbooks"], "default": "untrusted"},
            },
            ["query"],
        ),
        lambda a, ctx: brain_tools.recall(a.get("query", ""), a.get("scope", "untrusted"), ctx=ctx),
    ),
    ToolSpec(
        "list_open_followups",
        "Open (uncompleted) follow-ups, most-escalated first.",
        _obj({"limit": _LIMIT}),
        lambda a, ctx: brain_tools.list_open_followups(a.get("limit"), ctx=ctx),
    ),
    ToolSpec(
        "list_pending_task_candidates",
        "Task candidates awaiting a decision, highest-confidence first.",
        _obj({"limit": _LIMIT}),
        lambda a, ctx: brain_tools.list_pending_task_candidates(a.get("limit"), ctx=ctx),
    ),
    ToolSpec(
        "get_prospect",
        "One prospect by id.",
        _obj({"prospect_id": {"type": "integer"}}, ["prospect_id"]),
        lambda a, ctx: brain_tools.get_prospect(a.get("prospect_id"), ctx=ctx),
    ),
    ToolSpec(
        "list_new_prospects",
        "Prospects received within since_hours (≤168), newest first.",
        _obj(
            {
                "since_hours": {"type": "integer", "minimum": 1, "maximum": 168, "default": 24},
                "limit": _LIMIT,
            }
        ),
        lambda a, ctx: brain_tools.list_new_prospects(
            a.get("since_hours"), a.get("limit"), ctx=ctx
        ),
    ),
    ToolSpec(
        "spend_summary",
        "LLM spend rollup by function over a window.",
        _obj({"window": {"enum": ["today", "7d", "30d"]}}, ["window"]),
        lambda a, ctx: brain_tools.spend_summary(a.get("window", ""), ctx=ctx),
    ),
    ToolSpec(
        "get_playbook",
        "Read a trusted, git-tracked playbook by name.",
        _obj({"name": {"type": "string"}}, ["name"]),
        lambda a, ctx: brain_tools.get_playbook(a.get("name", ""), ctx=ctx),
    ),
    ToolSpec(
        "ingest_note",
        "Promote a note into the graph (one-way; source_type is 'tool').",
        _obj(
            {
                "text": {"type": "string", "minLength": 1, "maxLength": 32768},
                "source_ref": {"type": "string", "maxLength": 200},
            },
            ["text", "source_ref"],
        ),
        lambda a, ctx: brain_tools.ingest_note(a.get("text", ""), a.get("source_ref", ""), ctx=ctx),
    ),
    ToolSpec(
        "enqueue_approval",
        "Enqueue an action for human approval (#approvals). No side effect until approved.",
        _obj(
            {
                "item_type": {"type": "string"},
                "summary": {"type": "string", "maxLength": 500},
                "payload": {"type": "object"},
                "idempotency_key": {"type": "string", "maxLength": 100},
            },
            ["item_type", "summary", "payload"],
        ),
        lambda a, ctx: brain_tools.enqueue_approval(
            a.get("item_type", ""), a.get("summary", ""), a.get("payload", {}),
            a.get("idempotency_key"), ctx=ctx,
        ),
    ),
]

_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}


async def dispatch(name: str, arguments: dict, ctx: InvocationContext) -> dict:
    """Route a tool call to its brain_tools function. Raises ToolError on an
    unknown tool; the gates/validation live in brain_tools."""
    spec = _BY_NAME.get(name)
    if spec is None:
        raise ToolError("not_found", f"unknown tool {name!r}")
    result = spec.handler(arguments or {}, ctx)
    if inspect.isawaitable(result):
        result = await result
    return result


def json_default(o: Any) -> str:
    """JSON encoder fallback for DB row values (datetimes, dates, Decimals)."""
    if isinstance(o, datetime | date):
        return o.isoformat()
    if isinstance(o, Decimal):
        return str(o)
    return str(o)
