# Checklist — Phase 3.6: Control plane

Track A of the target-state migration (`architecture/26-cognee-migration-plan.md`).
Cognee-independent — this stands regardless of the pivot go/no-go.

## Acceptance criteria

- [x] **AC1** — Directory conventions exist: `.claude/skills/`, `loops/`,
  `playbooks/`, each with a `README.md` documenting the schema + trust rules (B4).
- [x] **AC2** — Loop/playbook manifests carry validated YAML frontmatter; loader
  `agents/_lib/control_plane.py` parses + validates against the schemas.
- [x] **AC3** — `cli/control.py list|validate` prints the inventory and fails
  (exit 1) on any malformed manifest (CI gate).
- [x] **AC4** — Existing recurring jobs are declared as loop manifests mirroring
  the launchd schedules: `morning-briefing` (0 6), `nightly-backup` (0 2). The
  bot stays a KeepAlive daemon (not a loop).
- [x] **AC5** — Seed skill (`spend-review`) and 3 seed playbooks
  (`daily-briefing`, `prospect-qualification`, `discovery-call-to-proposal`)
  exercise the conventions; the briefing loop resolves to a real playbook.
- [x] **AC6** — Unit tests cover parse + validation happy/error paths and assert
  the real seed content validates clean (`tests/test_control_plane.py`, 17 tests;
  suite 67/67). Lint clean.

## Scheduler daemon — builder-side DONE

- [x] **AC7** — `agents/scheduler/run.py`: reads `discover().enabled_loops()`,
  computes cron fire times (`croniter`), fires agent loops as
  `uv run python -m agents.<agent>.run` (with `COS_PLAYBOOK`) and command loops
  as their command, from the repo root under a login shell. `--dry-run` prints
  the schedule. SIGTERM/SIGINT exit cleanly (launchd KeepAlive target).
- [x] **AC8** — `launchd/com.aiadaptive.cos.scheduler.plist` (KeepAlive).
- [x] **AC9** — Tests (`tests/test_scheduler.py`, 9; suite 76/76): build_command
  agent/command variants, cron next-fire, plan() over the real seed loops
  (backup 02:00 before briefing 06:00). Lint clean. `--dry-run` verified.

## Runtime cutover (barry-agent — `/Users/Shared/afc-richmond/PHASE-3.6.md`)

- [ ] Pull; `uv sync` (new deps `croniter`, `pyyaml`); `--dry-run` sanity.
- [ ] **Cut over:** `bootout` the `briefing` + `pg-backup` calendar plists, then
  `bootstrap` the scheduler plist. The `discord-bot` plist STAYS (daemon, not a
  loop). Do NOT run old + new together (loops would double-fire).
- [ ] Validate: `launchctl kickstart` a test fire; confirm briefing + backup
  still run under the scheduler. **Not before Phase 3.5 runtime is closed.**

## Deferred to Track B

- [ ] `cli/publish_playbooks.py` (git→cognee publish for `publish_to_memory`
  playbooks) — Track B / W5, once the graph exists to publish into.

## Notes

- Skills registered with the harness on creation (verified: `spend-review`
  appears in the available-skills list).
- Obsidian, if used, is an authoring surface over `playbooks/`+`notes/`; git is
  the source of truth and sync.
