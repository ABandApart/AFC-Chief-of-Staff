"""Cost-emission helper for AFC Richmond's own agents.

Every LLM call from an *own-agent* goes through `agent_run()` (context manager)
and `RunContext`. Cognee's internal calls are attributed separately, via the
labeling callback in `_lib/telemetry_context.py` — but both paths write the same
`agent_runs` rows, so downstream (`cli/spend.py`, Higgins, Ted) reads one table.

**Telemetry model (Phase 3.7 / W1.2 — the cognee-pivot re-plumb).** There is
**no pre-flight per-call refusal** anymore. The old G1 per-run token cap and the
per-agent Anthropic keys are gone: cognee owns the call site for most spend and
can't support per-call gating, so the model is uniform — label + record every
call, and bound spend with a **soft daily breaker** that blocks the *next*
invocation once a ceiling is crossed. `cli/reconcile.py` (provider-bill vs
ledger) is the backstop that justifies dropping the hard gate.

  - **Daily breaker (soft ceiling)** — `assert_under_ceiling()` runs on
    `agent_run` entry, and is callable directly before a cognee operation. If
    today's spend for the agent is at/over `DAILY_CEILINGS[agent]`, or
    system-wide spend is at/over `GLOBAL_DAILY_CEILING`, it raises
    `DailyCeilingExceeded` and no row is written. It cannot prevent the call that
    crosses the line (that call already happened, or happens inside cognee) — it
    blocks the next one. "Today" starts at *local* midnight.
  - **Cost capture** — every call records tokens + USD from `PRICE_TABLE`. An
    unknown model raises `ValueError` *before* the paid call (never pay for a
    call whose cost can't be recorded).
  - **Provider error capture** — any exception inside the block writes a
    `failed` row.

One Anthropic key for own-agents (`anthropic-api-key`) and one Gemini key
(`gemini-api-key`). Per-agent provider keys were dropped with the pivot —
spend attribution now lives entirely in the `agent_runs` ledger (by
`agent_name` / `function_label` / `correlation_id`), not in provider dashboards.

G3 (anomaly detection) stays Ted's job (Phase 11) — pure Python over agent_runs.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import anthropic
from google import genai
from google.genai import types as genai_types

from agents._lib import creds, db

# =============================================================================
# Constants
# =============================================================================

# Price table — USD per token. Update via PR; bump module version on change.
PRICE_TABLE: dict[tuple[str, str], dict[str, float]] = {
    # (provider, model) -> {"input": USD/token, "output": USD/token}
    ("anthropic", "claude-opus-4-7"):    {"input":  5.0 / 1_000_000, "output": 25.0 / 1_000_000},
    ("anthropic", "claude-opus-4-6"):    {"input":  5.0 / 1_000_000, "output": 25.0 / 1_000_000},
    ("anthropic", "claude-sonnet-4-6"):  {"input":  3.0 / 1_000_000, "output": 15.0 / 1_000_000},
    ("anthropic", "claude-haiku-4-5"):   {"input":  1.0 / 1_000_000, "output":  5.0 / 1_000_000},
    ("gemini",    "gemini-2.5-flash"):   {"input":  0.075 / 1_000_000, "output": 0.30 / 1_000_000},
    # Embeddings. text-embedding-004 is not served on our Gemini key (404 on
    # embedContent, confirmed 2026-06-16) — gemini-embedding-001 is the
    # available model. Priced at the paid-tier rate; no billable "output" tokens.
    ("gemini",    "gemini-embedding-001"): {"input": 0.15 / 1_000_000, "output": 0.0},
    ("gemini",    "text-embedding-004"):   {"input": 0.0,              "output": 0.0},  # kept for reference; unavailable on this key
}

# Per-agent daily spend ceilings (the soft breaker). USD/day.
# Total daily blast radius for the fully-populated system: ~$15.
DAILY_CEILINGS: dict[str, float] = {
    "phase-2-smoke":     0.50,   # Phase 2 verification only
    "cognee":            5.00,   # Phase 3.7 — unlabeled cognee calls (labeled ones use the agent's own ceiling)
    "tartt":             5.00,   # Phase 4
    "keeley-strategy":   1.50,   # Phase 8 (combined ceiling with keeley-content per arch)
    "keeley-content":    1.50,   # Phase 8
    "roy-kent":          1.00,   # Phase 6
    "nate-shelley":      0.07,   # Phase 10 (~$0.50/week)
    "higgins":           0.04,   # Phase 11 (~$0.30/week)
    "ted":               0.20,   # Phase 11
    "briefing":          0.50,   # Phase 3+
    "sam":               1.00,   # Phase 8
    "meeting-processor": 3.00,   # Phase 7 ($1/transcript, capped at $3/day)
    "fact-extraction":   2.00,   # Phase 3
    "recall":            0.50,   # Phase 3.3 — query embeddings only (gemini, cheap)
}

# System-wide kill switch: total spend across ALL agents per day.
GLOBAL_DAILY_CEILING = 20.00

# One key per provider for own-agents (per-agent keys dropped with the pivot).
ANTHROPIC_KEY_ITEM = "anthropic-api-key"
GEMINI_KEY_ITEM = "gemini-api-key"


# =============================================================================
# Exceptions
# =============================================================================


class DailyCeilingExceeded(Exception):
    """The soft breaker: the agent (or the whole system) is at/over its
    per-day spend ceiling, so the next invocation is blocked."""


# =============================================================================
# Credential / client helpers
# =============================================================================


def _keychain_get(item_name: str) -> str:
    """Cached keychain lookup (see `_lib/creds.py`)."""
    return creds.keychain_get(item_name)


# SDK clients are reusable and thread-safe; construct once.
_ANTHROPIC_CLIENT: anthropic.Anthropic | None = None
_GENAI_CLIENT: genai.Client | None = None


def _anthropic_client() -> anthropic.Anthropic:
    global _ANTHROPIC_CLIENT
    if _ANTHROPIC_CLIENT is None:
        _ANTHROPIC_CLIENT = anthropic.Anthropic(api_key=_keychain_get(ANTHROPIC_KEY_ITEM))
    return _ANTHROPIC_CLIENT


def _gemini_client() -> genai.Client:
    global _GENAI_CLIENT
    if _GENAI_CLIENT is None:
        _GENAI_CLIENT = genai.Client(api_key=_keychain_get(GEMINI_KEY_ITEM))
    return _GENAI_CLIENT


def _l2_normalize(vec: list[float]) -> list[float]:
    """Return the L2-normalized (unit-length) vector. Zero vector → unchanged."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def _price_for(provider: str, model: str) -> dict[str, float]:
    """Look up pricing. Raises BEFORE any call is made — never pay for a
    call whose cost can't be recorded."""
    price = PRICE_TABLE.get((provider, model))
    if price is None:
        raise ValueError(
            f"No PRICE_TABLE entry for {provider}/{model}. "
            f"Update PRICE_TABLE before using this model."
        )
    return price


# =============================================================================
# Daily breaker (soft ceiling)
# =============================================================================


def assert_under_ceiling(agent_name: str) -> None:
    """Raise `DailyCeilingExceeded` if today's spend is at/over a ceiling.

    Checks the system-wide ceiling always, and the agent's own ceiling when it
    has a `DAILY_CEILINGS` entry (agents without one — rare — are bounded only
    by the global ceiling). Callable directly before a cognee operation to block
    the next cognify invocation once spend is over. "Today" = local midnight.
    """
    now = datetime.now(UTC)
    local_tz = datetime.now().astimezone().tzinfo
    today_start = now.astimezone(local_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(usd_cost) FILTER (WHERE agent_name = %s), 0), "
                "       COALESCE(SUM(usd_cost), 0) "
                "FROM agent_runs "
                "WHERE started_at >= %s",
                (agent_name, today_start),
            )
            row = cur.fetchone()
            spent_today = float(row[0]) if row else 0.0
            spent_global = float(row[1]) if row else 0.0

    if spent_global >= GLOBAL_DAILY_CEILING:
        raise DailyCeilingExceeded(
            f"System-wide spend is ${spent_global:.4f} today; global daily "
            f"ceiling is ${GLOBAL_DAILY_CEILING:.2f}. Next invocation blocked."
        )
    ceiling = DAILY_CEILINGS.get(agent_name)
    if ceiling is not None and spent_today >= ceiling:
        raise DailyCeilingExceeded(
            f"Agent '{agent_name}' has spent ${spent_today:.4f} today; "
            f"daily ceiling is ${ceiling:.2f}. Next invocation blocked."
        )


# =============================================================================
# Run state and context
# =============================================================================


@dataclass
class _RunState:
    """Internal state accumulated across the context. Written on exit."""

    agent_name: str
    function_label: str
    trigger_kind: str
    started_at: datetime
    correlation_id: str | None = None
    correlation_kind: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    usd_cost: float = 0.0
    status: str = "success"
    error_text: str | None = None
    # Telemetry of all calls in this context (multi-call runs are rare; supports it).
    _call_count: int = field(default=0)


class RunContext:
    """Exposed inside `with agent_run(...) as run:`. Holds LLM call methods.

    A RunContext can host multiple LLM calls in sequence; tokens and costs
    accumulate across them. Most agents make exactly one call per run.
    """

    def __init__(self, state: _RunState):
        self._state = state

    def _anthropic_call(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        max_output_tokens: int,
        system: str | None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
    ) -> Any:
        """Shared Anthropic call path: pricing + call + cost capture.

        Returns the raw Messages API response.
        """
        # Price check first: an unknown model must refuse BEFORE spending.
        price = _price_for("anthropic", model)
        client = _anthropic_client()

        create_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_output_tokens,
            "messages": messages,
        }
        if system is not None:
            create_kwargs["system"] = system
        if tools is not None:
            create_kwargs["tools"] = tools
        if tool_choice is not None:
            create_kwargs["tool_choice"] = tool_choice
        response = client.messages.create(**create_kwargs)

        # Record cost + telemetry
        self._state.llm_provider = "anthropic"
        self._state.llm_model = model
        self._state.input_tokens += response.usage.input_tokens
        self._state.output_tokens += response.usage.output_tokens
        self._state.usd_cost += (
            response.usage.input_tokens * price["input"]
            + response.usage.output_tokens * price["output"]
        )
        self._state._call_count += 1
        return response

    def call_anthropic(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        max_output_tokens: int,
        system: str | None = None,
    ) -> str:
        """Call Anthropic Messages API. Returns concatenated text content.

        Raises:
            ValueError: model has no PRICE_TABLE entry (raised before calling)
            (provider errors propagate; caught and recorded by agent_run)
        """
        response = self._anthropic_call(
            messages,
            model=model,
            max_output_tokens=max_output_tokens,
            system=system,
        )
        return "".join(b.text for b in response.content if b.type == "text")

    def call_anthropic_structured(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        max_output_tokens: int,
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
        system: str | None = None,
    ) -> dict[str, Any]:
        """Call Anthropic with a single forced tool — schema-validated output.

        The model MUST call the named tool, so the return value is the tool's
        input dict, already shaped by `input_schema`.

        Raises RuntimeError if the response contains no tool_use block (rare:
        e.g. the call hit max_output_tokens mid-generation).
        """
        response = self._anthropic_call(
            messages,
            model=model,
            max_output_tokens=max_output_tokens,
            system=system,
            tools=[{
                "name": tool_name,
                "description": tool_description,
                "input_schema": input_schema,
            }],
            tool_choice={"type": "tool", "name": tool_name},
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == tool_name:
                return dict(block.input)
        raise RuntimeError(
            f"Anthropic response contained no '{tool_name}' tool_use block "
            f"(stop_reason={response.stop_reason})"
        )

    def call_gemini(
        self,
        prompt: str,
        *,
        model: str,
        max_output_tokens: int,
    ) -> str:
        """Call Gemini generateContent. Returns response text.

        Raises:
            ValueError: model has no PRICE_TABLE entry (raised before calling)
            (provider errors propagate; caught and recorded by agent_run)
        """
        price = _price_for("gemini", model)
        client = _gemini_client()

        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(max_output_tokens=max_output_tokens),
        )

        usage = response.usage_metadata
        self._state.llm_provider = "gemini"
        self._state.llm_model = model
        self._state.input_tokens += usage.prompt_token_count or 0
        self._state.output_tokens += usage.candidates_token_count or 0
        self._state.usd_cost += (
            (usage.prompt_token_count or 0) * price["input"]
            + (usage.candidates_token_count or 0) * price["output"]
        )
        self._state._call_count += 1

        return response.text or ""

    def call_embedding(
        self,
        texts: list[str],
        *,
        model: str = "gemini-embedding-001",
        output_dimensionality: int = db.EMBEDDING_DIM,
    ) -> list[list[float]]:
        """Generate Gemini embeddings. Returns L2-normalized vectors.

        System standard is 768 dims, matching the `vector(768)` columns.
        `gemini-embedding-001` defaults to 3072 dims and only ships
        pre-normalized at that size; at 768 (a Matryoshka truncation) the
        vectors are NOT normalized by the API, so we L2-normalize here.
        """
        price = _price_for("gemini", model)
        client = _gemini_client()

        result = client.models.embed_content(
            model=model,
            contents=texts,
            config=genai_types.EmbedContentConfig(
                output_dimensionality=output_dimensionality
            ),
        )

        # Embeddings don't return usage_metadata the same way; estimate input
        # tokens as char_total / 4 (a standard rough heuristic).
        char_total = sum(len(t) for t in texts)
        estimated_tokens = max(1, char_total // 4)

        self._state.llm_provider = "gemini"
        self._state.llm_model = model
        self._state.input_tokens += estimated_tokens
        self._state.usd_cost += estimated_tokens * price["input"]
        self._state._call_count += 1

        return [_l2_normalize([float(v) for v in e.values]) for e in result.embeddings]


# =============================================================================
# agent_run context manager
# =============================================================================


@contextmanager
def agent_run(
    agent_name: str,
    function_label: str,
    *,
    trigger_kind: str = "manual",
    correlation_id: str | None = None,
    correlation_kind: str | None = None,
) -> Iterator[RunContext]:
    """Context manager wrapping every LLM call from an own-agent.

    On entry: runs the soft daily breaker (`assert_under_ceiling`). If the agent
    or the system is already at/over its ceiling, raises `DailyCeilingExceeded`
    and writes **no** row (the next invocation is what's blocked; the crossing
    call already happened). The check is check-then-act — concurrent runs can
    overshoot by roughly one call's cost, which is acceptable at these ceilings.

    On exit: writes exactly one row to agent_runs (success or failed).

    Args:
        agent_name: matches a DAILY_CEILINGS entry and agent_runs.agent_name
        function_label: matches architecture/80-telemetry-layer.md function labels
        trigger_kind: 'scheduled' | 'event' | 'manual'
        correlation_id/correlation_kind: the entity this run is about

    Raises:
        ValueError: agent_name has no DAILY_CEILINGS entry
        DailyCeilingExceeded: agent or system is at or over a daily ceiling
    """
    if agent_name not in DAILY_CEILINGS:
        raise ValueError(
            f"Agent '{agent_name}' has no DAILY_CEILINGS entry. "
            f"Add one to agents/_lib/runs.py before running."
        )

    started_at = datetime.now(UTC)
    state = _RunState(
        agent_name=agent_name,
        function_label=function_label,
        trigger_kind=trigger_kind,
        correlation_id=correlation_id,
        correlation_kind=correlation_kind,
        started_at=started_at,
    )

    # Soft daily breaker — blocks the next invocation once over; writes no row.
    assert_under_ceiling(agent_name)

    ctx = RunContext(state)
    try:
        yield ctx
    except BaseException as exc:
        # Any exception (provider errors, programmer errors) → failed row.
        if state.status == "success":
            state.status = "failed"
            state.error_text = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        # Write the row regardless of success/failure. Suppress write errors so
        # the original exception (if any) propagates.
        ended_at = datetime.now(UTC)
        try:
            with db.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO agent_runs (
                            agent_name, function_label, trigger_kind,
                            started_at, ended_at, status,
                            llm_provider, llm_model,
                            input_tokens, output_tokens, usd_cost,
                            correlation_id, correlation_kind, error_text
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            state.agent_name, state.function_label, state.trigger_kind,
                            state.started_at, ended_at, state.status,
                            state.llm_provider, state.llm_model,
                            state.input_tokens, state.output_tokens,
                            round(state.usd_cost, 8),
                            state.correlation_id, state.correlation_kind,
                            state.error_text,
                        ),
                    )
        except Exception:
            import sys
            import traceback
            print(
                "WARNING: failed to write agent_runs row (run was otherwise "
                f"{state.status}):",
                file=sys.stderr,
            )
            traceback.print_exc(file=sys.stderr)
