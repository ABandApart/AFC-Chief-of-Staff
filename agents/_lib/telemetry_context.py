"""Telemetry labeling for calls we don't own the call site of (cognee).

Phase 3.7 / W1, mitigation **M1**. Cognee makes LLM + embedding calls through
litellm; we can't wrap them in `agent_run()` the way our own agents' calls are
wrapped. Instead we set a contextvar *label* around a cognee operation and
register a litellm callback that reads it and writes a conformant `agent_runs`
row per provider call. The spike proved this reaches 100% of cognee's calls —
but ONLY when cognee's Anthropic path is routed through litellm
(`LLM_PROVIDER=custom`, `LLM_MODEL=anthropic/…`); the native adapter bypasses
the callback. That routing is configured at cognee stand-up (W2).

This module is additive and non-breaking: it does not touch `runs.py`. The
deprecation of the pre-flight gates + per-agent keys (W1.2) is a separate step.

`install_litellm_callback()` imports litellm lazily, so this module is importable
(and unit-testable) without litellm present.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from agents._lib import db

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Label:
    agent_name: str
    function_label: str
    trigger_kind: str
    correlation_id: str
    run_id: str


_current_label: ContextVar[Label | None] = ContextVar("_current_label", default=None)


# Cognee-call price map (USD/token), keyed by substring of litellm's model string
# (which may carry a provider prefix, e.g. "anthropic/claude-haiku-4-5"). Kept
# local rather than importing runs.PRICE_TABLE because litellm's model naming
# differs from our exact keys; own-agent calls still use runs.PRICE_TABLE.
_PRICE: dict[str, tuple[str, float, float]] = {
    # substring: (provider, input $/tok, output $/tok)
    "claude-opus":        ("anthropic", 5.0 / 1e6, 25.0 / 1e6),
    "claude-sonnet":      ("anthropic", 3.0 / 1e6, 15.0 / 1e6),
    "claude-haiku":       ("anthropic", 1.0 / 1e6, 5.0 / 1e6),
    "gemini-embedding":   ("gemini", 0.15 / 1e6, 0.0),
    "gemini-2.5-flash":   ("gemini", 0.075 / 1e6, 0.30 / 1e6),
}


def _price_for(model: str) -> tuple[str, float, float]:
    for key, val in _PRICE.items():
        if key in (model or ""):
            return val
    logger.warning("telemetry_context: no price for model %r — recording $0", model)
    return ("unknown", 0.0, 0.0)


@contextmanager
def labeled(
    agent_name: str,
    function_label: str,
    *,
    trigger_kind: str = "event",
    correlation_id: str | None = None,
) -> Iterator[str]:
    """Label every provider call made inside this block. Returns the run_id.

    All calls in the block share one `run_id`; the written rows carry
    `correlation_id` (the entity, e.g. a message id — falls back to run_id) and
    `correlation_kind='cognify_run'`, so `GROUP BY correlation_id` rolls a whole
    cognify up. Propagates into asyncio child tasks (contextvars are copied at
    task creation), which is why it survives cognee's chunk fan-out.
    """
    run_id = uuid4().hex
    label = Label(
        agent_name=agent_name,
        function_label=function_label,
        trigger_kind=trigger_kind,
        correlation_id=correlation_id or run_id,
        run_id=run_id,
    )
    token = _current_label.set(label)
    try:
        yield run_id
    finally:
        _current_label.reset(token)


def _record_call(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    started_at: datetime,
    ended_at: datetime,
    status: str = "success",
    error_text: str | None = None,
) -> None:
    """Write one `agent_runs` row for a provider call, attributed via the label.

    Unlabeled calls (outside any `labeled()` block) are still recorded, under
    agent 'cognee'/'unlabeled' — so nothing cognee spends goes unmeasured.
    """
    label = _current_label.get()
    agent_name = label.agent_name if label else "cognee"
    function_label = label.function_label if label else "unlabeled"
    trigger_kind = label.trigger_kind if label else "event"
    correlation_id = label.correlation_id if label else None

    provider, in_price, out_price = _price_for(model)
    usd = input_tokens * in_price + output_tokens * out_price

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
                        agent_name, function_label, trigger_kind,
                        started_at, ended_at, status,
                        provider, model,
                        input_tokens, output_tokens, round(usd, 8),
                        correlation_id, "cognify_run", error_text,
                    ),
                )
    except Exception:
        import sys
        import traceback
        print("WARNING: telemetry_context failed to write agent_runs row",
              file=sys.stderr)
        traceback.print_exc(file=sys.stderr)


def _usage_from(kwargs: dict, response_obj: object) -> tuple[str, int, int]:
    """Pull (model, input_tokens, output_tokens) from a litellm callback payload
    across the shapes litellm has used."""
    model = kwargs.get("model") or ""
    usage = getattr(response_obj, "usage", None) or kwargs.get("usage")
    in_tok = int(getattr(usage, "prompt_tokens", None)
                 or (usage or {}).get("prompt_tokens", 0) or 0)
    out_tok = int(getattr(usage, "completion_tokens", None)
                  or (usage or {}).get("completion_tokens", 0) or 0)
    return model, in_tok, out_tok


def install_litellm_callback() -> None:
    """Register the labeling callback on the shared litellm module.

    Call once at process start (after cognee config). litellm is imported here
    lazily so this module stays importable without it.
    """
    from litellm.integrations.custom_logger import CustomLogger

    def _handle(kwargs, response_obj, start_time, end_time, status, err=None):
        model, i, o = _usage_from(kwargs, response_obj)
        _record_call(
            model=model, input_tokens=i, output_tokens=o,
            started_at=start_time or datetime.now(UTC),
            ended_at=end_time or datetime.now(UTC),
            status=status, error_text=err,
        )

    class _LabelCapture(CustomLogger):
        def log_success_event(self, kwargs, response_obj, start_time, end_time):
            _handle(kwargs, response_obj, start_time, end_time, "success")

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            _handle(kwargs, response_obj, start_time, end_time, "success")

        def log_failure_event(self, kwargs, response_obj, start_time, end_time):
            _handle(kwargs, response_obj, start_time, end_time, "failed",
                    str(kwargs.get("exception", "")))

        async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
            _handle(kwargs, response_obj, start_time, end_time, "failed",
                    str(kwargs.get("exception", "")))

    import litellm
    litellm.callbacks = [_LabelCapture()]
    logger.info("telemetry_context: litellm callback installed")
