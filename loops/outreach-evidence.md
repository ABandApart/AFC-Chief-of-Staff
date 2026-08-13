---
name: outreach-evidence
schedule: "0 */12 * * *"
trigger_kind: scheduled
enabled: false
command: uv run python -m agents.outreach.evidence
description: Poll outreach targets' job boards and maintain first_seen/last_seen/closed evidence (Track O, 35- §6).
---

# Outreach evidence poller (Track O)

Runs `agents/outreach/evidence.py` every 12 hours. For each target with a
`careers_url`, reads its ATS board (Greenhouse / Lever / Ashby JSON APIs) and
reconciles `outreach_evidence`: a new req gets `first_seen_at = today`, a
still-listed req advances `last_seen_at`, and a req that has come down is closed.

**This is the loop to activate first, ahead of the rest of Track O.**
`first_seen_at` accrues only forward — it is the datum T10's posting-date
mechanic and S4's "posted 45+ days" band both rest on, and no provider sells it
retroactively. Two weeks of polling before the first send is two weeks of
posting-age data that cannot be bought back.

**No LLM.** Deterministic JSON GET + upsert, so it writes no `agent_runs` rows,
trips no ceiling, and cannot fail from a provider outage (`40-action-layer.md`,
Outreach_loops).

**Ships DISABLED** (`enabled: false`). Activate once targets with real
`careers_url` values are seeded — before that it has nothing to poll. Check what
it would do first:

```
uv run python -m cli.outreach_import targets.csv --dry-run   # seed targets
uv run python -m agents.outreach.evidence --dry-run          # confirm boards detected
```

Any target whose `careers_url` is not a recognised ATS board is reported
`UNSUPPORTED` by `--dry-run` and warned about on every real poll — it accrues no
posting age, so find the real board URL rather than leaving it.

Trust: job-board content is untrusted ingest → **B1** (the text is data, never
instructions). Fields are typed and short with a 500-char excerpt cap (H1) and
unicode-hardened on the way in (H2). The poller only ever writes evidence rows —
it proposes nothing and sends nothing, so it never approaches B2.
