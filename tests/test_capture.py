"""Unit tests for the capture cog's pure helper.

Post-W4 (cognee pivot) the extraction/embedding/facts-table path is gone —
cognee ingests notes into the graph. The only pure, unit-testable helper left
is `message_hash` (the message-level dedup key); the ingest flow itself is an
async cognee call exercised by the runtime capture validation.
"""

from __future__ import annotations

from agents.discord_bot.cogs.capture import message_hash


def test_hash_is_stable_for_identical_text():
    assert message_hash("Hello world.") == message_hash("Hello world.")


def test_hash_ignores_whitespace_and_case():
    # collapse whitespace runs + casefold → same hash
    assert message_hash("Hello   world.") == message_hash("hello world.")
    assert message_hash(" Hello\nworld. ") == message_hash("hello world.")


def test_hash_differs_for_different_words():
    # the 3b/dedup lesson: paraphrases are NOT the same note
    a = message_hash("There is a note that is testing deduplication.")
    b = message_hash("There is a note being tested for deduplication.")
    assert a != b


def test_hash_is_hex_sha256():
    h = message_hash("anything")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
