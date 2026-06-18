"""Cost-emission helper for AFC Richmond agents.

Every LLM call from every agent goes through `agent_run()` (context manager)
and the `RunContext` class. This module is the single enforcement point for:

  - **G1 (per-run token cap)** — before any call, count tokens; refuse if
    over `max_input_tokens`. Writes a `token_cap_exceeded` agent_runs row.
  - **G2 (per-day spend ceiling)** — on context entry, query today's total
    spend for this agent. Raises `DailyCeilingExceeded` if at or over the
    `DAILY_CEILINGS` value. No row is written for refused-before-start runs.
  - **Cost capture** — every successful call updates the run's input/output
    token counts and USD cost from `PRICE_TABLE`.
  - **Provider error capture** — any exception inside the block writes a
    `failed` row with `error_text` set.

G3 (anomaly detection) is **not** in this module — that's Ted's job in
Phase 11, runs as pure Python against `agent_runs`, no enforcement here.

See `architecture/80-telemetry-layer.md` for the architectural design.

Phase-1 architectural deviation: per-agent Anthropic API keys (see
`architecture/70-build-order.md` decision log). The `call_anthropic` method
looks up the right key by agent name via `KEY_BY_AGENT`. Agents not in
that mapping raise `MissingAgentKeyError` — keys must be provisioned
explicitly before a new agent goes live.
"""

from __future__ import annotations

import math
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

import anthropic
import psycopg
from google import genai
from google.genai import types as genai_types

# =============================================================================
# Constants
# =============================================================================

# Price table — USD per token. Update via PR; bump module version on change.
# Sources verified 2026-05-19:
#   Anthropic — platform.claude.com models table
#   Gemini — ai.google.dev/pricing
PRICE_TABLE: dict[tuple[str, str], dict[str, float]] = {
    # (provider, model) -> {"input": USD/token, "output": USD/token}
    ("anthropic", "claude-opus-4-7"):    {"input":  5.0 / 1_000_000, "output": 25.0 / 1_000_000},
    ("anthropic", "claude-opus-4-6"):    {"input":  5.0 / 1_000_000, "output": 25.0 / 1_000_000},
    ("anthropic", "claude-sonnet-4-6"):  {"input":  3.0 / 1_000_000, "output": 15.0 / 1_000_000},
    ("anthropic", "claude-haiku-4-5"):   {"input":  1.0 / 1_000_000, "output":  5.0 / 1_000_000},
    ("gemini",    "gemini-2.5-flash"):   {"input":  0.075 / 1_000_000, "output": 0.30 / 1_000_000},
    # Embeddings. text-embedding-004 is not served on our Gemini key (404 on
    # embedContent, confirmed 2026-06-16) — gemini-embedding-001 is the
    # available model. Priced at the paid-tier rate (verify periodically at
    # ai.google.dev/pricing); embeddings have no billable "output" tokens.
    ("gemini",    "gemini-embedding-001"): {"input": 0.15 / 1_000_000, "output": 0.0},
    ("gemini",    "text-embedding-004"):   {"input": 0.0,              "output": 0.0},  # kept for reference; unavailable on this key
}

# Per-agent daily spend ceilings (G2). USD/day.
# Source: architecture/80-telemetry-layer.md "Starting ceilings" table.
# Total daily blast radius for fully-populated system: ~$15.
DAILY_CEILINGS: dict[str, float] = {
    "phase-2-smoke":     0.50,   # Phase 2 verification only; remove when Phase 2 closes
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

# Per-agent Anthropic API key dispatch (Phase 1 architectural deviation).
# Maps agent name -> keychain item name. Agents without an entry here cannot
# make Anthropic calls — they must be added explicitly before going live.
# Gemini uses a single shared `gemini-api-key` (no per-agent split for Gemini).
KEY_BY_AGENT: dict[str, str] = {
    "phase-2-smoke":   "anthropic-key-ted",   # smoke test piggybacks on ted's key
    "ted":             "anthropic-key-ted",
    "keeley-strategy": "anthropic-key-keeley-strategy",
    "keeley-content":  "anthropic-key-keeley-content",
    "roy-kent":        "anthropic-key-roy-kent",
    "nate-shelley":    "anthropic-key-nate-shelley",
    "higgins":         "anthropic-key-higgins",
    "fact-extraction": "anthropic-key-fact-extraction",  # Phase 3.2
    # Agents below need keys provisioned at their respective phases:
    # "tartt":           gemini-only (Phase 4) — no anthropic key needed
    # "briefing":        anthropic-key-briefing (Phase 3.5)
    # "sam":             anthropic-key-sam (Phase 8)
    # "meeting-processor": anthropic-key-meeting-processor (Phase 7)
}


# =============================================================================
# Exceptions
# =============================================================================


class TokenCapExceeded(Exception):
    """G1: a single call's input token count would exceed the declared cap."""


class DailyCeilingExceeded(Exception):
    """G2: the agent has reached its per-day spend ceiling."""


class MissingAgentKeyError(Exception):
    """The agent has no `anthropic-key-<slug>` entry in `KEY_BY_AGENT`."""


# =============================================================================
# Keychain helper
# =============================================================================


def _keychain_get(item_name: str) -> str:
    """Fetch a credential from the current user's macOS Keychain.

    Returns the secret value. Raises RuntimeError if the item is missing.
    """
    result = subprocess.run(
        ["security", "find-generic-password", "-w", "-s", item_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Keychain item '{item_name}' not found. "
            f"Run scripts/keychain_setup.sh or check item name."
        )
    return result.stdout.strip()


def _db_url() -> str:
    """Pull the Postgres connection string from keychain."""
    return _keychain_get("db-url")


def _l2_normalize(vec: list[float]) -> list[float]:
    """Return the L2-normalized (unit-length) vector. Zero vector → unchanged."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


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

    def call_anthropic(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        max_input_tokens: int,
        max_output_tokens: int,
        system: str | None = None,
    ) -> str:
        """Call Anthropic Messages API. Returns concatenated text content.

        Enforces G1 (per-run input token cap) before the call. Looks up the
        per-agent API key via `KEY_BY_AGENT`.

        Raises:
            MissingAgentKeyError: agent has no key registered in KEY_BY_AGENT
            TokenCapExceeded: counted input tokens > max_input_tokens
            (provider errors propagate; caught and recorded by agent_run)
        """
        agent_name = self._state.agent_name
        key_item = KEY_BY_AGENT.get(agent_name)
        if key_item is None:
            raise MissingAgentKeyError(
                f"Agent '{agent_name}' has no Anthropic key registered in "
                f"KEY_BY_AGENT (agents/_lib/runs.py). Add an entry mapping the "
                f"agent slug to a keychain item name (e.g. "
                f"'anthropic-key-{agent_name}') before calling."
            )

        api_key = _keychain_get(key_item)
        client = anthropic.Anthropic(api_key=api_key)

        # G1: pre-call token count enforcement
        count_kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if system is not None:
            count_kwargs["system"] = system
        token_count = client.messages.count_tokens(**count_kwargs)
        if token_count.input_tokens > max_input_tokens:
            self._state.status = "token_cap_exceeded"
            self._state.error_text = (
                f"input tokens {token_count.input_tokens} exceeds cap {max_input_tokens}"
            )
            self._state.llm_provider = "anthropic"
            self._state.llm_model = model
            self._state.input_tokens = token_count.input_tokens
            raise TokenCapExceeded(self._state.error_text)

        # Make the actual call
        create_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_output_tokens,
            "messages": messages,
        }
        if system is not None:
            create_kwargs["system"] = system
        response = client.messages.create(**create_kwargs)

        # Record cost + telemetry
        price = PRICE_TABLE.get(("anthropic", model))
        if price is None:
            raise ValueError(
                f"No PRICE_TABLE entry for anthropic/{model}. "
                f"Update PRICE_TABLE before using this model."
            )
        self._state.llm_provider = "anthropic"
        self._state.llm_model = model
        self._state.input_tokens += response.usage.input_tokens
        self._state.output_tokens += response.usage.output_tokens
        self._state.usd_cost += (
            response.usage.input_tokens * price["input"]
            + response.usage.output_tokens * price["output"]
        )
        self._state._call_count += 1

        return "".join(b.text for b in response.content if b.type == "text")

    def call_gemini(
        self,
        prompt: str,
        *,
        model: str,
        max_input_tokens: int,
        max_output_tokens: int,
    ) -> str:
        """Call Gemini generateContent. Returns response text.

        Uses the shared `gemini-api-key` keychain item (Gemini is not split
        per-agent in v1 — only Anthropic is, for spend attribution).

        Raises:
            TokenCapExceeded: counted input tokens > max_input_tokens
            (provider errors propagate; caught and recorded by agent_run)
        """
        api_key = _keychain_get("gemini-api-key")
        client = genai.Client(api_key=api_key)

        # G1: pre-call token count enforcement
        count_resp = client.models.count_tokens(model=model, contents=prompt)
        if count_resp.total_tokens > max_input_tokens:
            self._state.status = "token_cap_exceeded"
            self._state.error_text = (
                f"input tokens {count_resp.total_tokens} exceeds cap {max_input_tokens}"
            )
            self._state.llm_provider = "gemini"
            self._state.llm_model = model
            self._state.input_tokens = count_resp.total_tokens
            raise TokenCapExceeded(self._state.error_text)

        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=genai_types.GenerateContentConfig(max_output_tokens=max_output_tokens),
        )

        price = PRICE_TABLE.get(("gemini", model))
        if price is None:
            raise ValueError(
                f"No PRICE_TABLE entry for gemini/{model}. "
                f"Update PRICE_TABLE before using this model."
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
        output_dimensionality: int = 768,
    ) -> list[list[float]]:
        """Generate Gemini embeddings. Returns L2-normalized vectors.

        System standard is 768 dims, matching the `vector(768)` columns.
        `gemini-embedding-001` defaults to 3072 dims and only ships
        pre-normalized at that size; at 768 (a Matryoshka truncation) the
        vectors are NOT normalized by the API, so we L2-normalize here. That
        keeps cosine search correct and also makes dot-product / L2 behave,
        and keeps stored facts and query embeddings on the same footing.

        (`text-embedding-004` is not served on our Gemini key — returns 404 on
        embedContent. See PRICE_TABLE note. 2026-06-16.)
        """
        api_key = _keychain_get("gemini-api-key")
        client = genai.Client(api_key=api_key)

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

        price = PRICE_TABLE.get(("gemini", model))
        if price is None:
            raise ValueError(f"No PRICE_TABLE entry for gemini/{model}")

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
    """Context manager wrapping every LLM call from every agent.

    On entry: checks G2 (per-day spend ceiling). If today's spend for this
    agent is at or over `DAILY_CEILINGS[agent_name]`, raises
    `DailyCeilingExceeded` immediately and writes **no** agent_runs row.

    On exit: writes exactly one row to agent_runs with the accumulated
    tokens, cost, status, and any error. Always writes — success, partial,
    failed, or token_cap_exceeded.

    Usage:
        with agent_run("tartt", "news_aggregation",
                       correlation_id=str(item_id),
                       correlation_kind="content_item") as run:
            summary = run.call_gemini(
                prompt=build_summary_prompt(item_text),
                model="gemini-2.5-flash",
                max_input_tokens=4000,
                max_output_tokens=500,
            )

    Args:
        agent_name: matches DAILY_CEILINGS entry and agent_runs.agent_name
        function_label: matches a value from architecture/80-telemetry-layer.md
            function labels table (news_aggregation, topic_research,
            action_surfacing, customer_discovery, infrastructure, telemetry)
        trigger_kind: 'scheduled' | 'event' | 'manual'
        correlation_id: id of the entity this run is about (content_item_id,
            prospect_id, etc.) — facilitates joining agent_runs to entities
        correlation_kind: type tag for correlation_id ('content_item',
            'prospect', 'transcript', etc.)

    Raises:
        ValueError: agent_name has no DAILY_CEILINGS entry
        DailyCeilingExceeded: agent is at or over its daily ceiling
    """
    if agent_name not in DAILY_CEILINGS:
        raise ValueError(
            f"Agent '{agent_name}' has no DAILY_CEILINGS entry. "
            f"Add one to agents/_lib/runs.py before running."
        )

    started_at = datetime.now(timezone.utc)
    state = _RunState(
        agent_name=agent_name,
        function_label=function_label,
        trigger_kind=trigger_kind,
        correlation_id=correlation_id,
        correlation_kind=correlation_kind,
        started_at=started_at,
    )

    # G2: pre-flight ceiling check. Refused runs write no row.
    db_url = _db_url()
    today_start = started_at.replace(hour=0, minute=0, second=0, microsecond=0)
    with psycopg.connect(db_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(usd_cost), 0) "
                "FROM agent_runs "
                "WHERE agent_name = %s AND started_at >= %s",
                (agent_name, today_start),
            )
            row = cur.fetchone()
            spent_today = float(row[0]) if row else 0.0

    ceiling = DAILY_CEILINGS[agent_name]
    if spent_today >= ceiling:
        raise DailyCeilingExceeded(
            f"Agent '{agent_name}' has spent ${spent_today:.4f} today; "
            f"daily ceiling is ${ceiling:.2f}. No call made; no row written."
        )

    ctx = RunContext(state)
    exception_to_reraise: BaseException | None = None
    try:
        yield ctx
    except TokenCapExceeded:
        # Status + error already set on state by call_*; just record + reraise
        exception_to_reraise = None  # don't suppress, but let finally write
        raise
    except BaseException as exc:
        # Any other exception (provider errors, programmer errors, etc.)
        if state.status == "success":
            state.status = "failed"
            state.error_text = f"{type(exc).__name__}: {exc}"
        exception_to_reraise = None  # don't suppress
        raise
    finally:
        # Write the row regardless of success/failure.
        # Suppress write errors so the original exception (if any) propagates.
        ended_at = datetime.now(timezone.utc)
        try:
            with psycopg.connect(db_url, autocommit=True) as conn:
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
                            round(state.usd_cost, 4),
                            state.correlation_id, state.correlation_kind,
                            state.error_text,
                        ),
                    )
        except Exception:
            # If we can't write the ledger, raise visibility but don't mask
            # the original failure.
            import sys
            import traceback
            print(
                "WARNING: failed to write agent_runs row (run was otherwise "
                f"{state.status}):",
                file=sys.stderr,
            )
            traceback.print_exc(file=sys.stderr)
