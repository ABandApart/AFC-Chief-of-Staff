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


def test_reload_picks_up_a_newly_enabled_loop(tmp_path):
    # THE regression. The scheduler read manifests once at startup and never
    # again, so `enabled: false -> true` had no effect until someone restarted
    # the daemon — and nothing said so. outreach-evidence was flipped on, pulled,
    # and silently never fired for a full night.
    (tmp_path / "playbooks").mkdir()
    loops = tmp_path / "loops"
    loops.mkdir()
    manifest = loops / "later.md"
    manifest.write_text(
        "---\nname: later\nschedule: \"0 */12 * * *\"\ntrigger_kind: scheduled\n"
        "enabled: false\ncommand: echo hi\ndescription: d\n---\n", encoding="utf-8"
    )
    now = datetime(2026, 1, 1, 0, 0)
    sched = Scheduler(discover(tmp_path), tmp_path, now)
    assert sched.plan() == []

    manifest.write_text(manifest.read_text().replace("enabled: false", "enabled: true"))
    added, removed = sched.reload(now)

    assert added == {"later"} and removed == set()
    assert [n for n, _, _ in sched.plan()] == ["later"]


def test_reload_notices_a_disabled_loop(tmp_path):
    (tmp_path / "playbooks").mkdir()
    loops = tmp_path / "loops"
    loops.mkdir()
    manifest = loops / "going.md"
    manifest.write_text(
        "---\nname: going\nschedule: \"0 6 * * *\"\ntrigger_kind: scheduled\n"
        "enabled: true\ncommand: echo hi\ndescription: d\n---\n", encoding="utf-8"
    )
    now = datetime(2026, 1, 1, 0, 0)
    sched = Scheduler(discover(tmp_path), tmp_path, now)
    manifest.write_text(manifest.read_text().replace("enabled: true", "enabled: false"))

    added, removed = sched.reload(now)
    assert removed == {"going"} and sched.plan() == []


def test_reload_does_not_reset_an_unchanged_loops_countdown(tmp_path):
    # Re-reading every 60s must not push next_run forward each time, or a daily
    # loop would never reach its fire time.
    (tmp_path / "playbooks").mkdir()
    loops = tmp_path / "loops"
    loops.mkdir()
    (loops / "steady.md").write_text(
        "---\nname: steady\nschedule: \"0 6 * * *\"\ntrigger_kind: scheduled\n"
        "enabled: true\ncommand: echo hi\ndescription: d\n---\n", encoding="utf-8"
    )
    sched = Scheduler(discover(tmp_path), tmp_path, datetime(2026, 1, 1, 0, 0))
    before = sched.plan()[0][1]

    sched.reload(datetime(2026, 1, 1, 5, 59))     # a later "now"
    assert sched.plan()[0][1] == before


def test_reload_keeps_the_current_schedule_when_a_manifest_is_broken(tmp_path):
    # A typo mid-edit must not drop every loop — that turns a mistake into an
    # outage across the whole control plane.
    (tmp_path / "playbooks").mkdir()
    loops = tmp_path / "loops"
    loops.mkdir()
    (loops / "good.md").write_text(
        "---\nname: good\nschedule: \"0 6 * * *\"\ntrigger_kind: scheduled\n"
        "enabled: true\ncommand: echo hi\ndescription: d\n---\n", encoding="utf-8"
    )
    sched = Scheduler(discover(tmp_path), tmp_path, datetime(2026, 1, 1, 0, 0))
    (loops / "broken.md").write_text("no frontmatter here", encoding="utf-8")

    added, removed = sched.reload(datetime(2026, 1, 1, 0, 1))
    assert (added, removed) == (set(), set())
    assert [n for n, _, _ in sched.plan()] == ["good"]


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


# --- cos-scheduler liveness beat (80-telemetry-layer § PERF-4) --------------

from datetime import timedelta  # noqa: E402

from agents.scheduler.run import SCHEDULER_BEAT_SECONDS, SCHEDULER_SLUG  # noqa: E402


def test_maybe_beat_pings_first_then_rate_limits(mocker):
    cp = discover(repo_root())
    t0 = datetime(2026, 1, 1, 0, 0)
    sched = Scheduler(cp, repo_root(), t0)
    ping = mocker.patch("agents.scheduler.run.heartbeat.ping")

    assert sched._maybe_beat(t0) is True                      # first cycle → green now
    assert sched._maybe_beat(t0 + timedelta(seconds=SCHEDULER_BEAT_SECONDS - 1)) is False
    assert sched._maybe_beat(t0 + timedelta(seconds=SCHEDULER_BEAT_SECONDS)) is True
    assert ping.call_count == 2
    ping.assert_called_with(SCHEDULER_SLUG)
