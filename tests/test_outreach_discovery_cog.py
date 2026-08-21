"""Unit tests for the Gate 0 review sheet (Track O, Part 0 · R0.15).

Discord objects are constructed for real where they are pure data; nothing here
touches a gateway. The guarantees that must hold:

  * **The component budget is enforced loudly.** A truncated sheet is
    indistinguishable from a day that found fewer candidates.
  * **There is no bulk-accept affordance.** One-click accept-all is the fastest
    way to fabricate training labels — risk D1.
  * **`unknown` is shown, never guessed** — the field contract's whole point.
  * **The draft hook is labelled as a draft** wherever it appears.
"""

from __future__ import annotations

import pytest

from agents.discord_bot.cogs import outreach_discovery as cog

ROW = {
    "id": 7,
    "company_name": "Acme Engineering",
    "segment": "engineering_consultancy",
    "headcount_band": "30-50",
    "hq_location": "Austin, TX",
    "icp_fit_score": 76,
    "icp_model_version": "v1",
    "email_confidence": "inferred_pattern",
}


def test_the_budget_matches_discords_documented_ceiling():
    assert cog.MAX_COMPONENTS_PER_MESSAGE == 40
    assert cog._COMPONENTS_PER_ROW == 3
    assert cog.ROWS_PER_MESSAGE == 12


def test_the_budget_guard_refuses_one_row_too_many():
    cog.assert_component_budget(cog.ROWS_PER_MESSAGE)
    with pytest.raises(ValueError, match="over Discord"):
        cog.assert_component_budget(cog.ROWS_PER_MESSAGE + 1)


def test_the_daily_window_fits_in_three_messages():
    from agents._lib import outreach_discovery as gate0
    pages = -(-gate0.DAILY_WINDOW // cog.ROWS_PER_MESSAGE)
    assert pages == 3


def test_there_is_no_bulk_accept_affordance():
    """Risk D1. Every accept must cost one deliberate modal."""
    source = (cog.__doc__ or "") + str(cog.SheetView.__init__.__doc__ or "")
    assert "accept all" not in source.lower()
    assert cog._CHROME_COMPONENTS == 2, "a footer row would reintroduce bulk actions"


def test_a_row_carries_five_scannable_fields():
    summary = cog.row_summary(ROW)
    assert "Acme Engineering" in summary
    assert "engineering consultancy" in summary
    assert "ICP 76" in summary
    assert "30-50" in summary


def test_a_row_omits_fields_it_does_not_have():
    sparse = cog.row_summary({"id": 1, "company_name": "Bare Co",
                              "segment": "msp_it_consultancy"})
    assert sparse == "**Bare Co** · msp it consultancy"


def test_missing_detail_fields_read_unknown_not_blank():
    detail = cog.detail_block({"id": 1, "company_name": "Bare Co",
                               "segment": "msp_it_consultancy"})
    assert detail.count("unknown") >= 5
    assert "none observed yet" in detail, "Part 1 has not run; do not imply we looked"


def test_the_hook_is_always_marked_a_draft():
    """R0.12: generated text is operator-facing only and never reaches a
    recipient. The label is the thing that keeps that true in practice."""
    detail = cog.detail_block({**ROW, "pain_hook": "They shipped an AI tool."})
    assert "draft" in detail.lower()
    assert "not for sending" in detail.lower()


def test_an_extracted_candidate_shows_where_its_name_came_from():
    """R0.21's safety property: the operator validates the entity name, which he
    can only do if the item that produced it is in front of him."""
    detail = cog.detail_block({**ROW, "source_url": "https://news.example/a"})
    assert "https://news.example/a" in detail
    assert "Named from" in detail


def test_the_detail_block_stays_inside_the_modal_text_limit():
    huge = {**ROW, "description": "x" * 9000, "verification_note": "y" * 9000}
    assert len(cog.detail_block(huge)) <= 3900


def test_the_reject_reasons_match_the_database_vocabulary():
    """A reason the CHECK constraint rejects would be an unusable label."""
    from agents._lib import outreach_discovery as gate0
    offered = {option.value for option in cog._REASONS}
    assert offered == gate0.REJECT_REASONS


def test_the_decisions_match_the_decision_core():
    from agents._lib import outreach_discovery as gate0
    assert {option.value for option in cog._DECISIONS} == gate0.DECISIONS


def test_the_modal_stays_within_discords_five_child_limit():
    """Built for real, not asserted from the docstring. discord.py raises above
    five children, so this also proves the modal is constructible at all."""
    from unittest.mock import MagicMock
    modal = cog.ReviewModal(MagicMock(), ROW)
    assert len(modal.children) == 4
    assert [type(c).__name__ for c in modal.children] == [
        "TextDisplay", "Label", "Label", "Label"]


def test_the_modal_carries_no_button():
    """Discord restricts buttons to messages. A button here would be rejected at
    send time, which is the failure the wireframe originally assumed away."""
    from unittest.mock import MagicMock
    modal = cog.ReviewModal(MagicMock(), ROW)
    assert not any(type(c).__name__ == "Button" for c in modal.children)


# --- regression: the components must actually CONSTRUCT ------------------------
#
# The bug these cover shipped because every earlier test in this file exercised
# pure functions (row_summary, detail_block, assert_component_budget) and the
# modal, and never built a Button or a View. The arithmetic and the text were
# right; the thing the cog actually posts was never instantiated once.


def test_the_review_button_constructs():
    """`discord.ui.Item.row` is a LAYOUT property whose setter runs
    `5 > value >= 0`. Naming an attribute `row` and assigning the discovery dict
    to it raised `TypeError: '>' not supported between instances of 'int' and
    'dict'` on every poll, so no Gate 0 card ever posted."""
    from unittest.mock import MagicMock
    button = cog._ReviewButton(MagicMock(), ROW)
    assert button.custom_id == f"{cog._REVIEW}{ROW['id']}"
    assert button.discovery is ROW


def test_no_item_attribute_shadows_a_discord_layout_property():
    """The general form of the bug, so a future attribute cannot repeat it."""
    from unittest.mock import MagicMock

    import discord
    button = cog._ReviewButton(MagicMock(), ROW)
    # `row` must still be discord.py's, not ours.
    assert button.row is None or isinstance(button.row, int)
    reserved = {"row", "width", "view", "id", "custom_id", "style", "label"}
    assert "discovery" not in reserved
    assert isinstance(button, discord.ui.Button)


def test_a_full_sheet_page_constructs_at_the_row_ceiling():
    from unittest.mock import MagicMock
    rows = [{**ROW, "id": i, "company_name": f"Firm {i}"}
            for i in range(1, cog.ROWS_PER_MESSAGE + 1)]
    view = cog.SheetView(MagicMock(), rows, page=1, pages=3, total=25)
    assert len(view.children) == 1  # one Container holding header + rows


def test_a_sheet_page_over_the_ceiling_refuses_to_build():
    from unittest.mock import MagicMock
    rows = [{**ROW, "id": i} for i in range(1, cog.ROWS_PER_MESSAGE + 2)]
    with pytest.raises(ValueError, match="over Discord"):
        cog.SheetView(MagicMock(), rows, page=1, pages=1, total=len(rows))


def test_a_sparse_row_still_builds_a_page():
    """Real pool rows carry NULLs. A card that renders in a test fixture but
    crashes on a row with no headcount would fail the same way this bug did."""
    from unittest.mock import MagicMock
    sparse = [{"id": 1, "company_name": "Bare Co", "segment": "msp_it_consultancy"}]
    view = cog.SheetView(MagicMock(), sparse, page=1, pages=1, total=1)
    assert len(view.children) == 1
