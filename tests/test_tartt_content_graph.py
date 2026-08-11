"""Unit tests for Tartt's typed ContentItem builder (Phase 4, Task 3).

Deterministic ids are the load-bearing property (a re-seen URL must upsert to one
node, not duplicate). The graph write (`add_content_item`) needs cognee and is
runtime-verified; the builder + id are pure and tested here.
"""

from __future__ import annotations

from agents.tartt import content_graph


def test_content_item_id_is_deterministic():
    assert content_graph.content_item_id("https://ex.com/1") == content_graph.content_item_id(
        "https://ex.com/1"
    )


def test_content_item_id_strips_whitespace():
    assert content_graph.content_item_id("  https://ex.com/1 ") == content_graph.content_item_id(
        "https://ex.com/1"
    )


def test_content_item_id_differs_by_url():
    assert content_graph.content_item_id("https://ex.com/1") != content_graph.content_item_id(
        "https://ex.com/2"
    )


def test_build_content_item_sets_fields_and_id():
    item = content_graph.build_content_item("https://ex.com/1", "  Title  ", "a summary")
    assert item.id == content_graph.content_item_id("https://ex.com/1")
    assert item.url == "https://ex.com/1"
    assert item.title == "Title"
    assert item.summary == "a summary"


def test_build_content_item_summary_none_passthrough():
    item = content_graph.build_content_item("https://ex.com/1", "T", None)
    assert item.summary is None


# --- InterestSignal (Task 4) -------------------------------------------------


def test_interest_signal_id_is_case_insensitive():
    # Topics are case-insensitive concepts → re-seeding upserts to one node.
    assert content_graph.interest_signal_id("AI Agents") == content_graph.interest_signal_id(
        "ai agents"
    )


def test_interest_signal_id_differs_by_topic():
    assert content_graph.interest_signal_id("AI") != content_graph.interest_signal_id("cooking")


def test_build_interest_signal_sets_fields_and_id():
    s = content_graph.build_interest_signal("  Dev tools  ", weight=2.0)
    assert s.id == content_graph.interest_signal_id("Dev tools")
    assert s.topic_label == "Dev tools"
    assert s.weight == 2.0
