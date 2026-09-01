"""Unit tests for send capture (`agents/outreach/gmail_capture.py`).

Mocks the Gmail service (the barry-agent-only SDK is never imported) and the DB.
The load-bearing guarantees: only SENT messages become events, correlation is by
threadId, recording is idempotent (a duplicate history event can't re-mark a
touch), an expired watermark re-bootstraps instead of crashing, and a not-ready
capture still records (§6) while flagging the Ted alert.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from agents.outreach import gmail_capture as cap


def _conn(mocker, fetch_seq):
    cur = mocker.MagicMock()
    cur.fetchone.side_effect = list(fetch_seq)
    conn = mocker.MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


# --- watermark ---------------------------------------------------------------

def test_save_watermark_upserts_the_singleton(mocker):
    conn, cur = _conn(mocker, [])
    cap.save_watermark(conn, "h42")
    sql, params = cur.execute.call_args.args
    assert "ON CONFLICT (only_row) DO UPDATE" in sql
    assert params == ("h42",)


# --- sent_events -------------------------------------------------------------

def _svc(pages):
    svc = MagicMock()
    svc.users.return_value.history.return_value.list.return_value.execute.side_effect = pages
    return svc


def _added(mid, thread, sent=True):
    return {"message": {"id": mid, "threadId": thread,
                        "labelIds": ["SENT"] if sent else ["DRAFT"]}}


def test_sent_events_keeps_only_sent_messages():
    page = {"historyId": "h9", "history": [
        {"messagesAdded": [_added("m1", "t1", sent=True), _added("m2", "t2", sent=False)]}]}
    newest, events = cap.sent_events(_svc([page]), "h1")
    assert newest == "h9"
    assert events == [{"message_id": "m1", "thread_id": "t1"}]


def test_sent_events_paginates():
    p1 = {"historyId": "h5", "nextPageToken": "x",
          "history": [{"messagesAdded": [_added("m1", "t1")]}]}
    p2 = {"historyId": "h6", "history": [{"messagesAdded": [_added("m2", "t2")]}]}
    newest, events = cap.sent_events(_svc([p1, p2]), "h1")
    assert newest == "h6"
    assert [e["message_id"] for e in events] == ["m1", "m2"]


def test_sent_events_returns_none_on_expired_history():
    svc = MagicMock()
    err = Exception("gone")
    err.resp = SimpleNamespace(status=404)
    svc.users.return_value.history.return_value.list.return_value.execute.side_effect = err
    assert cap.sent_events(svc, "h1") == (None, [])


def test_sent_events_reraises_a_non_404():
    svc = MagicMock()
    err = Exception("boom")
    err.resp = SimpleNamespace(status=500)
    svc.users.return_value.history.return_value.list.return_value.execute.side_effect = err
    try:
        cap.sent_events(svc, "h1")
        raise AssertionError("should have re-raised")
    except Exception as exc:  # noqa: BLE001
        assert "boom" in str(exc)


# --- record_send -------------------------------------------------------------

def test_record_send_records_once_and_reports_ready(mocker):
    conn, cur = _conn(mocker, [(5,), (True,)])   # update RETURNING id, then packet ready
    out = cap.record_send(conn, 5, "m1")
    assert out == {"recorded": True, "ready": True}
    assert "sent_via = %s" in cur.execute.call_args_list[0].args[0]


def test_record_send_flags_a_not_ready_capture(mocker):
    conn, cur = _conn(mocker, [(5,), (False,)])
    assert cap.record_send(conn, 5, "m1") == {"recorded": True, "ready": False}


def test_record_send_is_idempotent_when_already_sent(mocker):
    conn, cur = _conn(mocker, [None])            # WHERE sent_at IS NULL matched nothing
    out = cap.record_send(conn, 5, "m1")
    assert out == {"recorded": False, "ready": True}
    assert cur.execute.call_count == 1           # no packet-ready query when not recorded


# --- run orchestration -------------------------------------------------------

def _run_env(mocker, *, watermark):
    mocker.patch.object(cap.db, "connection")
    cap.db.connection.return_value.__enter__.return_value = MagicMock()
    mocker.patch.object(cap, "read_watermark", return_value=watermark)
    return mocker.patch.object(cap, "save_watermark")


def test_run_first_time_bootstraps_the_watermark_and_captures_nothing(mocker):
    save = _run_env(mocker, watermark=None)
    svc = MagicMock()
    svc.users.return_value.getProfile.return_value.execute.return_value = {"historyId": "h100"}
    mocker.patch.object(cap.gmail, "service", return_value=svc)
    counts = cap.run()
    save.assert_called_once_with(mocker.ANY, "h100")
    assert counts == {"events": 0, "recorded": 0, "unmatched": 0, "not_ready": 0}


def test_run_correlates_records_and_advances_the_watermark(mocker):
    save = _run_env(mocker, watermark="h1")
    mocker.patch.object(cap.gmail, "service", return_value=MagicMock())
    mocker.patch.object(cap, "sent_events",
                        return_value=("h9", [{"message_id": "m1", "thread_id": "t1"}]))
    mocker.patch.object(cap, "correlate", return_value=7)
    mocker.patch.object(cap, "record_send", return_value={"recorded": True, "ready": False})
    counts = cap.run()
    assert counts == {"events": 1, "recorded": 1, "unmatched": 0, "not_ready": 1}
    save.assert_called_once_with(mocker.ANY, "h9")   # watermark advanced


def test_run_counts_an_unmatched_send(mocker):
    _run_env(mocker, watermark="h1")
    mocker.patch.object(cap.gmail, "service", return_value=MagicMock())
    mocker.patch.object(cap, "sent_events",
                        return_value=("h9", [{"message_id": "m1", "thread_id": "unknown"}]))
    mocker.patch.object(cap, "correlate", return_value=None)
    rec = mocker.patch.object(cap, "record_send")
    counts = cap.run()
    assert counts["unmatched"] == 1 and counts["recorded"] == 0
    rec.assert_not_called()


# --- BCC body path -----------------------------------------------------------

from email.message import EmailMessage  # noqa: E402


def _bcc_raw(token, body, *, header="Delivered-To"):
    msg = EmailMessage()
    msg[header] = f"bcc+{token}@aiadaptive.co"
    msg["Subject"] = "Re: your team"
    msg.set_content(body)
    return msg.as_bytes()


def _parsed(raw):
    from email import message_from_bytes, policy
    return message_from_bytes(raw, policy=policy.default)


def test_bcc_token_parsed_from_routing_headers():
    assert cap.bcc_token_from_headers(_parsed(_bcc_raw("tok5", "hi"))) == "tok5"
    # token can ride other routing headers too
    assert cap.bcc_token_from_headers(_parsed(_bcc_raw("t9", "hi", header="To"))) == "t9"


def test_bcc_token_none_when_absent():
    m = EmailMessage()
    m["To"] = "someone@example.com"
    m.set_content("x")
    assert cap.bcc_token_from_headers(_parsed(m.as_bytes())) is None


def test_plain_body_extracts_the_text():
    assert cap.plain_body(_parsed(_bcc_raw("t", "Verbatim body."))).strip() == "Verbatim body."


def test_record_body_writes_body_and_claims_bcc_when_unsent(mocker):
    conn, cur = _conn(mocker, [(5, True), (True,)])   # id+was_unsent, then packet ready
    out = cap.record_body(conn, "tok5", "Body.")
    assert out == {"matched": True, "newly_sent": True, "ready": True}
    upd = cur.execute.call_args_list[1].args[0]
    assert "sent_body = %s" in upd
    assert "COALESCE(sent_at, now())" in upd and "COALESCE(sent_via, 'bcc')" in upd


def test_record_body_unmatched_token(mocker):
    conn, cur = _conn(mocker, [None])
    assert cap.record_body(conn, "nope", "b") == {
        "matched": False, "newly_sent": False, "ready": True}
    assert cur.execute.call_count == 1               # no UPDATE when no touch matched


def test_capture_bcc_parses_records_and_marks_seen(mocker):
    imap = MagicMock()
    imap.search.return_value = ("OK", [b"1"])
    imap.fetch.return_value = ("OK", [(b"1 (RFC822)", _bcc_raw("tok5", "Hi there."))])
    conn = mocker.MagicMock()
    cur = mocker.MagicMock()
    cur.fetchone.side_effect = [(5, True), (True,)]
    conn.cursor.return_value.__enter__.return_value = cur
    counts = cap.capture_bcc(imap, conn)
    assert counts["seen"] == 1 and counts["bodies"] == 1
    imap.store.assert_called_once_with(b"1", "+FLAGS", "\\Seen")


def test_run_bcc_skips_without_the_credential(mocker):
    mocker.patch.object(cap.creds, "keychain_get",
                        side_effect=RuntimeError("no gmail-bcc-imap"))
    ping = mocker.patch.object(cap.heartbeat, "ping")
    assert cap.run_bcc() == {"seen": 0, "bodies": 0, "unmatched": 0, "not_ready": 0}
    ping.assert_not_called()          # skip must NOT read green — bcc@ is off, not alive


def test_run_bcc_pings_the_switch_on_a_live_pass(mocker):
    mocker.patch.object(cap.creds, "keychain_get", return_value="pw")
    mocker.patch.object(cap, "bcc_imap", return_value=MagicMock())
    mocker.patch.object(cap.db, "connection")
    mocker.patch.object(cap, "capture_bcc",
                        return_value={"seen": 2, "bodies": 2, "unmatched": 0, "not_ready": 0})
    ping = mocker.patch.object(cap.heartbeat, "ping")
    cap.run_bcc()
    ping.assert_called_once_with(cap.HEARTBEAT_SLUG_BCC)


def test_run_bcc_signals_fail_when_the_pass_raises(mocker):
    import pytest
    mocker.patch.object(cap.creds, "keychain_get", return_value="pw")
    mocker.patch.object(cap, "bcc_imap", return_value=MagicMock())
    mocker.patch.object(cap.db, "connection")
    mocker.patch.object(cap, "capture_bcc", side_effect=RuntimeError("imap wedged"))
    fail = mocker.patch.object(cap.heartbeat, "ping_fail")
    with pytest.raises(RuntimeError):
        cap.run_bcc()
    fail.assert_called_once_with(cap.HEARTBEAT_SLUG_BCC)   # a broken pass alerts now
