"""Shared library code for AFC Richmond agents.

Phase 2: cost-emission helper (`runs.py`). Phase 3.7: telemetry labeling for
cognee (`telemetry_context.py`).
"""

from agents._lib.runs import (
    DAILY_CEILINGS,
    PRICE_TABLE,
    DailyCeilingExceeded,
    RunContext,
    agent_run,
    assert_under_ceiling,
)

__all__ = [
    "DAILY_CEILINGS",
    "PRICE_TABLE",
    "DailyCeilingExceeded",
    "RunContext",
    "agent_run",
    "assert_under_ceiling",
]
