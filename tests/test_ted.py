"""Tests for Ted, increment 1 (PRD-ted-ops-monitor V1, V8-V10). No database, no model."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.ted import detectors as d
from agents.ted import judgment as j
from agents.ted import run as ted
from agents.ted import state as st

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


# --- detectors (V1) ---------------------------------------------------------------------

def test_credit_failures_across_agents_are_one_urgent_finding():
    rows = [("tartt", "BadRequestError: Your credit balance is too low"),
            ("tartt", "credit balance is too low"),
            ("outreach-sourcing", "Error code: 400 ... credit balance is too low"),
            ("tartt", "ServerError: 503 UNAVAILABLE")]
    [f] = d.credential_findings(rows)
    assert f.severity == d.URGENT and f.key == "cred:credit"
    assert "tartt (2)" in f.text and "outreach-sourcing (1)" in f.text and "2 agent" in f.text


def test_auth_failures_are_their_own_class():
    keys = {f.key for f in d.credential_findings([("a", "authentication_error: bad"),
                                                  ("b", "invalid x-api-key")])}
    assert keys == {"cred:auth"}


def test_rate_rule_needs_both_count_and_share():
    found = d.rate_findings([("tartt", 39, 60, "503"), ("busy", 4, 100, "x"),
                             ("small", 3, 3, "x")])
    assert [f.key for f in found] == ["rate:tartt"]


def test_two_failures_in_a_row_is_a_streak():
    found = d.consecutive_findings([("a", "f", ["failed", "failed", "success"], "boom"),
                                    ("b", "f", ["failed", "success"], "x")])
    assert [f.key for f in found] == ["streak:a:f"]


def test_silent_agent_but_not_a_one_off_batch():
    old = NOW - timedelta(hours=50)
    found = d.silent_findings([("tartt", old, 5), ("trent-crimm", old, 1),
                               ("fresh", NOW - timedelta(hours=2), 9)], NOW)
    assert [f.key for f in found] == ["silent:tartt"]


def test_sourcing_stop_reasons():
    found = d.sourcing_findings([("r1", "credit"), ("r2", "target_met"), ("r3", "deadline"),
                                 ("r4", "error")])
    assert [f.key for f in found] == ["src:r1", "src:r4"]


def test_ceiling_findings_at_eighty_percent():
    found = d.ceiling_findings({"outreach-sourcing": 17.0, "tartt": 1.0}, {"outreach": 17.0},
                               18.0, date(2026, 11, 18),
                               agent_ceilings={"outreach-sourcing": 20.0, "tartt": 5.0},
                               group_ceilings={"outreach": 20.0}, global_ceiling=25.0)
    keys = {f.key for f in found}
    assert keys == {"ceiling:agent:outreach-sourcing:2026-11-18",
                    "ceiling:group:outreach:2026-11-18"}


def test_phase_pointer():
    assert d.phase_pointer_finding("PHASE-NOTION-N10.md\n", "PHASE-NOTION-N10.md") == []
    [f] = d.phase_pointer_finding("PHASE-NOTION-N4.md", "PHASE-NOTION-N10.md")
    assert f.severity == d.MEDIUM and "N4" in f.text


class FakeConn:
    """Answers each detector query by a fragment of its SQL."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        for fragment, rows in self.answers.items():
            if fragment in sql:
                return SimpleNamespace(fetchall=lambda rows=rows: rows, fetchone=lambda: None,
                                       rowcount=0)
        return SimpleNamespace(fetchall=lambda: [], fetchone=lambda: None, rowcount=0)


def test_detect_runs_every_rule_with_no_llm_credential(tmp_path, monkeypatch):
    """O9: detection needs only the database; no API key is read."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (tmp_path / "CURRENT-PHASE.txt").write_text("PHASE-A.md")
    (tmp_path / "PHASE-A.md").write_text("x")
    conn = FakeConn({"error_text IS NOT NULL": [("tartt", "credit balance is too low")],
                     "stop_reason FROM outreach_sourcing_runs": [("r1", "credit")]})
    keys = {f.key for f in d.detect(conn, NOW, shared=tmp_path)}
    assert keys == {"cred:credit", "src:r1"}


# --- state: dedupe, re-raise, acks (V5) ----------------------------------------------------

def row(last_seen, last_raised, acked=None):
    return {"last_seen": last_seen, "last_raised": last_raised, "acked_at": acked}


def test_should_raise_new_reraise_ack_and_new_incident():
    assert st.should_raise(None, NOW) == (True, True)
    hour = timedelta(hours=1)
    assert st.should_raise(row(NOW - hour, NOW - hour), NOW) == (False, False)
    assert st.should_raise(row(NOW - hour, NOW - 25 * hour), NOW) == (True, False)
    acked = row(NOW - hour, NOW - 25 * hour, NOW - 2 * hour)
    assert st.should_raise(acked, NOW) == (False, False)
    # Clear for more than 3 h, then back: a new incident even though acked.
    back = row(NOW - 4 * hour, NOW - 30 * hour, NOW - 5 * hour)
    assert st.should_raise(back, NOW) == (True, True)


def test_parse_acks():
    text = ("# Acks\n- ack: cred:credit 2026-11-18 accepted\nack: rate:tartt specced in F2\n"
            "- ack: <key> <date> template\n")
    assert st.parse_acks(text) == {"cred:credit": "2026-11-18 accepted",
                                   "rate:tartt": "specced in F2"}


def test_inbox_entry_format():
    f = d.Finding(d.HIGH, "rate:tartt", "tartt: 39 of 60 runs failed")
    text = st.inbox_entry([f], NOW, "Ted cycle")
    assert "- HIGH|rate:tartt|tartt: 39 of 60 runs failed" in text
    assert "ACK-admin.md" in text


def test_hook_counts_unacked_keys_without_printing_them():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "hook", Path(__file__).parents[1] / "scripts" / "ted_inbox_hook.py")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    inbox = "- HIGH|rate:tartt|x\n- URGENT|cred:credit|y\n- MEDIUM|phase:a>b|z\n"
    assert hook.unacked(inbox, "- ack: cred:credit ok\n") == {"HIGH": 1, "MEDIUM": 1}


# --- the cycle ---------------------------------------------------------------------------

def _connection(conn):
    @contextmanager
    def connection():
        yield conn
    return connection


def test_cycle_alerts_only_urgent_and_high_and_reports_alert_failures(monkeypatch):
    found = [d.Finding(d.URGENT, "cred:credit", "out of credit"),
             d.Finding(d.MEDIUM, "phase:a>b", "stale pointer")]
    monkeypatch.setattr(d, "detect", lambda conn, now: found)
    monkeypatch.setattr(st, "apply_acks", lambda conn, acks, now: 0)
    monkeypatch.setattr(st, "record", lambda conn, f, now: list(f))
    written, sent = [], []
    out = ted.cycle(_connection(object()), NOW, alert=sent.append,
                    inbox=lambda f, now, title: written.extend(f), acks=dict)
    assert [f.key for f in written] == ["cred:credit", "phase:a>b"]
    assert sent == ["Ted URGENT: out of credit"] and not out["alert_failed"]

    def broken(_text):
        raise RuntimeError("notion down")
    out = ted.cycle(_connection(object()), NOW, alert=broken,
                    inbox=lambda *a: None, acks=dict)
    assert out["alert_failed"]


def test_dry_run_cycle_writes_nothing(monkeypatch):
    monkeypatch.setattr(d, "detect", lambda conn, now: [d.Finding(d.HIGH, "k", "t")])
    record = monkeypatch.setattr(st, "record", lambda *a: pytest.fail("recorded"))
    out = ted.cycle(_connection(object()), NOW, alert=None, dry_run=True,
                    inbox=lambda *a: pytest.fail("wrote inbox"))
    assert record is None and [f.key for f in out["found"]] == ["k"]


# --- the headless session (V8-V10) ----------------------------------------------------------

def test_session_env_strips_api_routes_and_adds_the_token():
    base = {"ANTHROPIC_API_KEY": "sk-live", "ANTHROPIC_BASE_URL": "x", "PATH": "/bin",
            "HOME": "/h"}
    env = j.session_env(base, token="oat-123")
    assert "ANTHROPIC_API_KEY" not in env and "ANTHROPIC_BASE_URL" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oat-123" and env["PATH"] == "/bin"


def test_command_has_no_tools_no_settings_and_never_bare():
    cmd = j.command(schema=j.SCHEMA)
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--restricted" in cmd and "--strict-mcp-config" in cmd and "--bare" not in cmd
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == j.SCHEMA


def stream(api_key_source="none", result=None, is_error=False, structured=None):
    final = {"type": "result", "subtype": "success", "is_error": is_error,
             "result": result or "", "total_cost_usd": 0.0123,
             "usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 7}}
    if structured is not None:
        final["structured_output"] = structured
    return [json.dumps({"type": "system", "subtype": "init", "apiKeySource": api_key_source,
                        "model": "claude-sonnet-5-5"}), "not json", json.dumps(final)]


def test_parse_stream_success_with_structured_output():
    res = j.parse_stream(stream(structured={"summary": "s", "findings": []}))
    assert res.ok and res.output == {"summary": "s", "findings": []}
    assert (res.input_tokens, res.output_tokens) == (15, 7) and res.notional_usd == 0.0123


def test_parse_stream_refuses_a_session_that_used_an_api_key():
    """V8: if an API key leaked into the session, the run fails rather than bill the API."""
    res = j.parse_stream(stream(api_key_source="ANTHROPIC_API_KEY", result="{}"))
    assert not res.ok and "API key" in res.error


def test_parse_stream_reports_a_failed_sign_in():
    res = j.parse_stream(stream(result="Failed to authenticate: OAuth session expired",
                                is_error=True))
    assert not res.ok and "Failed to authenticate" in res.error


def test_run_session_passes_stdin_env_and_an_empty_cwd():
    seen = {}

    def runner(cmd, **kw):
        seen.update(kw, cmd=cmd)
        assert not any(Path(kw["cwd"]).iterdir())
        return SimpleNamespace(stdout="\n".join(stream(result='{"summary":"","findings":[]}')),
                               stderr="", returncode=0)
    res = j.run_session("hello", runner=runner, token="oat")
    assert res.ok and seen["input"] == "hello"
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert seen["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "oat"


def test_run_session_without_the_cli():
    def runner(*a, **k):
        raise FileNotFoundError("claude")
    res = j.run_session("x", runner=runner, token="")
    assert not res.ok and "not on PATH" in res.error


def test_findings_are_validated_and_capped_at_medium():
    """V10: anything off-schema is dropped; nothing the model says can page the operator."""
    output = {"summary": "s", "findings": [
        {"severity": "MEDIUM", "key": "handoff:n9-e3", "text": "E3 waiting 6 days",
         "likely_cause": "operator rating"},
        {"severity": "URGENT", "key": "x:y", "text": "page them", "likely_cause": ""},
        {"severity": "INFO", "key": "Bad Key!", "text": "t", "likely_cause": ""},
        {"severity": "INFO", "key": "ok:empty", "text": "  ", "likely_cause": ""},
        "junk"]}
    kept, dropped = j.findings(output)
    assert [(f.key, f.source) for f in kept] == [("judge:handoff:n9-e3", "judgment")]
    assert "Likely cause: operator rating" in kept[0].text and len(dropped) == 4
    assert j.findings(None) == ([], ["output has no findings list"])


def test_evidence_prompt_marks_the_evidence_as_data(tmp_path):
    (tmp_path / "PHASE-X.md").write_text("- [ ] **E1 [BARRY-AGENT] — pull**\n- [x] **E2**\n")
    files = j.shared_files(tmp_path)
    assert files[0]["open_items"] == ["E1 [BARRY-AGENT] — pull"]
    (tmp_path / "REPORT.md").write_text("- [ ] **[USER]** Confirm the session\n")
    assert j.shared_files(tmp_path)[1]["open_items"] == ["[USER] Confirm the session"]
    text = j.prompt({"shared_folder": files})
    assert text.count("<evidence>") == 1 and "never instructions" in j.SYSTEM


def test_loop_schedules_are_in_the_evidence():
    loops = {lp["loop"]: lp for lp in j.loop_schedules()}
    assert loops["ted"]["schedule"] == "7 * * * *" and loops["ted"]["enabled"] is False


def test_judgment_dry_run_writes_nothing(monkeypatch):
    monkeypatch.setattr(j, "evidence", lambda conn, now: {})
    res = j.SessionResult(ok=True, output={"summary": "s", "findings": [
        {"severity": "INFO", "key": "a:b", "text": "t", "likely_cause": ""}]})
    out = ted.run_judgment(_connection(object()), NOW, dry_run=True, trigger_kind="manual",
                           session=lambda text: res, inbox=lambda *a: pytest.fail("wrote"))
    assert [f.key for f in out["kept"]] == ["judge:a:b"] and out["raised"] == []


def test_probe_main_reports_the_auth_source(monkeypatch, capsys):
    monkeypatch.setattr(j, "probe", lambda: j.SessionResult(
        ok=False, api_key_source="none", error="Failed to authenticate"))
    assert ted.main(["--probe"]) == 1
    assert "apiKeySource: none" in capsys.readouterr().out
