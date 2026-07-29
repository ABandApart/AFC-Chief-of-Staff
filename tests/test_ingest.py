"""Unit tests for the channel-agnostic ingest core (W5).

`message_hash` is the only pure helper (the ingest flow is an async cognee call
exercised by runtime validation). Same dedup-key behavior as pre-pivot capture.
"""

from __future__ import annotations

import inspect

from agents._lib.ingest import CAPTURE_DATASET, ingest_note, message_hash


def test_hash_stable_for_identical_text():
    assert message_hash("Hello world.") == message_hash("Hello world.")


def test_hash_ignores_whitespace_and_case():
    assert message_hash("Hello   world.") == message_hash("hello world.")
    assert message_hash(" Hello\nworld. ") == message_hash("hello world.")


def test_hash_differs_for_different_words():
    a = message_hash("There is a note that is testing deduplication.")
    b = message_hash("There is a note being tested for deduplication.")
    assert a != b


def test_hash_is_hex_sha256():
    h = message_hash("anything")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def test_ingest_note_defaults_preserve_discord_behavior():
    """The Track C dataset/label overrides must default to the original Discord
    capture values, so existing callers (cogs/capture.py) are unchanged."""
    params = inspect.signature(ingest_note).parameters
    assert params["dataset"].default == CAPTURE_DATASET == "capture"
    assert params["label_agent"].default == "fact-extraction"
    assert params["label_function"].default == "customer_discovery"
