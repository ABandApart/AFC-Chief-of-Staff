"""Run one headless Claude Code session and collect its result (spike).

`PRD-claude-session-spike.md`. ADR-0001 names Claude Code as an alternate shell;
this is the smallest version of that: launch `claude -p` as a subprocess, get one
JSON result back, and write the session's reported cost to `agent_runs`.

The session is locked down by construction:
  - `--bare`: no hooks, no CLAUDE.md discovery, no auto-memory, no keychain reads.
    Auth is only the `ANTHROPIC_API_KEY` we pass in.
  - `--tools ""` and `--strict-mcp-config` with no config: no tools, no MCP
    servers. The prompt is data; the session can only answer.
  - `--max-budget-usd`: a per-session spend cap enforced by the CLI.
  - `--no-session-persistence`: nothing left on disk to resume.

The agent's own daily ceiling (`runs.DAILY_CEILINGS`) is checked before launch,
like every other agent.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from agents._lib import creds, db, runs

logger = logging.getLogger(__name__)

CLAUDE_BIN = "claude"
DEFAULT_TIMEOUT = 600


class ClaudeSessionError(RuntimeError):
    """The session failed, returned an error result, or returned unusable output."""


@dataclass
class SessionResult:
    output: dict[str, Any]     # the structured output, validated against the schema
    usd_cost: float
    input_tokens: int
    output_tokens: int
    session_id: str | None
    num_turns: int | None


def build_command(*, model: str, schema: dict[str, Any], max_budget_usd: float,
                  system_prompt: str | None = None) -> list[str]:
    """The `claude` argv (pure — unit-tested). The prompt goes on stdin."""
    argv = [
        CLAUDE_BIN, "-p",
        "--bare",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
        "--tools", "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--max-budget-usd", f"{max_budget_usd:.2f}",
        "--model", model,
    ]
    if system_prompt:
        argv += ["--append-system-prompt", system_prompt]
    return argv


def _usage_tokens(usage: dict[str, Any]) -> tuple[int, int]:
    """Input tokens (including cache reads and writes) and output tokens."""
    inp = sum(int(usage.get(k) or 0) for k in (
        "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return inp, int(usage.get("output_tokens") or 0)


def parse_result(stdout: str) -> SessionResult:
    """Parse the CLI's `--output-format json` result (pure — unit-tested).

    The structured output is read from `structured_output` when present, else
    `result` is parsed as JSON — the field name is unverified until the first
    runtime run (spec §Open).
    """
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise ClaudeSessionError(f"CLI output is not JSON: {stdout[:200]!r}") from e
    if not isinstance(data, dict):
        raise ClaudeSessionError("CLI output is not a JSON object")
    if data.get("is_error") or (data.get("subtype") not in (None, "success")):
        raise ClaudeSessionError(
            f"session ended with {data.get('subtype')!r}: {str(data.get('result'))[:300]}")

    output = data.get("structured_output")
    if output is None:
        try:
            output = json.loads(data.get("result") or "")
        except (TypeError, json.JSONDecodeError) as e:
            raise ClaudeSessionError("no structured output in the result") from e
    if not isinstance(output, dict):
        raise ClaudeSessionError("structured output is not an object")

    inp, out = _usage_tokens(data.get("usage") or {})
    return SessionResult(
        output=output,
        usd_cost=float(data.get("total_cost_usd") or 0.0),
        input_tokens=inp,
        output_tokens=out,
        session_id=data.get("session_id"),
        num_turns=data.get("num_turns"),
    )


def cost_from(stdout: str) -> float | None:
    """The reported session cost from CLI output, even for a failed session.

    A session can spend money and then fail (budget cap hit, bad output); that
    spend must still reach the ledger. None when the output carries no cost.
    """
    try:
        data = json.loads(stdout)
        return float(data["total_cost_usd"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None


def record_run(*, agent: str, function_label: str, model: str, started_at: datetime,
               status: str, result: SessionResult | None = None,
               usd_cost: float | None = None,
               error_text: str | None = None, correlation_id: str | None = None) -> None:
    """Write one `agent_runs` row for the whole session."""
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_runs (
                    agent_name, function_label, trigger_kind, started_at, ended_at,
                    status, llm_provider, llm_model, input_tokens, output_tokens,
                    usd_cost, correlation_id, correlation_kind, error_text
                ) VALUES (%s, %s, 'manual', %s, %s, %s, 'anthropic', %s, %s, %s, %s,
                          %s, 'claude_session', %s)
                """,
                (
                    agent, function_label, started_at, datetime.now(UTC), status, model,
                    result.input_tokens if result else None,
                    result.output_tokens if result else None,
                    result.usd_cost if result else usd_cost,
                    correlation_id or (result.session_id if result else None),
                    error_text,
                ),
            )


def run_session(prompt: str, *, agent: str, function_label: str, model: str,
                schema: dict[str, Any], max_budget_usd: float,
                system_prompt: str | None = None,
                timeout: int = DEFAULT_TIMEOUT) -> SessionResult:
    """Run one session and return its structured output. Ledgers success and failure.

    Raises `runs.DailyCeilingExceeded` before launching if the agent is over its
    ceiling, and `ClaudeSessionError` if the session fails.
    """
    runs.assert_under_ceiling(agent)
    argv = build_command(model=model, schema=schema, max_budget_usd=max_budget_usd,
                         system_prompt=system_prompt)
    env = {**os.environ, "ANTHROPIC_API_KEY": creds.keychain_get(runs.ANTHROPIC_KEY_ITEM)}
    started = datetime.now(UTC)
    stdout = ""
    try:
        proc = subprocess.run(argv, input=prompt, capture_output=True, text=True,
                              env=env, timeout=timeout, check=False)
        stdout = proc.stdout
        if proc.returncode != 0 and not proc.stdout.strip():
            raise ClaudeSessionError(
                f"claude exited {proc.returncode}: {proc.stderr.strip()[:300]}")
        result = parse_result(proc.stdout)
    except (ClaudeSessionError, subprocess.TimeoutExpired, OSError) as e:
        record_run(agent=agent, function_label=function_label, model=model,
                   started_at=started, status="failed", usd_cost=cost_from(stdout),
                   error_text=str(e)[:1000])
        raise ClaudeSessionError(str(e)) from e
    record_run(agent=agent, function_label=function_label, model=model,
               started_at=started, status="success", result=result)
    logger.info("claude session %s: $%.4f, %d in / %d out tokens, %s turn(s)",
                result.session_id, result.usd_cost, result.input_tokens,
                result.output_tokens, result.num_turns)
    return result
