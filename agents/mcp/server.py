"""Local stdio MCP server over the gated core (Track I, Task 3).

A thin transport: it advertises the tool catalog (`agents/mcp/tools.py`) and
routes each `tools/call` to `dispatch()` → `brain_tools`. All gates live in
`brain_tools`; this file only wires the MCP SDK to it. Trust is the OS account
(`barry-agent`); there is no network surface (stdio only).

Run (needs the `mcp` group: `uv sync --group mcp --group cognee`):
    uv run python -m agents.mcp.server

Register in a client's MCP config (e.g. Claude Code `.mcp.json`):
    { "mcpServers": { "afc-brain": {
        "command": "uv",
        "args": ["run", "python", "-m", "agents.mcp.server"],
        "cwd": "/Users/barry-agent/agents" } } }

Runtime-verify: this shim imports the `mcp` SDK, which is not synced on the build
box — the SDK wiring (types, run loop) is confirmed by the Claude Code end-to-end
drive (Task 6/7), not by the unit suite (which covers the transport-free catalog
+ dispatch in `agents/mcp/tools.py`).
"""

from __future__ import annotations

import json
import logging

import anyio

import mcp.types as types
from agents._lib import cognee_setup
from agents._lib.brain_tools import InvocationContext, ToolError
from agents.mcp.tools import TOOLS, dispatch, json_default
from mcp.server import Server
from mcp.server.stdio import stdio_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] mcp: %(message)s")
logger = logging.getLogger(__name__)

# stdio server → the caller is always the local OS account.
CTX = InvocationContext(caller="local:barry-agent", transport="mcp_stdio")

# Tools that reach cognee (graph search / cognify) need configure_cognee() first
# — the same lazy init the gateway does before dispatching these (a stdio process
# doesn't get that for free). The Postgres-backed reads don't touch cognee.
_COGNEE_TOOLS = frozenset({"recall", "ingest_note"})
_cognee_configured = False


def _ensure_cognee() -> None:
    global _cognee_configured
    if not _cognee_configured:
        cognee_setup.configure_cognee()
        _cognee_configured = True


server: Server = Server("afc-brain")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(name=t.name, description=t.description, inputSchema=t.input_schema)
        for t in TOOLS
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    """Route to the gated core; return the JSON result, or a structured error
    envelope (never a bare stack trace) so the shell can react programmatically."""
    try:
        if name in _COGNEE_TOOLS:
            _ensure_cognee()
        result = await dispatch(name, arguments or {}, CTX)
        text = json.dumps(result, default=json_default)
    except ToolError as e:
        logger.info("tool %s -> %s: %s", name, e.code, e.message)
        text = json.dumps(
            {"error": {"code": e.code, "message": e.message, "retryable": e.retryable}}
        )
    except Exception as e:  # unexpected — log the trace, return a terse envelope
        logger.exception("tool %s failed", name)
        text = json.dumps(
            {"error": {"code": "internal", "message": str(e), "retryable": False}}
        )
    return [types.TextContent(type="text", text=text)]


async def _run() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    logger.info("afc-brain MCP server starting (stdio); %d tools", len(TOOLS))
    anyio.run(_run)


if __name__ == "__main__":
    main()
