---
name: nightly-backup
schedule: "0 2 * * *"
trigger_kind: scheduled
enabled: true
command: "scripts/pg_backup.sh"
description: Nightly pg_dump of aiadaptive_cos + aiadaptive_cognee, gzip'd, keep 14 days.
---

Mirrors the `com.aiadaptive.cos.pg-backup` launchd job (2:00). A command loop —
no agent, no LLM, so it does not go through the cost helper. Dumps **both** brain
databases: `aiadaptive_cos` (operational) and `aiadaptive_cognee` (the cognee
knowledge graph, added in the 3.7 pivot — irreplaceable captured knowledge).
