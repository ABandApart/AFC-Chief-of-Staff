# Loops — control plane

Recurring-task manifests. A loop is the **declaration** of a scheduled unit of
work; one scheduler daemon reads these and fires them (replacing the per-job
launchd plists — refactor item A7). Part of the control plane: authored in git,
trusted (boundary B4).

> **Status:** manifests are the source-of-truth declarations *now*; the
> scheduler daemon that executes them is the next Track-A step. Until it lands,
> launchd still runs the jobs — these manifests mirror those schedules so the
> cutover is mechanical. Do not bootout launchd until the daemon is validated in
> runtime (barry-agent).

## Convention

One file per loop: `loops/<name>.md`. A loop runs **either** an agent (+optional
playbook) **or** a command.

```yaml
---
name: <slug>                 # must match the filename stem
schedule: "<m h dom mon dow>" # 5-field cron
trigger_kind: scheduled
enabled: true
# exactly one target:
agent: <agent-name>          # agent variant …
playbook: <playbook-name>    # … optional; references playbooks/<name>.md
# command: "<script or module>"   # …or the command variant (non-agent jobs)
description: <one line>
---

<optional notes>
```

Rules enforced by the loader (`agents/_lib/control_plane.py`, `cli/control.py
validate`): `name` matches the filename; `schedule` is a valid 5-field cron;
exactly one of `agent` / `command`; a referenced `playbook` must exist when the
loop is `enabled`.

## Not loops

The Discord bot is a **KeepAlive daemon**, not a scheduled loop — it stays a
supervised long-running process, not a manifest here.

## Planned (author as their agents land)

`tartt-discovery` (`0 5 * * *`), `ted-health` (`0 */6 * * *`),
`nate-weekly` (`0 20 * * 0`), `higgins-weekly` (`0 7 * * 1`).
