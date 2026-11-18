---
name: ted
schedule: "7 * * * *"
trigger_kind: scheduled
enabled: false
command: uv run python -m agents.ted.run
description: Ted's hourly ops check — pure SQL, no LLM; findings to INBOX-admin.md and urgent Briefing notes (PRD-ted-ops-monitor).
---

# Ted, the hourly cycle

Runs `agents/ted/run.py` at **:07 every hour**. No model call and no API key: it
must report a credit outage, so it can't depend on the credit (O9).

- **Detects:** credit and auth failures across all agents (one URGENT finding per
  class), 24 h failure rates, 2 failures in a row, silent agents, sourcing runs
  that stopped early, spend at 80% of a ceiling, and a stale `CURRENT-PHASE.txt`.
- **Raises** a finding when it's new, when it comes back after 3 h clear, or when
  it's unacknowledged and was last raised 24 h ago. State: `ted_findings` (0040).
- **Reports** to `/Users/Shared/afc-richmond/INBOX-admin.md` (barry-admin) and, for
  URGENT and HIGH, an urgent note on the day's Briefing page that @-mentions the
  operator. barry-admin acknowledges in `ACK-admin.md` (`ack: <key> <date> <note>`).
- **Pings `cos-ted`** (period 1 h, grace 15 min) on success, and `/fail` when an
  operator alert couldn't be delivered.

**Ships disabled.** Before enabling: migration `0040_ted_findings.sql` applied, the
`cos-ted` check created on healthchecks.io (H1), and one `--dry-run` that looks right.
