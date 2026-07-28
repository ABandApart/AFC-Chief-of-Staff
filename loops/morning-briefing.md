---
name: morning-briefing
schedule: "0 6 * * *"
trigger_kind: scheduled
enabled: true
agent: briefing
playbook: daily-briefing
description: Post the good-morning system status / digest to #briefing at 6:00 local.
---

Mirrors the `com.aiadaptive.cos.briefing` launchd job (6:00, one-shot). The
`briefing` agent currently posts a static status (Phase 3.5); the
`daily-briefing` playbook is what it will follow once it does real synthesis
over the brain (post-3.7).
