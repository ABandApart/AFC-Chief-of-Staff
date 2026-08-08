# Architecture Overview

<doc:layer>bridge — strategy to implementation</doc:layer>
<doc:stability>medium — edit on major topology changes</doc:stability>
<doc:depends_on>10-strategy.md, 25-target-state.md, 26-cognee-migration-plan.md</doc:depends_on>
<doc:referenced_by>30-memory-layer.md, 35-outreach-crm.md, 36-inbound-leads.md, 40-action-layer.md, 50-channel-layer.md, 60-content-pipeline.md, 80-telemetry-layer.md, 90-workflows.md</doc:referenced_by>

## Purpose

The structural overview that bridges strategy and implementation: the layers, the
agents in the action layer, how data flows, and — since the **Phase 3.7 cognee
pivot** — **which functions are leveraged from cognee versus built natively in
this system**. Schema and integration detail live in the implementation files
(30-, 40-, 50-, 60-, 80-) and `25-target-state.md`.

> **Post-cognee.** The memory layer is no longer a hand-rolled `facts` table with
> hybrid search on hosted Supabase. It is **two local Postgres databases**: a
> cognee-managed knowledge graph and a plain-SQL operational store. Ingestion is
> channel-agnostic; recall is GraphRAG. See §"Cognee vs native" for the split.

---

## Cognee vs native — what we leverage, what we build

<cognee_boundary>

The pivot's central design fact: **cognee owns the knowledge substrate; we own
everything around it.** Nothing that matters operationally (telemetry, auth,
channels, control plane, agent reasoning) lives inside cognee.

**Leveraged from cognee** (the `aiadaptive_cognee` DB + the `cognee` library):
- **Entity & relationship extraction** — cognee's `cognify` LLM step turns
  free-text notes into graph entities/edges.
- **Entity resolution** — merging "Elena Ruiz" across notes into one node.
- **Embedding + vector indexing** — via the embedder we configure (local
  FastEmbed, below).
- **Graph storage** — nodes + edges + the three backing stores (relational,
  vector `pgvector`, graph `postgres`), all inside `aiadaptive_cognee`.
- **Retrieval / GraphRAG** — `GRAPH_COMPLETION`: vector hint → graph traversal →
  synthesized answer.

**Built natively in this system:**
- **Ingestion orchestration** — `agents/_lib/ingest.ingest_note`: message-hash
  dedup (`capture_messages`), telemetry labeling, dataset/spend routing — a thin
  channel-agnostic wrapper *around* `cognee.add` + `cognify`.
- **Channel adapters** — the Discord bot + cogs, the **Gateway API** (FastAPI +
  HMAC + tunnel), the **Granola** meeting poller. Each is a thin `ingest_note`
  caller.
- **Telemetry** — the labeling callback (M1), the `agent_runs` ledger, the soft
  daily breaker, `cli/spend` + `cli/reconcile`. **cognee's own LLM spend is
  captured by *our* litellm callback**, not by cognee.
- **cognee configuration & isolation** — `agents/_lib/cognee_setup`: dedicated
  DB, M1 routing, config-cache clearing.
- **Typed ontology** — `agents/_lib/ontology` (8 DataPoints) for *structured*
  ingestion (meetings/content), distinct from free-text capture.
- **Operational store** — every SQL table (`prospects`, `outcomes`, `tasks`,
  `agent_runs`, `capture_messages`, `channel_state`, …) + the connection pool.
- **Agent reasoning** — Roy Kent qualification, Keeley triage+draft+self-check,
  Higgins reporting, Trent Crimm classification, Ted anomaly detection. These call Anthropic **directly
  through our cost helper (`agent_run`), not through cognee.**
- **Auth & gates** — HMAC request signing, the `#approvals` human gate, trust
  boundaries.

</cognee_boundary>

---

## Topology

<topology>

```
┌───────────────────────────────────────────────────────────────────────┐
│  CHANNEL / INGRESS                                                     │
│  Discord (mobile+desktop): #briefing #approvals #capture #system …    │
│     — status checks, queries, kickoffs, digest; #capture = one caller │
│  Gateway API (FastAPI 127.0.0.1:8788, HMAC, Cloudflare Tunnel):       │
│     POST /ingest  ← PRIMARY ingestion (tools, shortcuts)              │
│     POST /webhook/leads   GET /health                                 │
│  Granola poller (scheduled): meeting notes → ingest                   │
│  Claude Code CLI: ad-hoc sessions over the brain                      │
└───────────────────────────┬───────────────────────────────────────────┘
                            │  ingest_note()  /  graph_recall()
┌───────────────────────────▼───────────────────────────────────────────┐
│  ACTION (Mac mini, barry-agent, launchd + scheduler daemon)           │
│  Ingest core: agents/_lib/ingest → cognee.add + cognify (labeled)     │
│  Recall core: agents/_lib/graph_recall → cognee GRAPH_COMPLETION      │
│  Agents (reason via OUR cost helper, not cognee):                     │
│    Roy Kent · Tartt · Nate · Keeley · Trent Crimm · Briefing · Higgins│
│    · Ted · Keeley Distribution (no LLM)                               │
│  Control plane (git): skills / loops / playbooks + scheduler          │
│  Telemetry: labeling callback (M1) + soft breaker + reconcile         │
└──────────────┬────────────────────────────────────┬───────────────────┘
               │ knowledge                           │ operational state
┌──────────────▼─────────────────────┐  ┌────────────▼───────────────────┐
│  aiadaptive_cognee  (COGNEE)        │  │  aiadaptive_cos  (native SQL)   │
│  graph + vector(pgvector) +         │  │  agent_runs (ledger) · outcomes │
│  relational — facts, people, orgs,  │  │  prospects · tasks · follow_ups │
│  decisions, meetings, ICP, content. │  │  content_pipeline · approval_q  │
│  cognee owns extract/resolve/embed/ │  │  capture_messages · channel_st. │
│  retrieve. LLM=Anthropic(litellm/M1)│  │  dashboard · sources            │
│                                     │  │  outreach_{targets·evidence·    │
│                                     │  │   touches·packets·events} (35-) │
│  Embeddings=local FastEmbed bge@768 │  │  (status machines, queues,      │
│  Gemini reserved for news (Tartt).  │  │   ledgers — plain SQL via pool) │
└─────────────────────────────────────┘  └─────────────────────────────────┘
   cross-link: cognee node-id as a TEXT column on the SQL side (app-code join,
   never a cross-DB FK). Both DBs on ONE local Postgres 17.
```

</topology>

---

## Layer responsibilities

<layer id="channel">
**Channel / ingress** — surface the system and take input. Discord is *not* the
primary ingestion channel (status/queries/kickoffs/digest); the **Gateway API**
is. All ingress channels funnel through one `ingest_note` core. Channels hold no
state and run no LLM logic themselves. The outreach work surface is a **NocoDB
filtered view** over `aiadaptive_cos` (dedicated role, shared views disabled,
**Tailscale Serve**) — a read/write channel to operational SQL, never to the
graph. Per `PRD-b3-tunnel.md` A2, **Tailscale is the default for human surfaces;
Cloudflare fronts machine callers that cannot join the tailnet.** See
`35-outreach-crm.md` §9.
</layer>

<layer id="action">
**Action** — execute work: ingest into the graph, recall from it, run the
agents, drive external APIs (Buffer, Granola, WordPress leads). Scheduled work is
owned by one **scheduler daemon** reading `loops/` manifests (replacing per-job
plists). All own-agent LLM calls go through the cost helper; cognee's go through
the litellm callback. Nothing persists outside the two DBs.
</layer>

<layer id="memory">
**Memory** — two stores on one Postgres 17 (`30-memory-layer.md`):
`aiadaptive_cognee` (cognee GraphRAG — what the brain *knows*) and
`aiadaptive_cos` (operational SQL — what the brain is *doing*). The
entity↔operational boundary keeps knowledge in the graph and status/queues/
ledgers in SQL, cross-linked by node-id TEXT in app code.
</layer>

<layer id="telemetry">
**Telemetry** — labeling (contextvar + litellm callback, M1) writes one
`agent_runs` row per provider call (cognee's included); a **soft daily breaker**
(`assert_under_ceiling`) blocks the next invocation once over ceiling; monthly
`cli/reconcile` checks the ledger against provider bills. The old pre-flight
gates (G1/G2) and per-agent keys were removed with the pivot; a single
`anthropic-api-key`. G3 anomaly detection (Ted) remains planned. See `80-`.
</layer>

---

## Agent roster (status)

<agent_roster>

Ted Lasso-named. Reasoning agents call Anthropic/Gemini **through the cost helper
(`agent_run`)** — not cognee. Ingestion agents call `ingest_note` (cognee).

**Built:** Discord bot + cogs (capture, recall, outcome, approvals, system) ·
Briefing (morning digest) · **Gateway API** (B3 ingress) · **Granola poller**
(meeting channel) · scheduler daemon.

**Planned (inherit the graph model + native telemetry when built):**
- **Roy Kent** — inbound ICP qualifier (Claude Haiku); WordPress leads via the
  gateway `/webhook/leads`. Writes `prospects`, ICP signals, `task_candidates`.
- **Tartt** — content discovery (Gemini Flash, 5am); the one pipeline that keeps
  **Gemini** (news ingestion).
- **Nate Shelley** — weekly ICP-signal synthesis (Sonnet).
- **Keeley** — triage + draft + self-check in **one Sonnet call** (merged 2026-08-08; replaced Keeley Strategy, Keeley Content and Sam). Graph-grounded via the scoped retrieval wrapper. `self_check` renders on the approval card as reviewer context, not as a gate.
- **Keeley Distribution** — Buffer publishing (no LLM).
- **Higgins** — weekly KR dashboard (Sonnet).
- **Ted** — 6-hourly health + G3 anomaly detection (pure Python over `agent_runs`).
- **Trent Crimm** — watchlist monitor (`35-outreach-crm.md` §10): weekly
  classification of detected signals against each target's watch trigger (Haiku,
  `function_label='outreach_watch'`, the **only LLM in the outreach system**).
  The evidence poller that feeds it (careers pages, RSS) is pure Python and also
  maintains `outreach_evidence` first/last-seen dates.

</agent_roster>

---

## Primary data flows

<data_flows>

<flow id="DF1" name="Ingestion → knowledge graph (any channel)">
Discord `#capture`, Gateway `POST /ingest`, or the Granola poller →
`ingest_note(text, source_ref, source_type, dataset, label_*)` → message-hash
dedup (skip exact re-posts pre-LLM) → under `labeled(...)`, `cognee.add` +
`cognify` build the note into the graph (cognee extracts + resolves entities).
Spend lands in `agent_runs` via the M1 callback. `50-channel-layer.md`.
</flow>

<flow id="DF2" name="Recall → synthesized answer">
`/recall` (Discord) or `cli/recall` → `graph_recall.recall(query)` → cognee
`GRAPH_COMPLETION` (vector hint → graph traversal → answer). Returns an answer,
not a ranked list. `30-memory-layer.md`.
</flow>

<flow id="DF3" name="Inbound lead → qualification (planned)">
WordPress form → Gateway `POST /webhook/leads` (HMAC, ack-then-process) → Roy
Kent (Haiku) scores ICP fit → writes `prospects` + ICP signals + `task_candidates`
(SQL); pains also cognified. Never runs inside the request. `40-action-layer.md`.
High-fit leads also get an `outreach_targets` row (`trigger_kind='inbound_enquiry'`)
— but **inbound never runs the cold five-touch arc**; handling is deliberately
open in `36-inbound-leads.md`.
</flow>

<flow id="DF4" name="Content discovery → publication (planned)">
Tartt (5am, Gemini, batched) → `content_items` → **Keeley: triage + draft +
self-check in one Sonnet call** (graph-grounded) → `#approvals` → human ✅ →
Keeley Distribution
→ Buffer. `60-content-pipeline.md`.
</flow>

<flow id="DF5" name="Morning briefing">
Scheduler fires the `morning-briefing` loop (6am) → Briefing assembles graph +
operational state (bounded queries) → posts `#briefing`. `40-action-layer.md`.
</flow>

<flow id="DF6" name="Cold outreach engine (specified — 35-outreach-crm.md)">
Evidence poller (12h) → `outreach_evidence` first/last-seen → scoring (S1 derived
from `trigger_date`; S4/S5 human-judged) → **Task Tinder intake gate** (capacity
cap: 15 cold + 3 re-engagement) → Selector materialises five touches (zero LLM) →
05:45 packet assembly (**deterministic query — no LLM; no generated prose in the
outbound path**) → operator writes the observation, sends from own client, BCCs
`outreach+<token>@` → IMAP poller matches the token, logs `sent_at`/`sent_body` →
a reply **halts the sequence** → drain (requires `stalled_reason`) → watchlist →
Trent Crimm → re-engagement, bypassing the cold cap. Work surface is a NocoDB
view behind Cloudflare Access; every invariant is a DB constraint, not app code.
Map in `37-outreach-workflow.md`.
</flow>

</data_flows>

---

## Trust boundaries

<trust_boundaries>

<boundary id="B1" name="Ingest → memory (data, not instructions)">
Ingested content is **data, never instructions** — extraction/retrieval treat it
as inert. No ingested text may act as a command, an approval, or a playbook.
</boundary>

<boundary id="B2" name="Agent → outbound (approval gate)">
`#approvals` human gate: sending email, publishing, creating a document, or any
world-affecting act requires an explicit human ✅.
</boundary>

<boundary id="B3" name="Network exposure (authenticated tunnel)">
"From anywhere" is the **Gateway API** (`127.0.0.1:8788`, HMAC-signed requests)
fronted by a Cloudflare Tunnel. No inbound ports open; Postgres stays on the
local socket and is never internet-reachable.
</boundary>

<boundary id="B4" name="Control-plane provenance (git)">
Skills, loops, and playbooks reach the running system **only through git**
(authored in barry-admin → committed → pulled to barry-agent). Nothing is
authored at runtime; the graph never mints one.
</boundary>

<boundary id="TB-accounts" name="Admin → agent account (git-gate) + credentials">
Code is written in barry-admin, committed, pulled into barry-agent for execution
(macOS account separation; the agent account can't write back to the admin repo).
Runtime creds (`db-url`, `anthropic-api-key`, `gemini-api-key`,
`gateway-hmac-secret`, …) live only in barry-agent's Keychain. barry-admin uses a
local socket as superuser for migrations/admin queries.
</boundary>

</trust_boundaries>
