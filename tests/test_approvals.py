"""Unit tests for the approval-gate core (pure — no Discord / DB).

Covers the trust-boundary logic that must be correct regardless of the bot:
the state-transition guard (incl. the double-click no-op), the payload edit
merge, the envelope shape, and the handler registry lookup / missing-handler
error. The full Discord post → click → dispatch loop is validated by the
barry-agent smoke test (demo `noop_echo`).
"""

from __future__ import annotations

import pytest

from agents._lib import approvals

# --- state machine / idempotency guard ------------------------------------


def test_pending_transitions():
    assert approvals.next_status("pending", "approve") == "approved"
    assert approvals.next_status("pending", "reject") == "rejected"
    assert approvals.next_status("pending", "edit") == "edited"


def test_already_decided_is_noop():
    # A second click on any decided row returns None (execute-exactly-once).
    for decided in ("approved", "rejected", "edited"):
        assert approvals.next_status(decided, "approve") is None
        assert approvals.next_status(decided, "reject") is None


def test_unknown_action_raises_when_pending():
    with pytest.raises(ValueError):
        approvals.next_status("pending", "frobnicate")


def test_only_ship_statuses_dispatch():
    # Approve/edit run the handler; reject must not.
    assert "approved" in approvals.DISPATCH_STATUSES
    assert "edited" in approvals.DISPATCH_STATUSES
    assert "rejected" not in approvals.DISPATCH_STATUSES
    assert "pending" not in approvals.DISPATCH_STATUSES


# --- payload edit merge ---------------------------------------------------


def test_merge_edit_replaces_field_and_is_pure():
    original = {"text": "old", "channel": "linkedin"}
    merged = approvals.merge_edit(original, {"text": "new"})
    assert merged == {"text": "new", "channel": "linkedin"}
    # original is untouched (new dict returned)
    assert original["text"] == "old"


def test_merge_edit_adds_missing_field():
    assert approvals.merge_edit({}, {"text": "hi"}) == {"text": "hi"}


# --- envelope shape -------------------------------------------------------


def test_envelope_roundtrip():
    env = approvals.build_envelope("Post this draft", {"text": "hello"}, "text")
    assert approvals.envelope_summary(env) == "Post this draft"
    assert approvals.envelope_payload(env) == {"text": "hello"}
    assert approvals.envelope_edit_field(env) == "text"


def test_envelope_edit_field_defaults_to_text():
    assert approvals.envelope_edit_field({}) == "text"
    assert approvals.envelope_edit_field({"edit_field": ""}) == "text"


def test_envelope_payload_defaults_empty():
    assert approvals.envelope_payload({}) == {}
    assert approvals.envelope_payload({"payload": None}) == {}


# --- handler registry -----------------------------------------------------


def test_register_and_get_handler():
    approvals.register_handler("_unit_demo", lambda p: f"ran:{p.get('x')}")
    handler = approvals.get_handler("_unit_demo")
    assert handler({"x": 1}) == "ran:1"


def test_missing_handler_raises():
    with pytest.raises(approvals.HandlerNotRegisteredError):
        approvals.get_handler("_definitely_not_registered")


def test_noop_echo_registered_and_echoes():
    # The demo handler ships registered so the smoke test can drive the loop.
    handler = approvals.get_handler("noop_echo")
    assert handler({"text": "ping"}) == "echoed: ping"
