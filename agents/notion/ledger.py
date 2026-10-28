"""One `agent_runs` row per Notion sync run (`PRD-notion-integration.md`, Components).

Notion sync makes no LLM call, so it does not go through `runs.agent_run`: that
wrapper runs the soft daily breaker, which would stop a free loop whenever LLM
spend crossed a ceiling. This writes the same row shape directly, with
`agent_name='notion-sync'` and `usd_cost=0`, so KR4.2 counts the run and Ted can
watch it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from agents._lib import db

logger = logging.getLogger(__name__)

AGENT = "notion-sync"


@contextmanager
def notion_run(function_label: str, *, trigger_kind: str = "scheduled") -> Iterator[None]:
    """Write one success or failed row when the block exits. Never masks the block's error."""
    started_at = datetime.now(UTC)
    status, error_text = "success", None
    try:
        yield
    except BaseException as exc:
        status, error_text = "failed", f"{type(exc).__name__}: {exc}"[:2000]
        raise
    finally:
        try:
            with db.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO agent_runs (agent_name, function_label, trigger_kind,
                                            started_at, ended_at, status, usd_cost, error_text)
                    VALUES (%s, %s, %s, %s, %s, %s, 0, %s)
                    """,
                    (AGENT, function_label, trigger_kind, started_at, datetime.now(UTC),
                     status, error_text),
                )
        except Exception:
            logger.warning("failed to write the agent_runs row for %s", function_label,
                           exc_info=True)
