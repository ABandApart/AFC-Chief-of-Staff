---
name: ted-judgment
schedule: "30 7 * * 1"
trigger_kind: scheduled
enabled: false
command: uv run python -m agents.ted.run --judgment
description: Ted's weekly review — a headless Claude Code session on the Claude subscription, no tools, evidence in the prompt (PRD-ted-ops-monitor D8).
---

# Ted, the weekly review

Runs `agents/ted/run.py --judgment` at **07:30 on Mondays**. Ted gathers a week of
evidence with SQL and file metadata (run failures, sourcing runs, open findings, the
shared folder's files and their open checklist items), then runs one headless
`claude -p` session to name friction a rule can't see.

- **Subscription, not API:** the session's environment has no `ANTHROPIC_API_KEY`.
  It signs in with the subscription token in barry-agent's keychain
  (`claude-oauth-token`, made once with `claude setup-token`).
- **Boxed in:** no tools (`--tools ""`), no settings, hooks, or MCP servers
  (`--restricted --strict-mcp-config`), structured output (`--json-schema`).
- **Findings** are MEDIUM or INFO only, and go to `INBOX-admin.md`, never to the
  operator's phone.
- **Ledger:** one `agent_runs` row (`ted` / `telemetry`, provider
  `anthropic-subscription`, `usd_cost` 0).

**Ships disabled.** Before enabling: `--probe` passes as barry-agent (the
subscription answers headless), and one `--judgment --dry-run` reads well.
