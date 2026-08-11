"""Interest scoring for Tartt (Phase 4, Task 4).

An article's `interest_score` is the **max cosine** of its `ContentItem` summary
vector against the operator's `InterestSignal` vectors (both local bge @768, same
space). Per the P4-1 probe: cognee stores one pgvector table per DataPoint-type ×
index-field, keyed by the DataPoint's `id` directly, so a single SQL join scores
one item against every interest. Score on **`ContentItem_summary`** — the
`ContentItem_title` table is a cognee multi-index-field quirk (it holds a
duplicate of the summary vector, not a distinct title signal).

The score gates the expensive downstream: only items above `INTEREST_THRESHOLD`
get the mode-1 cognify (Anthropic extraction); the cheap typed ContentItem is
added for all. The vectors live in `aiadaptive_cognee` (read here); the score is
written to `content_items.interest_score` in `aiadaptive_cos` by the caller.
"""

from __future__ import annotations

import psycopg

from agents._lib import cognee_setup, creds

# max cosine of a ContentItem's summary vs all InterestSignals. Empty interests
# or a not-yet-embedded node → NULL → the caller reads it as 0.0.
_SCORE_SQL = """
    SELECT max(1 - (c.vector <=> s.vector))
    FROM "ContentItem_summary" c, "InterestSignal_topic_label" s
    WHERE c.id = %s
"""

# Cosine gate for mode-1 cognify. bge cosine runs high even for unrelated text
# (~0.28 in the probe) and ~0.9 for a strong match, so a mid threshold gates
# "relevant to at least one interest". A starting value — tune against real data.
INTEREST_THRESHOLD = 0.40


# A higher bar than cognify: a task candidate asks for the operator's attention
# (Task Tinder), so only strongly-relevant items become one. Tunable.
TASK_CANDIDATE_THRESHOLD = 0.55


def should_cognify(score: float) -> bool:
    """Whether an item is interesting enough for the deep (mode-1) cognify."""
    return score >= INTEREST_THRESHOLD


def is_task_worthy(score: float) -> bool:
    """Whether an item is relevant enough to propose as a task candidate."""
    return score >= TASK_CANDIDATE_THRESHOLD


def score_content_item(content_node_id: str) -> float:
    """Max cosine of this ContentItem's summary vs all InterestSignal vectors.

    Reads `aiadaptive_cognee` (cognee's vector store). Returns 0.0 when the node
    has no summary vector yet or there are no InterestSignals to score against.
    """
    dsn = cognee_setup.cognee_dsn(creds.keychain_get("db-url"))
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(_SCORE_SQL, (content_node_id,))
        row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0
