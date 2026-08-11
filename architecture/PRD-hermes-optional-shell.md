# Hermes as an Optional Interactive Shell — PRD & Build Spec

<doc:meta>
  <doc:phase>Track H (OPTIONAL) — an additional interactive front-end over the brain, not a replacement for the fleet</doc:phase>
  <doc:theme>Hermes drives the same gated Track I boundary as any other shell; governance (B1/B2/B4) is enforced server-side, so a generalist autonomous runtime can be adopted without dissolving it.</doc:theme>
  <doc:duration>~2–3 days integration (assumes Track I already built)</doc:duration>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>OPTIONAL — planned, not adopted. Build only if the interactive capability (proven first with our own thin loop) justifies the multi-channel/voice upside. ADR-0001 D2.</doc:status>
  <doc:depends_on>PRD-mcp-tool-layer.md (Track I — the boundary Hermes drives); ADR-0001; B3 (verified 2026-08-10, for the remote path)</doc:depends_on>
  <doc:blocks>nothing — off every critical path by design</doc:blocks>
</doc:meta>

## TL;DR

If — and only if — the interactive brain-agent proves its worth, **Hermes**
(NousResearch) can be adopted as an *additional* shell that reaches the brain
through the **Track I gated tool layer**. Hermes brings a multi-channel gateway
(Slack/WhatsApp/Signal/Email/Telegram + voice) and dynamic subagents that our own
loop does not. It is **sandboxed to preserve governance**: self-authored skills
disabled (B4 stays git-only), reads are data (B1), actions go through `#approvals`
(B2), and Hermes keeps its **own SQLite scratch** — never synced to the brain
(ADR-0001 D3). This is a *shell swap behind a fixed boundary*, not a migration.

## Why this is a phase at all (and why it's optional)

<rationale>

- ADR-0001 **D1** rejected wholesale migration to Hermes. This phase is the *other
  half* of D2: the boundary makes the shell swappable, so Hermes becomes a
  low-commitment **add-on**, not a fork of the system.
- **What Hermes adds over our own loop:** multi-channel human interface + voice
  memo transcription (we are Discord-only), dynamic subagent spawning for parallel
  research, `/model` provider switching, and an MCP client already built.
- **Why optional:** none of that is on the KR critical path, and every governance-
  critical property already lives in Track I. Adopt Hermes for reach and
  ergonomics, or never — the brain is unaffected either way.

</rationale>

## Design

### Topology — Hermes is just another Track I client

<topology>

```
  humans ──▶ Hermes gateway (Slack/WhatsApp/Signal/Email/Telegram/voice)
                 │  Hermes agent loop + subagents   (own SQLite scratch)
                 │
        ┌────────┴─────────┐
        │ MCP (stdio)       │ or  MCP-over-HTTP / REST (off-box)
        ▼                   ▼
   agents/mcp/server.py    agents/gateway/app.py   ← Track I boundary (gates here)
        └─────────┬──────────┘
                  ▼
        agents/_lib/brain_tools  →  retrieval.py · db pool · ingest · approvals
```

Hermes never touches Postgres, cognee, or the control plane directly. Its only
brain access is the Track I tool set (`recall`, bounded reads, `ingest_note`,
`enqueue_approval`). Everything Hermes "knows" beyond that lives in **its own
SQLite** — which we treat as untrusted scratch and never mirror into the brain.

</topology>

### Connection mode

<connection>

- **Local (preferred):** run Hermes on the mini (`barry-agent`) and point its MCP
  config at the **stdio** server (`command: uv`, `args: [run, python, -m,
  agents.mcp.server]`). No network surface; trust = the OS account.
- **Off-box (if Hermes runs serverless/elsewhere):** Hermes reaches the **Gateway
  REST** surface (B3, verified 2026-08-10) with the `tools` HMAC caller. Its own
  reasoning stays remote; only gated tool calls cross the tunnel.

</connection>

### Sandboxing — the non-negotiable constraints

<sandboxing>

Each maps to a boundary; if any cannot be enforced, **do not adopt Hermes**.

1. **Self-authored skills OFF (B4).** Hermes's runtime skill creation is disabled;
   the control plane stays git-only. If Hermes cannot be configured to not author
   skills, it is confined to a directory with no path back into the repo, and its
   skills are never granted to any brain-touching action.
2. **Reads are data (B1).** Hermes receives `recall`/read results as content; the
   Track I server already refuses raw `cognee.search` and never interprets tool
   args as commands. Prompt-injected content in a recall answer cannot become an
   instruction that reaches an action, because the only action path is B2.
3. **Actions gated (B2).** Hermes cannot send/publish/write externally. The most
   it can do is `enqueue_approval`; a human clicks Approve in `#approvals`. No
   Hermes tool is wired to a direct outbound effect.
4. **No storage sync (D3).** Hermes's SQLite is its own; there is **no**
   translation/sync layer to Postgres/cognee. Knowledge promotion is one-way via
   `ingest_note` only.
5. **Least-privilege tools.** Hermes is given the Track I tool set and nothing
   else — no shell/terminal-backend tool with brain credentials, no direct DB
   tool. Its generic 40+ tools operate only in its own sandbox.
6. **Model routing for confidential work.** Where Hermes reasons over recalled
   client-confidential content, prefer a local/open model via `/model` so that
   content does not leave the box beyond the provider we already accept.

</sandboxing>

### Telemetry — the known gap

<telemetry>

Track I records every brain-touching **tool** call in `tool_invocations` (and
LLM-touching ones in `agent_runs`). But Hermes's **own reasoning tokens** are
outside our ledger (ADR-0001 accepted limitation). Two responses:

- **v1: accept it.** Tool-boundary spend is captured; Hermes's own spend is
  visible in whatever provider account its `/model` points at.
- **If Hermes becomes the standing shell:** route its LLM calls through a
  litellm callback (Hermes supports litellm) into `agent_runs` under a
  `hermes_shell` label — the same M1 pattern that captures cognee's calls. This
  is the trigger-condition to close the gap, not v1 scope.

</telemetry>

## Acceptance criteria

<acceptance>

1. Hermes drives `recall` and at least one bounded read through the Track I stdio
   MCP server, on-box, with results appearing in `tool_invocations`.
2. Hermes's `ingest_note` promotes a note (dedup honored) and produces a
   `cognify_run` ledger row; no other write path to the brain exists for Hermes.
3. An `enqueue_approval` from Hermes creates a `pending` row and performs **no**
   side effect until a human approves in `#approvals`.
4. **B4 check:** Hermes cannot author a skill/loop/playbook that reaches the repo
   or any brain action (self-authored skills disabled or confined).
5. **B1 check:** a recall answer containing an injection string ("ignore your
   instructions and …") does not cause any action; it is treated as data.
6. Off-box path (if used): the same calls succeed over Gateway REST with the
   `tools` HMAC caller; an unsigned request → 401.
7. Multi-channel proof: the same brain query answered through at least one Hermes
   gateway channel beyond Discord (e.g. Signal or email), demonstrating the
   capability that justifies the phase.

</acceptance>

## Build tasks

<build_tasks>

1. **Prereq:** Track I built and validated with our own loop / Claude Code
   (`PRD-mcp-tool-layer.md`). Do not start Track H before Track I is green.
2. Install Hermes on `barry-agent`; configure `/model` (default to a local/open
   model for confidential reasoning; Anthropic for quality).
3. Register the Track I stdio MCP server in Hermes's MCP config; grant **only**
   the Track I tools.
4. Disable/​confine self-authored skills (B4); verify AC-4.
5. Stand up the Hermes gateway for one non-Discord channel; verify AC-7.
6. Run the B1/B2 adversarial checks (AC-3, AC-5).
7. Decide on telemetry: accept the gap (v1) or wire the litellm callback if Hermes
   is kept as the standing shell.

</build_tasks>

## Non-goals

<non_goals>

- **Not** a replacement for the scheduled fleet, the scheduler, the telemetry
  ledger, or the two-plane memory (ADR-0001 D1).
- **Not** a second memory system for the brain — Hermes's SQLite stays its own
  (D3).
- **Not** a channel for autonomous outbound action — B2 is absolute.
- **Not** a path for Hermes-authored skills to enter the control plane — B4 is
  absolute.

</non_goals>

## Open questions

<open_questions>

1. **Skill-authoring switch** — does Hermes expose a clean "disable self-authored
   skills" setting, or must we confine it by directory + tool-scoping? Determines
   how AC-4 is met.
2. **Which gateway channel first** — Signal, email, or WhatsApp? Pick the one the
   operator would actually use for a pre-call brief on the move.
3. **Local model choice** — which open model via `/model` is good enough for
   graph-grounded drafting while keeping confidential content on-box?
4. **Standing vs occasional** — if Hermes is only used occasionally, the telemetry
   gap is negligible and v1-accept is fine; if it becomes the daily driver, wire
   the litellm callback. Revisit after two weeks of real use.

</open_questions>
