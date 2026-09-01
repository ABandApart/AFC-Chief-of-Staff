"""Control-plane scheduler daemon.

Reads the enabled loop manifests (`agents/_lib/control_plane`) and fires each on
its cron schedule — ONE supervised process replacing the per-job launchd
calendar plists (Track A / refactor item A7). launchd keeps this daemon alive
(KeepAlive); this daemon owns the *timing* of the loops.

- **agent loops** run `uv run python -m agents.<agent>.run`, with the loop's
  playbook exported as `COS_PLAYBOOK` so the agent can load it.
- **command loops** run the command string (e.g. `scripts/pg_backup.sh`).

Both run from the repo root under a login shell (so `uv` is on PATH), mirroring
the plists they replace. Jobs are launched fire-and-forget (`Popen`) so a slow
job never delays another; their output is inherited into the daemon's log.

Usage:
    uv run python -m agents.scheduler.run --dry-run   # print schedule + next fires, exit
    uv run python -m agents.scheduler.run             # run the daemon (launchd target)

SIGTERM / SIGINT exit cleanly (launchd unload; the wait is interruptible).
"""

from __future__ import annotations

import argparse
import logging
import os
import shlex
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from croniter import croniter

from agents._lib import heartbeat
from agents._lib.control_plane import ControlPlane, Loop, discover, repo_root

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] scheduler: %(message)s",
)
logger = logging.getLogger(__name__)

# How long the daemon may sleep before re-reading the loop manifests. Bounds how
# stale the running schedule can be after an activation — one minute, rather than
# "until someone restarts the daemon", which is what it used to be.
RELOAD_SECONDS = 60

# The scheduler's own dead-man's switch (`cos-scheduler`, 80-telemetry-layer §
# PERF-4). launchd keeps THIS daemon alive, but nothing off-box knows if it is
# actually cycling — if it wedges, every loop silently stops firing and each
# loop's own check only trips after its (long) grace. So the daemon pings its
# liveness slug as it cycles; the *absence* of that ping is the fast, off-box
# alert that the whole schedule has stalled. An on-box monitor can't do this
# job — it can't observe the box being dead. Grace is 1h; ping every 5 minutes
# so a single slow cycle is nowhere near the threshold. No-ops until the
# `healthchecks-ping-key` is provisioned (see heartbeat.py).
SCHEDULER_SLUG = "cos-scheduler"
SCHEDULER_BEAT_SECONDS = 300


def build_command(loop: Loop, root: Path | str) -> tuple[list[str], dict[str, str]]:
    """Return (argv, env-overlay) to run one loop from the repo root.

    Pure — unit-tested. argv is a login-shell invocation so `uv` is on PATH,
    matching the launchd plists this replaces.
    """
    root_q = shlex.quote(str(root))
    if loop.command:
        inner = loop.command
        env: dict[str, str] = {}
    else:
        inner = f"uv run python -m agents.{loop.agent}.run"
        env = {"COS_PLAYBOOK": loop.playbook} if loop.playbook else {}
    argv = ["/bin/zsh", "-lc", f"cd {root_q} && exec {inner}"]
    return argv, env


def next_fire(schedule: str, base: datetime) -> datetime:
    """Next fire time strictly after `base` for a 5-field cron string."""
    return croniter(schedule, base).get_next(datetime)


@dataclass
class Job:
    loop: Loop
    argv: list[str]
    env: dict[str, str]
    next_run: datetime


class Scheduler:
    """Holds the schedule; `plan()` is inspectable, `run_forever()` executes."""

    def __init__(self, cp: ControlPlane, root: Path | str, now: datetime):
        self.root = Path(root)
        self.jobs: list[Job] = []
        self._last_beat: datetime | None = None
        for lp in cp.enabled_loops():
            argv, env = build_command(lp, root)
            self.jobs.append(Job(lp, argv, env, next_fire(lp.schedule, now)))

    def _maybe_beat(self, now: datetime) -> bool:
        """Ping the daemon's own liveness switch (`cos-scheduler`), rate-limited.

        Returns whether it pinged — the first call always does (so the check
        goes green the moment the daemon starts), then at most once per
        `SCHEDULER_BEAT_SECONDS`. Never raises: `heartbeat.ping` swallows its
        own errors, and monitoring must never break the work it reports on.
        """
        if (self._last_beat is not None
                and (now - self._last_beat).total_seconds() < SCHEDULER_BEAT_SECONDS):
            return False
        heartbeat.ping(SCHEDULER_SLUG)
        self._last_beat = now
        return True

    def plan(self) -> list[tuple[str, datetime, list[str]]]:
        """(name, next_run, argv) sorted by next_run — for --dry-run / tests."""
        return sorted(
            [(j.loop.name, j.next_run, j.argv) for j in self.jobs],
            key=lambda t: t[1],
        )

    def reload(self, now: datetime) -> tuple[set[str], set[str]]:
        """Re-read the manifests. Returns (newly enabled, newly disabled) names.

        **Added 2026-08-15 after this cost a night of polling.** The scheduler
        read manifests once at startup and never again, so enabling a loop in git
        had no effect until the daemon happened to be restarted — and nothing
        said so, in the logs or in `loops/README.md`. `outreach-evidence` was
        flipped on, pulled, and simply never fired, while every health signal
        looked fine. A loop that silently never runs is the worst failure this
        daemon has, because there is nothing to notice.

        A job whose schedule is unchanged keeps its existing `next_run`, so
        re-reading never resets a countdown or causes a double fire.
        """
        cp = discover(self.root)
        if not cp.ok:
            # Keep the schedule we already have. Dropping every loop because
            # someone is mid-edit on one manifest would turn a typo into an
            # outage.
            for e in cp.errors:
                logger.error(
                    "control-plane error on reload (keeping current schedule): %s", e
                )
            return set(), set()

        existing = {j.loop.name: j for j in self.jobs}
        rebuilt: list[Job] = []
        for lp in cp.enabled_loops():
            prior = existing.get(lp.name)
            if prior is not None and prior.loop.schedule == lp.schedule:
                rebuilt.append(prior)          # keep its next_run
            else:
                argv, env = build_command(lp, self.root)
                rebuilt.append(Job(lp, argv, env, next_fire(lp.schedule, now)))

        names = {j.loop.name for j in rebuilt}
        added, removed = names - set(existing), set(existing) - names
        self.jobs = rebuilt
        return added, removed

    def _fire(self, job: Job) -> None:
        logger.info("firing loop '%s': %s", job.loop.name, job.loop.target)
        env = {**os.environ, **job.env}
        try:
            subprocess.Popen(job.argv, env=env, cwd=str(self.root))
        except Exception:
            logger.exception("failed to launch loop '%s'", job.loop.name)

    def run_forever(self, stop: threading.Event) -> None:
        logger.info("scheduling %d loop(s): %s", len(self.jobs),
                    ", ".join(j.loop.name for j in self.jobs) or "(none yet)")
        while not stop.is_set():
            # Liveness first: the daemon reached the top of another cycle. If it
            # dies or wedges below this line, the ping stops and cos-scheduler
            # goes silent — that silence IS the alert.
            self._maybe_beat(datetime.now())
            added, removed = self.reload(datetime.now())
            if added:
                logger.info("loop(s) enabled since last check: %s", ", ".join(sorted(added)))
            if removed:
                logger.info("loop(s) disabled since last check: %s", ", ".join(sorted(removed)))

            if not self.jobs:
                # Bounded wait, not an indefinite one: with nothing enabled the
                # daemon used to block forever, so enabling the first loop
                # required a restart nobody knew to do.
                logger.warning("no enabled loops — re-checking in %ds", RELOAD_SECONDS)
                if stop.wait(timeout=RELOAD_SECONDS):
                    break
                continue

            # Never sleep past the next reload, or a loop enabled today would
            # wait for whatever is scheduled furthest out before being noticed.
            soonest = min(j.next_run for j in self.jobs)
            wait_s = min((soonest - datetime.now()).total_seconds(), RELOAD_SECONDS)
            if wait_s > 0 and stop.wait(timeout=wait_s):
                break
            now = datetime.now()
            for job in self.jobs:
                if job.next_run <= now:
                    self._fire(job)
                    job.next_run = next_fire(job.loop.schedule, now)


def main() -> int:
    parser = argparse.ArgumentParser(description="Control-plane scheduler daemon.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the schedule + next fire times and exit (no daemon)",
    )
    args = parser.parse_args()

    cp = discover(repo_root())
    if not cp.ok:
        for e in cp.errors:
            logger.error("control-plane error: %s", e)
        return 1

    sched = Scheduler(cp, repo_root(), datetime.now())

    if args.dry_run:
        print(f"{len(sched.jobs)} enabled loop(s):")
        for name, nxt, argv in sched.plan():
            print(f"  {name:<20} next: {nxt:%Y-%m-%d %H:%M}   {argv[-1]}")
        return 0

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    logger.info("scheduler starting (pid %d)", os.getpid())
    sched.run_forever(stop)
    logger.info("scheduler shutting down")
    return 0


if __name__ == "__main__":
    sys.exit(main())
