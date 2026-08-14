"""Unit tests for the control-plane scheduler.

Pure pieces (build_command, next_fire, plan) over the real seed manifests and
synthetic loops. The daemon loop itself (run_forever) is not unit-tested — it is
a sleep/subprocess loop; its logic is covered by testing plan() + build_command.
"""

from __future__ import annotations

from datetime import datetime

from agents._lib.control_plane import Loop, discover, repo_root
from agents.scheduler.run import Scheduler, build_command, next_fire


def _agent_loop() -> Loop:
    return Loop(name="morning-briefing", schedule="0 6 * * *", enabled=True,
                agent="briefing", playbook="daily-briefing")


def _command_loop() -> Loop:
    return Loop(name="nightly-backup", schedule="0 2 * * *", enabled=True,
                command="scripts/pg_backup.sh")


# --- build_command --------------------------------------------------------


def test_build_command_agent_variant():
    argv, env = build_command(_agent_loop(), "/Users/barry-agent/agents")
    assert argv[0] == "/bin/zsh" and argv[1] == "-lc"
    assert "uv run python -m agents.briefing.run" in argv[2]
    assert "cd /Users/barry-agent/agents" in argv[2]
    assert env == {"COS_PLAYBOOK": "daily-briefing"}


def test_build_command_agent_without_playbook_has_no_env():
    lp = Loop(name="x", schedule="0 6 * * *", enabled=True, agent="tartt")
    _, env = build_command(lp, "/tmp/root")
    assert env == {}


def test_build_command_command_variant():
    argv, env = build_command(_command_loop(), "/Users/barry-agent/agents")
    assert "exec scripts/pg_backup.sh" in argv[2]
    assert env == {}  # command loops never set COS_PLAYBOOK


def test_build_command_quotes_root_with_spaces():
    argv, _ = build_command(_agent_loop(), "/Users/Shared/afc richmond")
    assert "'/Users/Shared/afc richmond'" in argv[2]


# --- next_fire ------------------------------------------------------------


def test_next_fire_daily_six_am():
    base = datetime(2026, 1, 1, 0, 0)
    assert next_fire("0 6 * * *", base) == datetime(2026, 1, 1, 6, 0)


def test_next_fire_is_strictly_after_base():
    base = datetime(2026, 1, 1, 6, 0)
    # already 6:00 → next is tomorrow, not now
    assert next_fire("0 6 * * *", base) == datetime(2026, 1, 2, 6, 0)


def test_next_fire_every_six_hours():
    base = datetime(2026, 1, 1, 5, 30)
    assert next_fire("0 */6 * * *", base) == datetime(2026, 1, 1, 6, 0)


# --- Scheduler over the real repo seed loops ------------------------------


def test_scheduler_plans_only_enabled_real_loops():
    # Asserts the PROPERTY, not the current roster. This test previously
    # hardcoded the enabled set, so it went red on every intentional
    # activation — granola-poll's, then outreach-evidence's — training whoever
    # flipped the flag to just update the constant. A test that fails for
    # correct changes stops being read as a signal.
    cp = discover(repo_root())
    now = datetime(2026, 1, 1, 0, 0)
    sched = Scheduler(cp, repo_root(), now)
    planned = {name for name, _, _ in sched.plan()}

    enabled = {lp.name for lp in cp.enabled_loops()}
    disabled = {lp.name for lp in cp.loops} - enabled

    assert planned == enabled
    assert planned.isdisjoint(disabled)   # the thing the name actually promises
    assert enabled and disabled           # a repo with neither would vacuously pass


def test_scheduler_plan_is_ordered_by_next_fire():
    cp = discover(repo_root())
    sched = Scheduler(cp, repo_root(), datetime(2026, 1, 1, 0, 0))
    fire_times = [when for _, when, _ in sched.plan()]
    assert fire_times == sorted(fire_times)


def test_scheduler_briefing_job_invokes_agent_with_playbook():
    cp = discover(repo_root())
    sched = Scheduler(cp, repo_root(), datetime(2026, 1, 1, 0, 0))
    brief = next(j for j in sched.jobs if j.loop.name == "morning-briefing")
    assert "agents.briefing.run" in brief.argv[2]
    assert brief.env.get("COS_PLAYBOOK") == "daily-briefing"
