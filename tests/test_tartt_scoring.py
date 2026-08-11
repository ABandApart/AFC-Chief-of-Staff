"""Unit tests for Tartt interest scoring (Phase 4, Task 4).

The cosine query reads cognee's vector store (runtime); here we cover the pure
gate + guard the P4-1 decision to score on the summary vector.
"""

from __future__ import annotations

from agents.tartt import scoring


def test_should_cognify_below_threshold_is_false():
    assert scoring.should_cognify(scoring.INTEREST_THRESHOLD - 0.01) is False


def test_should_cognify_at_and_above_threshold_is_true():
    assert scoring.should_cognify(scoring.INTEREST_THRESHOLD) is True
    assert scoring.should_cognify(0.95) is True


def test_score_sql_targets_summary_not_title():
    # P4-1 caveat: ContentItem_title holds a duplicate of the summary vector
    # (cognee multi-index-field quirk), so score on ContentItem_summary only.
    sql = scoring._SCORE_SQL
    assert '"ContentItem_summary"' in sql
    assert '"InterestSignal_topic_label"' in sql
    assert "ContentItem_title" not in sql
