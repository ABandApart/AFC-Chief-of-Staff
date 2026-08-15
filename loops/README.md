# Loops — control plane

Recurring-task manifests. A loop is the **declaration** of a scheduled unit of
work; one scheduler daemon reads these and fires them (replacing the per-job
launchd plists — refactor item A7). Part of the control plane: authored in git,
trusted (boundary B4).

> **Status (corrected 2026-08-14):** the scheduler daemon is **live** —
> `com.aiadaptive.cos.scheduler` under barry-agent, cut over 2026-07-28, with the
> old per-job calendar plists disabled. These manifests are what actually runs.
> *(This block previously described the daemon as "the next Track-A step" months
> after it landed — the staleness `70-build-order.md` §Working convention S6
> exists to catch.)*

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

## Activation — ships disabled, flipped deliberately

**Written down 2026-08-14.** This was an unwritten habit (`tartt-poll`,
`granola-poll`, `outreach-evidence` all shipped `enabled: false`) that the
operator reasonably could not find in any spec. It is a convention, now stated:

**A new loop ships `enabled: false`.** A manifest is authored in the same commit
as the agent it runs, but the agent is rarely ready to run unattended the moment
its code lands — it usually needs seed data, a credential, or one manual run that
looks right. Shipping enabled means the scheduler starts firing it on the next
pull, before anyone has looked.

**Activating is a two-account, two-step move:**

1. **barry-admin** flips `enabled: false → true` and pushes. Nothing happens yet
   — the repo is not what the scheduler reads.
2. **barry-agent** pulls. The scheduler re-reads manifests every 60 seconds, so
   the loop starts firing on its cron within about a minute.

> **This used to require a daemon restart, and the earlier version of this note
> said otherwise.** Until 2026-08-15 the scheduler read manifests **once at
> startup** and never again, so a pulled `enabled: true` did nothing until
> someone happened to restart `com.aiadaptive.cos.scheduler`. It cost
> `outreach-evidence` a night of polling: the flag was set, the file was pulled,
> every health signal looked fine, and the loop simply never fired. `reload()`
> now runs on each wake and logs what changed. If you are ever debugging a loop
> that will not fire, `scheduling N loop(s): …` in the scheduler log names
> exactly what it believes it is running.

**Confirm with the operator before flipping.** Not ceremony: activation is the
moment a loop starts doing things unattended — spending, calling third parties,
or writing on a schedule nobody is watching. The operator is the one who knows
whether the preconditions are actually met.

**State what activation costs when you ask.** The answer differs sharply per
loop and is what the decision turns on:

| Loop | What activation starts |
|------|------------------------|
| `tartt-poll` | Real Gemini spend, per item, on a 6-hourly cadence |
| `outreach-evidence` | Nothing billable — public JSON GETs, no LLM, no `agent_runs` rows, writes only `outreach_evidence` |

A loop whose activation costs nothing and whose data only accrues forward should
be activated as soon as it works; one that spends money on every cycle deserves
the pause. Do not apply the same caution to both — treating a free read-only poll
like a metered LLM loop just delays data you cannot backfill.

## Not loops

The Discord bot is a **KeepAlive daemon**, not a scheduled loop — it stays a
supervised long-running process, not a manifest here.

## Planned (author as their agents land)

`tartt-discovery` (`0 5 * * *`), `ted-health` (`0 */6 * * *`),
`nate-weekly` (`0 20 * * 0`), `higgins-weekly` (`0 7 * * 1`).
