"""Graph-native recall via cognee GRAPH_COMPLETION (W5).

Thin operator-facing entry point over the scoped retrieval wrapper
(`agents/_lib/retrieval.py`, Phase 3.8). `/recall` and `cli/recall.py` call in
here; the actual graph search call now lives (once, scoped) in the wrapper, so
B1's read-side scoping is enforced structurally rather than by convention.

Recall reads the **UNTRUSTED** scope (capture / granola / outreach_public) — the
operator's general "what do we know about X" question — and returns the
synthesized answer string. `answer_text` / `NO_RESULT` are re-exported from the
wrapper for backward compatibility.

`configure_cognee()` must have run first. cognee is imported lazily inside the
wrapper.
"""

from __future__ import annotations

from agents._lib import retrieval
from agents._lib.retrieval import NO_RESULT
from agents._lib.retrieval import normalize_answer as answer_text

__all__ = ["NO_RESULT", "answer_text", "recall"]


async def recall(query: str) -> str:
    """Answer a recall query from the graph. Returns the synthesized answer.

    Delegates to the scoped wrapper at `Scope.UNTRUSTED` (the general recall
    surface), labeled as a manual `/recall` invocation for telemetry.
    """
    result = await retrieval.recall(
        query, scope=retrieval.Scope.UNTRUSTED, agent="recall", trigger_kind="manual"
    )
    return result.answer
