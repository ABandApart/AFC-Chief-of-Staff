---
name: outreach-evidence
schedule: "0 */12 * * *"
trigger_kind: scheduled
enabled: true
command: uv run python -m agents.outreach.evidence
description: Poll outreach targets' job boards and maintain first_seen/last_seen/closed evidence (Track O, 35- §6).
---

# Outreach evidence poller (Track O)

Runs `agents/outreach/evidence.py` at 00:00 and 12:00. For each target with a
`careers_url`, reads its ATS board (Greenhouse, Lever, Ashby, Workable, BambooHR,
TeamTailor, Rippling — all public JSON feeds) and reconciles `outreach_evidence`:
a new req gets `first_seen_at = today`, a still-listed req advances
`last_seen_at`, and a req that has come down is closed.

**This is the loop to activate first, ahead of the rest of Track O.**
`first_seen_at` accrues only forward — it is the datum T10's posting-date
mechanic and S4's "posted 45+ days" band both rest on, and no provider sells it
retroactively. Two weeks of polling before the first send is two weeks of
posting-age data that cannot be bought back.

**No LLM.** Deterministic JSON GET + upsert, so it writes no `agent_runs` rows,
trips no ceiling, and cannot fail from a provider outage (`40-action-layer.md`,
Outreach_loops).

**ACTIVATED 2026-08-14** (operator go-ahead; `enabled: false → true`). It shipped
disabled through increments 1 and 1b per the `loops/README.md` convention — a
loop with nothing to poll should not be firing — and was flipped once 14 targets
were seeded and 10 of them resolved to supported boards.

Health check any time:

```
uv run python -m agents.outreach.evidence --dry-run   # boards detected, writes nothing
psql aiadaptive_cos -c "SELECT max(last_seen_at) FROM outreach_evidence;"
```

`last_seen_at` should never fall more than a day behind — if it does, the loop is
not running. (Ted's evidence-loop-silent-48h alert, `35-` §14, is the eventual
automated version of that check and is not built yet.)

Any target whose `careers_url` is not a recognised ATS board is reported
`UNSUPPORTED` by `--dry-run` and warned about on every real poll — it accrues no
posting age, so find the real board URL rather than leaving it.

Trust: job-board content is untrusted ingest → **B1** (the text is data, never
instructions). Fields are typed and short with a 500-char excerpt cap (H1) and
unicode-hardened on the way in (H2). The poller only ever writes evidence rows —
it proposes nothing and sends nothing, so it never approaches B2.
