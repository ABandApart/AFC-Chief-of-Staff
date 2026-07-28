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

from agents._lib.control_plane import ControlPlane, Loop, discover, repo_root

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] scheduler: %(message)s",
)
logger = logging.getLogger(__name__)


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
        for lp in cp.enabled_loops():
            argv, env = build_command(lp, root)
            self.jobs.append(Job(lp, argv, env, next_fire(lp.schedule, now)))

    def plan(self) -> list[tuple[str, datetime, list[str]]]:
        """(name, next_run, argv) sorted by next_run — for --dry-run / tests."""
        return sorted(
            [(j.loop.name, j.next_run, j.argv) for j in self.jobs],
            key=lambda t: t[1],
        )

    def _fire(self, job: Job) -> None:
        logger.info("firing loop '%s': %s", job.loop.name, job.loop.target)
        env = {**os.environ, **job.env}
        try:
            subprocess.Popen(job.argv, env=env, cwd=str(self.root))
        except Exception:
            logger.exception("failed to launch loop '%s'", job.loop.name)

    def run_forever(self, stop: threading.Event) -> None:
        if not self.jobs:
            logger.warning("no enabled loops — scheduler idle until stopped")
            stop.wait()
            return
        logger.info("scheduling %d loop(s): %s", len(self.jobs),
                    ", ".join(j.loop.name for j in self.jobs))
        while not stop.is_set():
            soonest = min(j.next_run for j in self.jobs)
            wait_s = (soonest - datetime.now()).total_seconds()
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
