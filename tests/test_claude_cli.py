"""Unit tests for the headless Claude CLI wrapper (PRD-claude-session-spike V4).
No CLI, no network, no DB: subprocess, keychain, ceiling, and DB are mocked."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

from agents._lib import claude_cli

SCHEMA = {"type": "object", "properties": {"subject": {"type": "string"}}}


def _ok(**extra):
    data = {
        "type": "result", "subtype": "success", "is_error": False,
        "result": "", "structured_output": {"subject": "S", "body": "B"},
        "total_cost_usd": 0.1234, "session_id": "sess-1", "num_turns": 1,
        "usage": {"input_tokens": 100, "cache_read_input_tokens": 20,
                  "cache_creation_input_tokens": 5, "output_tokens": 50},
    }
    data.update(extra)
    return json.dumps(data)


def test_command_is_locked_down():
    argv = claude_cli.build_command(model="claude-opus-5-5", schema=SCHEMA, max_budget_usd=1)
    assert argv[:2] == ["claude", "-p"]
    assert "--bare" in argv
    assert "--strict-mcp-config" in argv
    assert "--no-session-persistence" in argv
    assert argv[argv.index("--tools") + 1] == ""          # no tools at all
    assert argv[argv.index("--max-budget-usd") + 1] == "1.00"
    assert json.loads(argv[argv.index("--json-schema") + 1]) == SCHEMA
    assert "--mcp-config" not in argv


def test_parse_structured_output_and_usage():
    r = claude_cli.parse_result(_ok())
    assert r.output == {"subject": "S", "body": "B"}
    assert r.usd_cost == pytest.approx(0.1234)
    assert (r.input_tokens, r.output_tokens) == (125, 50)
    assert r.session_id == "sess-1"


def test_parse_falls_back_to_result_json():
    r = claude_cli.parse_result(
        _ok(structured_output=None, result=json.dumps({"subject": "X", "body": "Y"})))
    assert r.output == {"subject": "X", "body": "Y"}


@pytest.mark.parametrize("stdout", [
    "not json",
    _ok(is_error=True, subtype="error_max_budget_usd"),
    _ok(subtype="error_during_execution"),
    _ok(structured_output=None, result="plain prose, no JSON"),
    _ok(structured_output=["not", "an", "object"]),
])
def test_parse_rejects_bad_results(stdout):
    with pytest.raises(claude_cli.ClaudeSessionError):
        claude_cli.parse_result(stdout)


def test_cost_from_failed_session():
    assert claude_cli.cost_from(_ok(is_error=True)) == pytest.approx(0.1234)
    assert claude_cli.cost_from("garbage") is None


@pytest.fixture
def harness(mocker):
    mocker.patch.object(claude_cli.runs, "assert_under_ceiling")
    mocker.patch.object(claude_cli, "check_cli_version", return_value=(2, 1, 282))
    mocker.patch.object(claude_cli.creds, "keychain_get", return_value="sk-test")
    record = mocker.patch.object(claude_cli, "record_run")
    run = mocker.patch.object(claude_cli.subprocess, "run")
    return SimpleNamespace(record=record, run=run)


def _call():
    return claude_cli.run_session("prompt", agent="meeting-digest", function_label="meeting_digest",
                                  model="claude-opus-5-5", schema=SCHEMA, max_budget_usd=1)


def test_run_session_success_ledgers_cost(harness):
    harness.run.return_value = SimpleNamespace(returncode=0, stdout=_ok(), stderr="")
    result = _call()
    assert result.output["subject"] == "S"
    kwargs = harness.run.call_args.kwargs
    assert kwargs["input"] == "prompt"                       # prompt on stdin
    assert kwargs["env"]["ANTHROPIC_API_KEY"] == "sk-test"
    rec = harness.record.call_args.kwargs
    assert rec["status"] == "success" and rec["result"].usd_cost == pytest.approx(0.1234)


def test_run_session_failure_still_ledgers_spend(harness):
    harness.run.return_value = SimpleNamespace(
        returncode=1, stdout=_ok(is_error=True, subtype="error_max_budget_usd"), stderr="")
    with pytest.raises(claude_cli.ClaudeSessionError):
        _call()
    rec = harness.record.call_args.kwargs
    assert rec["status"] == "failed"
    assert rec["usd_cost"] == pytest.approx(0.1234)


def test_run_session_timeout_is_ledgered(harness):
    harness.run.side_effect = subprocess.TimeoutExpired("claude", 600)
    with pytest.raises(claude_cli.ClaudeSessionError):
        _call()
    assert harness.record.call_args.kwargs["status"] == "failed"


def test_run_session_respects_ceiling(mocker):
    mocker.patch.object(claude_cli.runs, "assert_under_ceiling",
                        side_effect=claude_cli.runs.DailyCeilingExceeded("over"))
    run = mocker.patch.object(claude_cli.subprocess, "run")
    with pytest.raises(claude_cli.runs.DailyCeilingExceeded):
        _call()
    run.assert_not_called()                                  # never launched


def test_record_run_writes_one_row(mocker):
    cur = mocker.MagicMock()
    conn = mocker.MagicMock()
    conn.__enter__.return_value = conn
    conn.cursor.return_value.__enter__.return_value = cur
    mocker.patch.object(claude_cli.db, "connection", return_value=conn)
    result = claude_cli.parse_result(_ok())
    from datetime import UTC, datetime
    claude_cli.record_run(agent="meeting-digest", function_label="meeting_digest",
                          model="claude-opus-5-5", started_at=datetime.now(UTC),
                          status="success", result=result)
    params = cur.execute.call_args.args[1]
    assert params[0:2] == ("meeting-digest", "meeting_digest")
    assert "claude-opus-5-5" in params
    assert 0.1234 in params and "sess-1" in params


# --- CLI version check (barry-agent 2026-10-05: 2.1.228 rejected the model) ---

@pytest.mark.parametrize("text,expected", [
    ("2.1.282 (Claude Code)\n", (2, 1, 282)),
    ("2.1.228 (Claude Code)", (2, 1, 228)),
    ("claude version unknown", None),
])
def test_parse_version(text, expected):
    assert claude_cli.parse_version(text) == expected


def _version_proc(mocker, stdout):
    return mocker.patch.object(
        claude_cli.subprocess, "run",
        return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr=""))


def test_version_check_accepts_new_enough(mocker):
    _version_proc(mocker, "2.1.280 (Claude Code)")
    assert claude_cli.check_cli_version() == (2, 1, 280)
    _version_proc(mocker, "3.0.0 (Claude Code)")
    assert claude_cli.check_cli_version() == (3, 0, 0)


def test_version_check_rejects_old_with_clear_message(mocker):
    _version_proc(mocker, "2.1.228 (Claude Code)")
    pattern = r"2\.1\.228 is too old.*2\.1\.280.*claude update"
    with pytest.raises(claude_cli.ClaudeSessionError, match=pattern):
        claude_cli.check_cli_version()


def test_version_check_unreadable_or_missing(mocker):
    _version_proc(mocker, "")
    with pytest.raises(claude_cli.ClaudeSessionError, match="could not read"):
        claude_cli.check_cli_version()
    mocker.patch.object(claude_cli.subprocess, "run", side_effect=FileNotFoundError("claude"))
    with pytest.raises(claude_cli.ClaudeSessionError, match="could not run"):
        claude_cli.check_cli_version()


def test_old_cli_never_launches_and_writes_no_row(mocker):
    mocker.patch.object(claude_cli.runs, "assert_under_ceiling")
    mocker.patch.object(claude_cli.creds, "keychain_get", return_value="sk-test")
    record = mocker.patch.object(claude_cli, "record_run")
    run = _version_proc(mocker, "2.1.228 (Claude Code)")
    with pytest.raises(claude_cli.ClaudeSessionError, match="too old"):
        _call()
    assert run.call_count == 1                 # only `claude --version`, no session
    assert run.call_args.args[0] == ["claude", "--version"]
    record.assert_not_called()


def test_error_result_message_names_failure_not_success():
    pattern = r"session failed \(success\): Not logged in"
    with pytest.raises(claude_cli.ClaudeSessionError, match=pattern):
        claude_cli.parse_result(_ok(is_error=True, result="Not logged in"))
