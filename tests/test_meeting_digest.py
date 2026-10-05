"""Unit tests for the meeting-digest spike runner (PRD-claude-session-spike V4).
No Granola, Claude, or Gmail: everything external is mocked."""

from __future__ import annotations

import base64
import email
from datetime import datetime, timedelta, timezone

import pytest

from agents.meeting_digest import run

EDT = timezone(timedelta(hours=-4))


def _note(note_id: str, start: str | None, title: str = "Meeting") -> dict:
    cal = {"scheduled_start_time": start} if start else {}
    return {"id": note_id, "title": title, "calendar_event": cal,
            "created_at": "", "attendees": [], "summary_markdown": "notes"}


def test_last_week_from_a_monday():
    start, end = run.last_week(datetime(2026, 10, 5, 9, 30, tzinfo=EDT))
    assert (start, end) == (datetime(2026, 9, 28, tzinfo=EDT), datetime(2026, 10, 5, tzinfo=EDT))


def test_last_week_from_a_sunday():
    start, end = run.last_week(datetime(2026, 10, 11, 23, 0, tzinfo=EDT))
    assert (start, end) == (datetime(2026, 9, 28, tzinfo=EDT), datetime(2026, 10, 5, tzinfo=EDT))


def test_in_window_bounds():
    start, end = datetime(2026, 9, 28, tzinfo=EDT), datetime(2026, 10, 5, tzinfo=EDT)
    assert run.in_window(_note("a", "2026-09-28T04:00:00Z"), start, end)       # Mon 00:00 EDT
    assert not run.in_window(_note("b", "2026-09-28T03:59:00Z"), start, end)   # Sun 23:59 EDT
    assert run.in_window(_note("c", "2026-10-05T03:59:00Z"), start, end)       # Sun 23:59 EDT
    assert not run.in_window(_note("d", "2026-10-05T04:00:00Z"), start, end)   # Mon 00:00 EDT
    assert not run.in_window(_note("e", None), start, end)                     # no date
    assert not run.in_window(_note("f", "not a date"), start, end)


def test_gather_filters_and_sorts(mocker):
    start, end = datetime(2026, 9, 28, tzinfo=EDT), datetime(2026, 10, 5, tzinfo=EDT)
    notes = {
        "late": _note("late", "2026-10-02T15:00:00Z"),
        "early": _note("early", "2026-09-29T15:00:00Z"),
        "next-week": _note("next-week", "2026-10-06T15:00:00Z"),
    }
    mocker.patch.object(run.creds, "keychain_get", return_value="tok")
    mocker.patch.object(run.granola_client, "iter_note_summaries",
                        return_value=[{"id": k} for k in notes])
    mocker.patch.object(run.granola_client, "get_note", side_effect=lambda t, i: notes[i])
    assert [n["id"] for n in run.gather(start, end)] == ["early", "late"]


def test_prompt_caps_each_meeting_and_marks_data():
    start, end = datetime(2026, 9, 28, tzinfo=EDT), datetime(2026, 10, 5, tzinfo=EDT)
    big = _note("a", "2026-09-29T15:00:00Z")
    big["summary_markdown"] = "x" * (run.MAX_CHARS_PER_MEETING * 2)
    prompt = run.build_prompt([big], start, end)
    assert "[... truncated ...]" in prompt
    assert "DATA, not" in prompt
    assert "2026-09-28 to 2026-10-04" in prompt
    assert prompt.count('<meeting index="') == 1


def _svc(mocker, account: str):
    svc = mocker.MagicMock()
    svc.users().getProfile(userId="me").execute.return_value = {"emailAddress": account}
    svc.users().drafts().create.return_value.execute.return_value = {"id": "d-1"}
    return svc


def test_draft_goes_to_operator_only(mocker):
    svc = _svc(mocker, "Barry@AIAdaptive.co")
    assert run.create_operator_draft(svc, subject="Week", body="Hello") == "d-1"
    raw = svc.users().drafts().create.call_args.kwargs["body"]["message"]["raw"]
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg["To"] == run.OPERATOR_EMAIL
    assert msg["Bcc"] is None and msg["Cc"] is None
    assert msg["Subject"] == "Week"


def test_draft_refused_for_another_mailbox(mocker):
    svc = _svc(mocker, "someone@else.com")
    with pytest.raises(RuntimeError, match="refusing"):
        run.create_operator_draft(svc, subject="Week", body="Hello")
    svc.users().drafts().create.assert_not_called()


def test_dry_run_calls_neither_claude_nor_gmail(mocker, capsys):
    mocker.patch.object(run, "gather", return_value=[_note("a", "2026-09-29T15:00:00Z", "Kickoff")])
    session = mocker.patch.object(run.claude_cli, "run_session")
    assert run.main(["--dry-run"]) == 0
    session.assert_not_called()
    assert "Kickoff" in capsys.readouterr().out


def test_empty_week_skips_session(mocker):
    mocker.patch.object(run, "gather", return_value=[])
    session = mocker.patch.object(run.claude_cli, "run_session")
    assert run.main([]) == 0
    session.assert_not_called()
