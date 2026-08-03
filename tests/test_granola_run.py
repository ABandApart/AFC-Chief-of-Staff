"""Unit test for the Granola poller's pure go-forward seed decision.

The poll flow itself (HTTP + cognee) is exercised by runtime validation; the one
piece of pure logic is *whether* a run seeds the watermark forward (skipping
history) vs. proceeds to ingest.
"""

from __future__ import annotations

from agents.granola.run import MAX_NOTES_PER_RUN, go_forward_seed_needed


def test_seed_only_on_empty_watermark_without_backfill():
    assert go_forward_seed_needed(None, backfill=False) is True       # first run → seed forward
    assert go_forward_seed_needed(None, backfill=True) is False        # --backfill → ingest history
    assert go_forward_seed_needed("2026-08-01T00:00:00Z", backfill=False) is False  # have watermark
    assert go_forward_seed_needed("2026-08-01T00:00:00Z", backfill=True) is False


def test_per_run_cap_is_sane():
    assert 1 <= MAX_NOTES_PER_RUN <= 30  # bounded work per cycle; ≤ API page size
