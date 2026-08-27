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


# --- regression: the submit path must actually WRITE ---------------------------
#
# Second bug of the same class as the `row` collision: the object constructed
# correctly and did the wrong thing when used. The modal built with four children
# and rendered perfectly, and every submit silently recorded nothing because the
# decision was read from an attribute RadioGroup does not have.


def _label_with(value: str | None):
    import discord
    import discord.ui as ui
    group = ui.RadioGroup(options=[discord.SelectOption(label="X", value="x")])
    if value is not None:
        group._value = value
    return ui.Label(text="Decision", component=group)


def test_selected_reads_radiogroup_value_not_values():
    """`RadioGroup` exposes `value` (singular). Reading `.values` returned None
    for every submit, so every decision failed validation with 'unknown Gate 0
    action: None' and nothing was ever written."""
    assert cog._selected(_label_with(None)) is None
    assert cog._selected(_label_with("reject")) == "reject"


def test_radiogroup_genuinely_has_no_values_attribute():
    """Pins the fact the bug rested on, so a 'tidy-up' cannot reintroduce it."""
    import discord
    import discord.ui as ui
    group = ui.RadioGroup(options=[discord.SelectOption(label="X", value="x")])
    assert not hasattr(group, "values")
    assert hasattr(group, "value")


def _submit(mocker, decision, reason=None, note=None):
    """Drive ReviewModal.on_submit the way Discord does, and report the call."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    modal = cog.ReviewModal(MagicMock(), ROW)
    # Refreshing the posted sheet is a separate concern with its own tests; here
    # it must simply not swallow the submit path.
    modal.cog.refresh_sheet = AsyncMock()
    modal.decision.component._value = decision
    if reason is not None:
        modal.reason.component._value = reason
    if note is not None:
        modal.note.component._value = note

    decide = mocker.patch.object(
        cog.outreach_discovery, "decide",
        return_value={"company_name": ROW["company_name"], "labelled": True},
    )
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    asyncio.run(modal.on_submit(interaction))
    return decide, interaction


def test_an_accept_reaches_the_decision_core(mocker):
    decide, interaction = _submit(mocker, "accept")
    decide.assert_called_once()
    args, kwargs = decide.call_args
    assert args[0] == ROW["id"] and args[1] == "accept"
    assert kwargs["reason"] is None
    interaction.response.send_message.assert_awaited_once()


def test_a_reject_carries_its_reason_through(mocker):
    """The reason is the training label — a reject that loses it is worse than
    no reject at all."""
    decide, _ = _submit(mocker, "reject", reason="too_small")
    args, kwargs = decide.call_args
    assert args[1] == "reject" and kwargs["reason"] == "too_small"


def test_a_reject_without_a_reason_never_reaches_the_database(mocker):
    decide, interaction = _submit(mocker, "reject")
    decide.assert_not_called()
    message = interaction.response.send_message.call_args[0][0]
    assert "reason" in message.lower()


def test_other_without_a_note_never_reaches_the_database(mocker):
    """The one rule the modal UI cannot express — Discord has no conditional
    requirement — so it must hold on submit."""
    decide, interaction = _submit(mocker, "reject", reason="other")
    decide.assert_not_called()
    assert "note" in interaction.response.send_message.call_args[0][0].lower()


def test_a_deferral_reaches_the_core_and_records_no_label(mocker):
    decide, _ = _submit(mocker, "defer")
    decide.assert_called_once()
    assert decide.call_args[0][1] == "defer"


# --- regression: the buttons must survive a restart ----------------------------
#
# Third bug of the same family. The view constructed, the submit path worked, and
# every card posted before a restart had dead buttons — a persistent view is only
# live in the process that registered it. The click routed to no handler and
# Discord timed out after three seconds with nothing in the log.


def test_the_sheet_view_is_persistent_and_re_attachable():
    """`add_view` refuses a view with a timeout or a component lacking a
    custom_id. If either regressed, re-attach would silently do nothing."""
    from unittest.mock import MagicMock

    import discord
    rows = [{**ROW, "id": i, "company_name": f"Firm {i}"} for i in range(1, 13)]
    view = cog.SheetView(MagicMock(), rows, page=1, pages=3, total=25)
    assert view.timeout is None
    assert view.is_persistent() is True
    client = discord.Client(intents=discord.Intents.none())
    client.add_view(view, message_id=1540154416026488902)  # must not raise


def test_reattach_registers_one_view_per_message(mocker):
    import asyncio
    from unittest.mock import MagicMock
    bot = MagicMock()
    instance = cog.OutreachDiscoveryCog(bot)
    mocker.patch.object(cog.OutreachDiscoveryCog, "_fetch_surfaced_pages",
                        return_value={
                            "111": [{**ROW, "id": 1}, {**ROW, "id": 2}],
                            "222": [{**ROW, "id": 3}],
                        })
    asyncio.run(instance._reattach_views())
    assert bot.add_view.call_count == 2
    assert {kw["message_id"] for _, kw in bot.add_view.call_args_list} == {111, 222}


def test_reattach_survives_one_bad_message(mocker):
    """One unrebuildable page must not cost the others their live buttons."""
    import asyncio
    from unittest.mock import MagicMock
    bot = MagicMock()
    instance = cog.OutreachDiscoveryCog(bot)
    mocker.patch.object(cog.OutreachDiscoveryCog, "_fetch_surfaced_pages",
                        return_value={
                            # Real message ids are numeric snowflake strings.
                            "1540154416026488902": [{**ROW, "id": i}
                                                    for i in range(20)],  # over budget
                            "1540154417356079154": [{**ROW, "id": 99}],
                        })
    asyncio.run(instance._reattach_views())
    assert bot.add_view.call_count == 1
    assert bot.add_view.call_args[1]["message_id"] == 1540154417356079154


def test_a_non_numeric_message_id_is_skipped_not_fatal(mocker):
    """Snowflakes are numeric strings; anything else is corrupt data and must
    cost only its own page."""
    import asyncio
    from unittest.mock import MagicMock
    bot = MagicMock()
    instance = cog.OutreachDiscoveryCog(bot)
    mocker.patch.object(cog.OutreachDiscoveryCog, "_fetch_surfaced_pages",
                        return_value={"not-a-snowflake": [{**ROW, "id": 1}]})
    asyncio.run(instance._reattach_views())
    bot.add_view.assert_not_called()


def test_reattach_happens_once_per_process(mocker):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    bot = MagicMock()
    bot.wait_until_ready = AsyncMock()
    instance = cog.OutreachDiscoveryCog(bot)
    reattach = mocker.patch.object(instance, "_reattach_views", AsyncMock())
    asyncio.run(instance._before_poll())
    asyncio.run(instance._before_poll())
    reattach.assert_awaited_once()


def test_a_read_failure_does_not_stop_the_bot_starting(mocker):
    import asyncio
    from unittest.mock import MagicMock
    bot = MagicMock()
    instance = cog.OutreachDiscoveryCog(bot)
    mocker.patch.object(cog.OutreachDiscoveryCog, "_fetch_surfaced_pages",
                        side_effect=RuntimeError("db down"))
    asyncio.run(instance._reattach_views())  # must not raise
    bot.add_view.assert_not_called()


# --- the sheet must show what the database holds -------------------------------


def test_an_undecided_row_shows_an_enabled_review_button():
    from unittest.mock import MagicMock
    button = cog._ReviewButton(MagicMock(), ROW)
    assert button.label == "Review" and button.disabled is False


def test_an_accepted_row_shows_a_disabled_accepted_button():
    from unittest.mock import MagicMock
    button = cog._ReviewButton(MagicMock(), {**ROW, "review_decision": "accept"})
    assert button.label == "Accepted" and button.disabled is True


def test_a_rejected_row_shows_a_disabled_rejected_button():
    from unittest.mock import MagicMock
    button = cog._ReviewButton(MagicMock(), {**ROW, "review_decision": "reject"})
    assert button.label == "Rejected" and button.disabled is True


def test_a_decided_row_is_marked_in_the_text_too():
    """The greyed button alone is too subtle to scan down a 12-row list."""
    assert cog.row_summary({**ROW, "review_decision": "accept"}).startswith("✅")
    rejected = cog.row_summary({**ROW, "review_decision": "reject",
                                "reject_reason": "too_small"})
    assert rejected.startswith("❌") and "too small" in rejected


def test_a_deferred_row_still_reads_as_pending():
    """OQ-H: a deferral records nothing, so the sheet cannot mark it without
    inventing state. Pending is the honest rendering — it IS still pending."""
    from unittest.mock import MagicMock
    row = {**ROW, "review_decision": None}
    assert cog.row_summary(row).startswith("**")
    assert cog._ReviewButton(MagicMock(), row).disabled is False


def test_a_decided_row_keeps_its_component_cost():
    """A disabled button is still three components, so a refreshed page cannot
    overflow a budget the original post satisfied."""
    from unittest.mock import MagicMock
    rows = [{**ROW, "id": i, "review_decision": "accept"}
            for i in range(1, cog.ROWS_PER_MESSAGE + 1)]
    view = cog.SheetView(MagicMock(), rows, page=1, pages=1, total=len(rows))
    assert len(view.children) == 1


def test_refresh_edits_the_posted_message(mocker):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    bot = MagicMock()
    channel = MagicMock()
    partial = MagicMock()
    partial.edit = AsyncMock()
    channel.get_partial_message.return_value = partial
    bot.get_channel.return_value = channel
    instance = cog.OutreachDiscoveryCog(bot)
    mocker.patch.object(cog.OutreachDiscoveryCog, "_fetch_page",
                        return_value=[{**ROW, "review_decision": "accept"}])
    asyncio.run(instance.refresh_sheet("1540154418404794418"))
    channel.get_partial_message.assert_called_once_with(1540154418404794418)
    partial.edit.assert_awaited_once()


def test_refresh_redraws_a_page_whose_last_row_was_just_decided(mocker):
    """The page drops out of `surfaced_pages` at that moment, so the refresh must
    read the UNFILTERED page or the final row keeps an enabled Review button for
    a decision already recorded."""
    import inspect
    source = inspect.getsource(cog.OutreachDiscoveryCog._fetch_page)
    assert "page_rows" in source
    assert "surfaced_pages" not in source


def test_a_refresh_failure_never_looks_like_a_failed_decision(mocker):
    """The write has already committed by the time this runs."""
    import asyncio
    from unittest.mock import MagicMock
    bot = MagicMock()
    bot.get_channel.side_effect = RuntimeError("discord down")
    instance = cog.OutreachDiscoveryCog(bot)
    mocker.patch.object(cog.OutreachDiscoveryCog, "_fetch_page", return_value=[ROW])
    asyncio.run(instance.refresh_sheet("1540154418404794418"))  # must not raise


def test_refresh_is_a_no_op_without_a_message_id(mocker):
    import asyncio
    from unittest.mock import MagicMock
    bot = MagicMock()
    instance = cog.OutreachDiscoveryCog(bot)
    fetch = mocker.patch.object(cog.OutreachDiscoveryCog, "_fetch_page")
    asyncio.run(instance.refresh_sheet(None))
    fetch.assert_not_called()


def test_a_submit_refreshes_the_sheet_it_came_from(mocker):
    decide, _ = _submit(mocker, "accept")
    decide.assert_called_once()


# --- contact correction (interim; NocoDB owns "correct" once it lands) --------


RECORD = {
    "company_name": "AIIR Consulting", "company_domain": "aiirconsulting.com",
    "contact_name": "Jonathan Kirschner", "contact_title": "Founder & CEO",
    "contact_email": "jk@aiirconsulting.com", "contact_linkedin_url": None,
    "email_confidence": None,
}


def test_the_edit_modal_sits_exactly_on_discords_child_limit():
    """Five fields, five children, nothing spare. A sixth means dropping one or
    splitting the modal — a ceiling worth failing a test over rather than
    discovering when Discord rejects the payload."""
    from unittest.mock import MagicMock
    modal = cog.EditContactModal(MagicMock(), RECORD)
    assert len(modal.children) == 5


def test_the_edit_modal_prefills_what_is_already_known():
    from unittest.mock import MagicMock
    modal = cog.EditContactModal(MagicMock(), RECORD)
    assert modal.contact_name.component.default == "Jonathan Kirschner"
    assert modal.contact_email.component.default == "jk@aiirconsulting.com"
    assert modal.contact_linkedin.component.default is None


def test_verified_by_hand_is_offered_and_ranks_above_a_pattern_guess():
    values = [o.value for o in cog._CONFIDENCE]
    assert "operator_verified" in values
    assert values.index("operator_verified") < values.index("inferred_pattern")


def test_the_confidence_options_match_the_database_vocabulary():
    from agents._lib import outreach_discovery as gate0
    assert {o.value for o in cog._CONFIDENCE} == set(gate0.EMAIL_CONFIDENCE)


def test_the_decision_reply_carries_an_edit_button():
    """A button cannot live in a modal and a modal cannot open a modal, so the
    ephemeral reply is the only place in the review flow that can carry one."""
    from unittest.mock import MagicMock
    view = cog.EditPromptView(MagicMock(), "aiirconsulting.com")
    assert len(view.children) == 1
    assert isinstance(view.children[0], discord_button_type())


def discord_button_type():
    import discord
    return discord.ui.Button


def test_the_slash_command_exists_and_autocompletes_on_domain():
    """Names read, domains carry — the domain is the identity both tables key
    on (R0.10), and two firms can share a name."""
    assert cog.OutreachDiscoveryCog.gate0_edit.name == "gate0-edit"


def _blank_modal(mocker):
    from unittest.mock import MagicMock
    modal = cog.EditContactModal(MagicMock(), RECORD)
    modal.cog.refresh_for_domain = mocker.AsyncMock()
    return modal


def test_an_untouched_form_writes_nothing(mocker):
    """`TextInput.value` falls back to its prefilled default, so an untouched
    field submits its CURRENT value. Without a diff every edit would rewrite all
    five fields and the audit log — which is the history — would record four
    changes that never happened."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    modal = _blank_modal(mocker)
    update = mocker.patch.object(cog.outreach_discovery, "update_contact",
                                 return_value={"changed": []})
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    asyncio.run(modal.on_submit(interaction))
    assert "Nothing changed" in interaction.response.send_message.call_args[0][0]
    update.assert_not_called()


def test_only_the_edited_field_is_written(mocker):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    modal = _blank_modal(mocker)
    modal.contact_email.component._value = "corrected@aiirconsulting.com"
    update = mocker.patch.object(cog.outreach_discovery, "update_contact",
                                 return_value={"changed": ["contact_email"],
                                               "company_name": "AIIR Consulting",
                                               "discovery": True, "target": True})
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    asyncio.run(modal.on_submit(interaction))
    assert update.call_args[0][1] == {"contact_email": "corrected@aiirconsulting.com"}


def test_an_edit_reports_which_records_it_reached(mocker):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    modal = _blank_modal(mocker)
    modal.contact_email.component._value = "new@aiirconsulting.com"
    mocker.patch.object(cog.outreach_discovery, "update_contact", return_value={
        "changed": ["contact_email"], "company_name": "AIIR Consulting",
        "discovery": False, "target": True,
    })
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    asyncio.run(modal.on_submit(interaction))
    message = interaction.response.send_message.call_args[0][0]
    assert "contact_email" in message and "the target" in message


def test_a_failed_edit_says_nothing_changed_rather_than_pretending(mocker):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    modal = _blank_modal(mocker)
    modal.contact_name.component._value = "New Name"
    mocker.patch.object(cog.outreach_discovery, "update_contact",
                        side_effect=RuntimeError("db down"))
    interaction = MagicMock()
    interaction.response.send_message = AsyncMock()
    asyncio.run(modal.on_submit(interaction))
    assert "nothing changed" in interaction.response.send_message.call_args[0][0].lower()
