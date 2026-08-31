"""Unit tests for Gmail drafting (`agents/outreach/gmail.py`).

The Google SDK is the barry-agent-only `gmail` group, so these mock the service
and never import it — the module must load and test green on the build box. What
matters: the draft carries the BCC token address (the only correlation key that
survives, post-V1), drafting is create-once (no duplicate drafts, G-R4), and
nothing here sends (G3).
"""

from __future__ import annotations

import base64
from email import message_from_bytes, policy
from unittest.mock import MagicMock

from agents.outreach import gmail


def test_bcc_address_carries_the_token_on_the_dedicated_mailbox():
    assert gmail.bcc_address("abc123") == "bcc+abc123@aiadaptive.co"


def test_body_hash_is_stable_and_ignores_edge_whitespace():
    assert gmail.body_hash("hello") == gmail.body_hash("  hello\n")
    assert gmail.body_hash("hello") != gmail.body_hash("hell0")


def test_build_raw_sets_to_bcc_subject_and_body():
    raw = gmail.build_raw(to="jane@aiir.co", subject="Re: your team",
                          body="Body with [operator] slot.", bcc="bcc+t9@aiadaptive.co")
    msg = message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)
    assert msg["To"] == "jane@aiir.co"
    assert msg["Bcc"] == "bcc+t9@aiadaptive.co"     # the correlation key rides here
    assert msg["Subject"] == "Re: your team"
    assert "[operator] slot" in msg.get_content()


def _fake_service(draft_id="d1", thread_id="th1", message_id="m1"):
    svc = MagicMock()
    svc.users.return_value.drafts.return_value.create.return_value.execute.return_value = {
        "id": draft_id, "message": {"id": message_id, "threadId": thread_id}}
    return svc


def test_create_draft_posts_the_raw_and_returns_the_ids():
    svc = _fake_service()
    ids = gmail.create_draft(svc, to="jane@aiir.co", subject="s", body="b",
                             bcc="bcc+t@aiadaptive.co")
    assert ids == {"draft_id": "d1", "thread_id": "th1", "message_id": "m1"}
    # It is a DRAFTS.CREATE, never a send (G3).
    svc.users.return_value.drafts.return_value.create.assert_called_once()
    body = svc.users.return_value.drafts.return_value.create.call_args.kwargs["body"]
    assert "raw" in body["message"]


def test_list_draftable_sql_is_create_once_and_active_only(mocker):
    cur = mocker.MagicMock()
    cur.fetchall.return_value = []
    conn = mocker.MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    gmail.list_draftable(conn)
    sql = cur.execute.call_args.args[0]
    assert "gmail_draft_id IS NULL" in sql          # create-once (no duplicate drafts)
    assert "status = 'in_sequence'" in sql          # only active sequences
    assert "sent_at IS NULL" in sql and "skipped_at IS NULL" in sql


_TOUCH = {"touch_id": 5, "bcc_token": "tok5", "contact_email": "jane@aiir.co",
          "company_name": "AIIR", "subject_line": "Re: your team",
          "body_filled": "Body [operator]."}


def test_run_dry_run_makes_no_gmail_call(mocker):
    mocker.patch.object(gmail, "list_draftable", return_value=[_TOUCH])
    mocker.patch.object(gmail.db, "connection")
    svc = mocker.patch.object(gmail, "service")
    counts = gmail.run(dry_run=True)
    assert counts == {"draftable": 1, "created": 0}
    svc.assert_not_called()                          # dry-run touches no token, no Gmail


def test_run_creates_a_draft_and_saves_state(mocker):
    mocker.patch.object(gmail, "list_draftable", return_value=[_TOUCH])
    mocker.patch.object(gmail.db, "connection")
    mocker.patch.object(gmail, "service", return_value=_fake_service())
    save = mocker.patch.object(gmail, "save_draft_state")
    counts = gmail.run()
    assert counts == {"draftable": 1, "created": 1}
    # State persisted: touch id, the returned ids, and the body hash.
    args = save.call_args.args
    assert args[1] == 5 and args[2]["draft_id"] == "d1"
    assert args[3] == gmail.body_hash("Body [operator].")
