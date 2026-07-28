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

## Deferred to a follow-up (runtime — barry-agent)

- [ ] **Scheduler daemon** that reads `discover().enabled_loops()` and fires them,
  replacing the per-job launchd plists (refactor A7). Build builder-side, then
  cut over in runtime. **Do NOT bootout launchd** until the daemon is validated
  live — and not before Phase 3.5 runtime is closed (the launchd jobs it
  installs aren't validated yet).
- [ ] `cli/publish_playbooks.py` (git→cognee publish for `publish_to_memory`
  playbooks) — belongs to Track B / W5, once the graph exists to publish into.

## Notes

- Skills registered with the harness on creation (verified: `spend-review`
  appears in the available-skills list).
- Obsidian, if used, is an authoring surface over `playbooks/`+`notes/`; git is
  the source of truth and sync.
