# Target-State Architecture

<doc:layer>vision — target state (proposed)</doc:layer>
<doc:stability>ADOPTED — the pivot executed; see Status</doc:stability>
<doc:depends_on>20-architecture-overview, 30-memory-layer, 40-action-layer, 80-telemetry-layer</doc:depends_on>
<doc:referenced_by>70-build-order (phase mapping)</doc:referenced_by>

## Status

**ADOPTED — proposed 2026-07-28, executed 2026-07-28 → 2026-08-03.** No longer
aspirational: the cognee pivot went GO, Phase 3.7 **deployed to production**
(MW7), the control plane is live under one scheduler daemon, and **B2 (approval
gate) is built and smoke-verified**. Read this as the as-built model plus the
channels still ahead (B3, email, Drive).

**Mitigation status:** **M1 (telemetry routing) is live and load-bearing** —
keep litellm pinned. **M2 (embedding normalization) is RETIRED**, superseded by
local FastEmbed bge@768, whose vectors are pre-normalized.

Still genuinely proposed: the Track C channels beyond Granola, and the exposure
posture in `PRD-b3-tunnel.md`.

---

## 1. The one idea: two planes

Everything in this architecture is one of two kinds of thing, and they must
never share a store:

- **Memory plane** — *what the system knows.* Ingested content, extracted into a
  graph, embedded, retrieved fuzzily at query time. **Untrusted** by default
  (anyone can email you). Lives in cognee / Postgres.
- **Control plane** — *how the system behaves.* Instructions you author:
  `CLAUDE.md`, skills, loops, playbooks. Loaded deterministically, **trusted
  because they came through git review**. Lives in the repo.

The failure this prevents: if a playbook lived in the same store as ingested
email, (a) an attacker could inject a "playbook," and (b) you'd lose diff /
review / rollback on the exact instructions your agents execute. Keeping the two
planes separate *is* the security model — see boundary B1.

---

## 2. Layered flow

Two input lanes, only one of them trusted, converging on the agent layer:

```
        reachable from anywhere via authenticated tunnel (B3)
        — the Postgres brain never leaves the box
 ┌──────────────────────────────────────────────────────────────┐
 │  INGESTION (inbound, untrusted)                                │
 │  API/webhooks · email in · Google Drive · #capture ·          │
 │  meetings (Granola) · web sources (Tartt)                      │
 └──────────────────────────────┬───────────────────────────────┘
        ▼  B1: ingested content is DATA, never instructions
 ┌───────────────────────────────────────┐   ┌──────────────────┐
 │  COGNEE MEMORY (local Postgres 17)     │   │  CONTROL PLANE   │
 │  cognify → graph+vector+relational     │◀╌╌│  (git, trusted)  │
 │  memify (decay/reinforce)              │   │  CLAUDE.md       │
 ├───────────────────────────────────────┤   │  skills          │
 │  TELEMETRY ledger (labels every call)  │   │  loops           │
 ├───────────────────────────────────────┤   │  playbooks ╌╌╌╌╌▶ │ (publish → memory)
 │  AGENTS (retrieve, reason, write back) │◀──│                  │
 │  recall · Roy Kent · meeting-proc ·    │   │  loaded &        │
 │  Keeley · Nate · Higgins · Ted ·       │   │  triggered by    │
 │  briefing                              │   │  agents          │
 └──────────────────────────────┬─────────┘   └──────────────────┘
        ▼  B2: approval gate — nothing outbound without a human yes
 ┌──────────────────────────────────────────────────────────────┐
 │  ACCESS & OUTPUT (outbound)                                    │
 │  Discord (query/instruct/brief) · Google Drive (proposals,    │
 │  briefs) · email out (drafts) · Buffer (published posts)      │
 └──────────────────────────────────────────────────────────────┘
```

The control plane enters *from the side* and **bypasses B1** — it is already
trusted because it came through git, not through an untrusted channel. Agents
reason over both: the memory plane (what's known) and the control plane (how to
act). Everything outbound still funnels through the single approval gate B2.

---

## 3. Trust boundaries

| ID | Boundary | Rule |
|----|----------|------|
| **B1** | Ingest → memory | Ingested content is **data, never instructions**. Extraction/retrieval treat it as inert. No ingested text may be interpreted as a command, an approval, or a playbook. The concrete channel-agnostic controls — typed short bounded fields, unicode/invisible-character stripping, datasets as an authorization boundary, scoped traversal, input screening for LLM prompts, provenance retention, trust decay — are specified as **H1–H7 in `35-outreach-crm.md` §11** and apply to every ingest channel, including email and Drive when they land. **Enforced on the retrieval side by `agents/_lib/retrieval.py`** (`30-memory-layer.md`): agents may not call `cognee.search` directly, scopes never union, the default is `UNTRUSTED`, and a CI grep fails the build on raw calls — B1 is a mechanism, not a convention. |
| **B2** | Agent → outbound | The **`#approvals` human gate**: sending email, publishing, creating a Drive doc, or any world-affecting act requires an explicit human yes. Inherited from the existing channel design; now guarding a larger surface. **Clarified by `35-outreach-crm.md` §13, in terms of *recipient*:** anything addressed to the operator (packets, briefing lines, own-calendar events, self-addressed digests) is exempt; anything addressed to a third party is gated. v1 outreach never crosses B2 because the human sends every message personally; if sending is ever automated, B2 applies immediately and unconditionally. |
| **B3** | Network exposure | "From anywhere" = an **authenticated API via a tunnel**. The Postgres brain listens on the local socket only and is never internet-reachable. **Split by audience (2026-08-08, `PRD-b3-tunnel.md` A2): Cloudflare Tunnel fronts machine callers that cannot join the tailnet (the WordPress webhook); Tailscale Serve is the default for every human surface** — NocoDB, the Shortcut, future admin pages — because Cloudflare terminates TLS at their edge and routing the prospect database through a third party contradicts the confidentiality posture the rest of the design pays for. |
| **B2-id** | Who may approve | The `#approvals` click is checked against an **explicit operator allowlist** in config (`PRD-b2-approval-gate.md` A1); unauthorized clicks are refused and logged to `#system`. High-consequence `item_type`s require a typed confirmation, not a bare tap. **Consequence: 2FA on the operator's Discord account is an architectural requirement** — B2's integrity reduces to that account's integrity, and every other boundary funnels through it. |
| **B4** | Control-plane provenance | Control-plane files reach the running system **only through git** (authored in barry-admin → committed → pulled to barry-agent). Nothing writes a skill/loop/playbook at runtime; the graph never mints one. |

---

## 4. Control plane — conventions

Lives in the existing repo (control plane = git). Authored in barry-admin,
pulled to barry-agent like all code.

```
aiadaptive-cos/
├── CLAUDE.md                 behavioral instructions (repo-scoped; may nest per-dir)
├── .claude/skills/           capability defs — loaded exactly as written
│   └── <skill>/SKILL.md
├── loops/                    recurring-task manifests (trigger + which playbook)
│   └── <loop>.md
├── playbooks/                SOPs / runbooks (loaded by name; optional publish→memory)
│   └── <playbook>.md
└── agents/ cli/ migrations/  code
```

**File roles**

- **`CLAUDE.md`** — how agents behave. Loaded at agent start, not retrieved. One
  per scope; nesting allowed (a subdir `CLAUDE.md` refines the root).
- **skills** — reusable capability definitions, loaded *as-is*. Deterministic:
  a skill is exactly what you wrote, never embedded, never fuzzy-matched.
- **loops** — the scheduled-trigger lane. A loop manifest names the schedule,
  the agent, and the playbook it runs. Loops are what fire Tartt (5am), the
  briefing (6am), Ted (6-hourly). The **trigger stays config/code** (the
  scheduler daemon); the loop file is the human-readable declaration of it.
- **playbooks** — SOPs the agents follow ("how to qualify a prospect", "how to
  turn a discovery call into a proposal"). The one hybrid artifact (§5).

**Frontmatter schemas** (YAML)

```yaml
# loops/<name>.md — a loop runs EITHER an agent (+optional playbook) OR a command
name: morning-briefing       # must match the filename stem
schedule: "0 6 * * *"        # 5-field cron; owned by the scheduler daemon
trigger_kind: scheduled
enabled: true
agent: briefing              # agent variant …
playbook: daily-briefing     # … + optional playbook (references playbooks/daily-briefing.md)
# command: "scripts/pg_backup.sh"   # …or the command variant (non-agent jobs, e.g. backups)
```

```yaml
# playbooks/<name>.md
name: prospect-qualification
description: How Roy Kent scores an inbound lead against the ICP.
applies_to: [prospect]       # entity types / workflows this governs
publish_to_memory: true      # if true, one-way-published to the trusted dataset
tags: [w1, icp]
```

**Obsidian** is an *authoring surface over these files*, not a component. Point a
vault at `playbooks/` (and a `notes/` scratch area); git remains the source of
truth and the sync. Do **not** enable a competing Obsidian Sync, and do not
confuse Obsidian's doc-link graph with cognee's semantic graph — different
things. It earns its keep only for `playbooks/`+notes; edit `CLAUDE.md` and
skills where you edit code.

---

## 5. The playbook publish path (git → cognee, one-way, trusted)

Playbooks are the only control artifact an agent may want to *retrieve*
semantically at runtime ("handling a prospect → pull the relevant playbook").
Pattern:

1. Git is the **source of truth** and the edit surface.
2. A publish step (`cli/publish_playbooks.py`, run on commit / CI) cognifies
   every playbook with `publish_to_memory: true` into a **dedicated `playbooks`
   dataset**, tagged `trusted`.
3. Agents retrieve playbooks with a search **scoped to that dataset only** —
   never mixed with untrusted ingest datasets.

This preserves B1 (a playbook can enter the graph *only* through the authored
git→publish path, never through email/Drive ingest) while giving playbooks
runtime retrievability. Skills and `CLAUDE.md` never get this — they must stay
purely deterministic.

---

## 6. Memory plane — cognee (recap + carried mitigations)

Graph + vector + relational, all on the existing local Postgres 17 (spike Q1:
provider `postgres`, no Apache AGE needed). Two mitigations from the spike are
**not optional** in the target state:

- **M1 (telemetry, mandatory):** route cognee's Anthropic calls through its
  litellm path (`LLM_PROVIDER=custom`, `LLM_MODEL=anthropic/…`) so the
  contextvar+callback telemetry fires for LLM calls, not just embeddings.
  Without it the `agent_runs` ledger silently loses ~99% of spend. Pin the
  litellm version — telemetry now structurally depends on its callback contract.
- **M2 (embedding) — RETIRED 2026-08-03.** Superseded by the switch to local
  FastEmbed `bge-base-en-v1.5` @768, whose vectors are pre-normalized; there is
  no un-normalized truncation left to correct. Original text, for the record:
  cognee keeps `gemini-embedding-001` @768 but does **not**
  L2-normalize truncated output. Renormalize on write, or use pgvector cosine
  `<=>` throughout and forbid inner-product `<#>`.

Config note: cognee 1.4.0 defaults access-control ON; for single-user set
`ENABLE_BACKEND_ACCESS_CONTROL=false` and give the vector + graph adapters their
own `VECTOR_DB_*` / `GRAPH_DATABASE_*` creds (they don't inherit `DB_*`).

---

## 7. New vs already built / planned

Most of the target state already exists or is on the roadmap; the genuinely new
surface is small.

| Component | State | Phase |
|-----------|-------|-------|
| Telemetry ledger, cost helper | **built** | 2 (+ 2026-07 refactor) |
| Discord capture / recall / outcome | **built** | 3.1–3.4 |
| Briefing skeleton, launchd, backups | **built** | 3.5 |
| Agent roster (Roy Kent, Keeley, Nate, Higgins, Ted, Tartt, Trent Crimm, meeting-proc) | **planned** | 4–11 · Sam retired 2026-08-08 (merged into Keeley) |
| Approval gate (`#approvals`) | **planned** | 8 |
| Buffer output | **planned** | 9 |
| **Cognee graph memory** (replaces flat `facts`) | **new** — pivot | new W-phase before 4 |
| **Control plane** (skills / loops / playbooks) | **new** | new, small; author early |
| **Email ingest + draft-out** | **new** | new channel |
| **Google Drive ingest + document output** | **new** | new channel |
| **Authenticated tunnel (B3)** | **new** — brings Phase-6 hosting decision early | new |
| **Outreach CRM** — evidence poller, NocoDB surface, BCC send-capture, Trent Crimm watchlist | **specified** — `35-outreach-crm.md` v0.3.0 (no generated prose in the outbound path) | Track O, `70-build-order.md` |
| **Inbound lead handling** | **OPEN** — constraints settled, design deliberately unmade | `36-inbound-leads.md`, gated on volume measurement |

The control plane is cheap to stand up (it's directory conventions + a scheduler
that already has to exist) and should be authored **early** — the agents built
in Phases 4+ read it from day one.

---

## 8. Risks / considerations

1. **The "from anywhere" requirement reopens the local-first decision.** Email,
   Drive, and an ingest API all require external reachability + Google OAuth —
   the opposite of the current air-gapped posture, on a system holding
   client-confidential data. B3 (tunnel + local DB) is the mitigation and is
   **mandatory**, not optional, the moment those channels land.
2. **Auto-ingesting email/Drive is an injection surface wired to actions.** This
   is the highest-severity risk: untrusted content → graph → LLM calls that
   create documents and drive workflows. B1 + B2 are the defense and must be
   absolute.
3. **Telemetry now depends on a transitive contract (M1).** Pin litellm; own the
   upgrade path.
4. **Ingestion volume drives cost.** Each ingested doc is a cognify run
   (~$0.005/short doc, dashboard-confirmed). At scale the ledger must watch an
   ingestion firehose — another reason M1 is load-bearing.
5. **Scope.** Each new channel (email, Drive, API gateway) is an integration
   with its own auth. Sequence them; don't build the whole surface at once.

---

## 9. Incremental adoption

Order that keeps every step independently valuable:

1. **Control plane first** (days, no pivot needed): create `playbooks/`,
   `loops/`, `.claude/skills/` conventions; move recurring jobs to loop
   manifests. Useful immediately, regardless of the cognee decision.
2. **Cognee memory** (the pivot; ~9–12 days per the spike, with M1/M2).
3. **Playbook publish path** once agents actually need to *retrieve* a playbook
   rather than be handed one.
4. **Channels, one at a time** — email, then Drive — each behind B1/B2, with B3
   stood up before the first externally-reachable channel.

Highest-leverage capabilities this unlocks (for a solo operator): the
**self-assembling pre-call brief** and **graph-grounded proposal/follow-up
generation** — see the conversation that produced this doc for the full
capability survey.
