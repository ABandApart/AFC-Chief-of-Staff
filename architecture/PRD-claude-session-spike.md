# Claude CLI session spike — last week's meetings → a Gmail draft

<doc:meta>
  <doc:type>Spike spec (minimal test software)</doc:type>
  <doc:status>BUILT builder-side 2026-10-05 (branch `claude-session-spike`; V1 and V4 pass on the build box, V4 = 23 tests). V2 and V3 need barry-agent.</doc:status>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:depends_on>`ADR-0001` (Claude Code as an alternate shell); `agents/_lib/granola_client.py`; `agents/outreach/gmail.py` (OAuth service, G3 send guard)</doc:depends_on>
  <doc:size>Two new modules, tests, one ceiling entry. No schema change.</doc:size>
</doc:meta>

## Purpose

Prove the pattern "an agent launches a headless Claude CLI session, the session
does work, and the result is collected in our systems" on one real task: draft a
summary of last week's meetings into the operator's Gmail Drafts folder.

## Outcome (S1)

**Running `uv run python -m agents.meeting_digest.run` as barry-agent leaves one
new draft in barry@aiadaptive.co's Drafts folder, addressed to
barry@aiadaptive.co, whose body summarizes every Granola meeting dated in the
previous Monday–Sunday week, and writes one `agent_runs` row carrying the CLI
session's reported cost.**

## Design

1. **Gather (deterministic, no LLM).** `agents/meeting_digest/run.py` lists Granola
   notes updated since the start of last week, fetches each, and keeps the ones
   whose meeting date falls in last week (local time, Monday 00:00 to the next
   Monday 00:00). Each meeting is assembled with the existing
   `granola_client.assemble_note_text` and capped at 12,000 characters, so the
   prompt size and cost stay bounded.
2. **Launch the session.** `agents/_lib/claude_cli.py` runs
   `claude -p --bare --output-format json --json-schema <schema> --tools ""
   --strict-mcp-config --no-session-persistence --max-budget-usd <cap> --model <model>`
   with the prompt on stdin and `ANTHROPIC_API_KEY` set from the keychain
   (`anthropic-api-key`). The session has **no tools and no MCP servers**: the
   meeting text is data in the prompt, and the session can only answer.
3. **Collect.** The wrapper parses the JSON result: the structured output
   `{subject, body}`, cost, and token usage. It writes one `agent_runs` row
   (`agent_name = meeting-digest`, `function_label = meeting_digest`,
   `llm_provider = anthropic`, `llm_model = <model>`, `usd_cost` from the CLI).
4. **Deliver.** The runner checks that the Gmail OAuth token belongs to
   barry@aiadaptive.co (`users.getProfile`), then calls `drafts().create` with
   the operator as the only recipient. **Never a send:** G3
   (`tests/test_no_outbound_send.py`) applies. A draft addressed to the operator is
   exempt from B2 (`35-` §13), and it isn't sent either way.

## Non-goals (S2)

- **Not a production loop.** No scheduler manifest; run by hand.
- **No tools for the session.** Brain access through the MCP tool layer is the
  next step, not this spike.
- **No storage of the summary** beyond the draft and the ledger row.
- **No idempotency.** Running it twice creates two drafts.
- **No per-call ledger rows.** The CLI reports one total per session; that is the
  accepted limit of a third-party shell (ADR-0001).

## Verification (S3)

| # | Check | Pass when |
|---|---|---|
| V1 | Dry run | `--dry-run` prints the meeting list for last week and the prompt size, and calls neither Claude nor Gmail. |
| V2 | Real run | Exit 0; a new draft appears in barry@aiadaptive.co's Drafts, To: barry@aiadaptive.co, covering each meeting from V1. |
| V3 | Ledger | `SELECT usd_cost, llm_model FROM agent_runs WHERE agent_name='meeting-digest' ORDER BY id DESC LIMIT 1` matches the cost the run printed. |
| V4 | Unit tests | Week bounds; meeting filtering by date; command line has `--tools ""`, `--bare`, `--strict-mcp-config`; JSON parsing (structured output, error result, malformed output); ledger row values; refusal when the Gmail account is not barry@aiadaptive.co. Suite passes without optional dependency groups. |

## Settled

- Model: `claude-opus-5-5` by default (`--model` overrides). Budget cap: $1.00 per
  session (`--max-budget`), and a `meeting-digest` daily ceiling of $2.00.
- Function label `meeting_digest` is new to the taxonomy.

## Open (S4)

- **Which Google account the existing OAuth token belongs to.** The spec assumes
  barry@aiadaptive.co (`PRD-outreach-gmail-channel.md` G1, own-domain Workspace).
  The runner checks and refuses otherwise; the first run settles it.
- ~~Field name of the structured output.~~ **Settled 2026-10-05:** a no-key run of
  the exact command (CLI 2.1.282) accepted every flag and returned a JSON result
  with a `structured_output` field. The same run showed that an auth failure
  reports `subtype: "success"` with `is_error: true`, so the parser checks
  `is_error` first. The fallback to parsing `result` stays, at no cost.
- **Client confidentiality.** Meeting text, including client meetings, goes to
  Anthropic in the prompt, as it already does through cognify. Whether
  client-meeting content should be excluded belongs to the separate-database
  discussion, not this spike.
