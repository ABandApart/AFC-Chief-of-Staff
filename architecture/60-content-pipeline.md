# Content Pipeline

<doc:layer>implementation</doc:layer>
<doc:stability>medium — edit as the pipeline matures</doc:stability>
<doc:depends_on>10-strategy.md, 20-architecture-overview.md, 30-memory-layer.md, 40-action-layer.md, 50-channel-layer.md</doc:depends_on>
<doc:referenced_by>70-build-order.md</doc:referenced_by>

## Purpose

This file is a deep-dive on the content discovery-to-publication value stream (VS1). It defines the state machine, the gemba points, Buffer integration mechanics, and the rate-limiting strategy.

---

## State Machine

<state_machine>

> **Simplified 2026-08-08.** Triage, drafting, and evaluation collapsed into one
> Sonnet call (agent `Keeley`, `40-action-layer.md`). Seven states became four;
> Sam is retired. Rationale below.

The `content_pipeline` table holds one row per content item that enters the
pipeline. The `stage` column is the state.

```
       Tartt discovery
              │
              ▼
       ┌────────────┐
       │ discovered │  (below threshold → stays here)
       └─────┬──────┘
             │ Keeley: triage + draft + self-check, ONE call
             ├─────────────────────────────► declined  (reason: not ICP fit)
             ▼
       ┌──────────────────┐
       │ pending_approval │   ◄── GEMBA: human decides via Discord
       └─────┬────────────┘        (self_check renders on the card as context)
             │
       ┌─────┴─────────────────────────────┐
       │                                   │
   ✅ approve                          ❌ reject
       │                                   │
       ▼                                   ▼
   ┌──────────┐                       ┌──────────┐
   │ approved │                       │ declined │ (terminal)
   └─────┬────┘                       └──────────┘
         │ Keeley Distribution invoked
         ▼
   ┌────────────┐
   │ scheduled  │   (Buffer accepted the post)
   └─────┬──────┘
         │ Buffer status poll
         ▼
   ┌────────────┐
   │ published  │   (terminal)
   └────────────┘
```

**Retired states:** `triaged`, `drafted`, `sam_passed`. **Retired mechanic:** the
max-2 re-draft loop — a self-check that fails now simply renders its objection on
the approval card, where the human was going to look anyway.

**Schema note.** Phase 8 has not been built, so this costs no migration:
`content_pipeline.sam_evaluation` is renamed `self_check` in the Phase-8
migration, and `triage_notes` is retained (Keeley returns a `rationale` that
belongs there).

</state_machine>

---

## Gemba Point

<gemba>

The pipeline has exactly one human gate: the `pending_approval` → `approved`/`declined` transition in Discord `#approvals`.

This is the only Tier 2 → Tier 3 boundary in this value stream. Everything upstream is Tier 1; everything downstream of approval is Tier 1.

**Why one gate, not multiple**: multiple gates fragment attention and create approval fatigue.

**Why no automated pre-gate either (2026-08-08)**: the previous design put Sam in front of this gate to ensure the human only saw drafts worth deciding on. At ~5 drafts/week that reasoning did not survive contact with the arithmetic — Sam's output was a pass/fail against a rubric that the operator re-derives seconds later, at a gate they must open regardless. The evaluation did not reduce the human's work; it duplicated it one step earlier. Keeley's `self_check` now renders *on* the approval card as reviewer context — the same information, at the moment it is actually used, for no extra call.

**Calibration signal**: the rejection rate at this gate. Above **30% over the first 20 drafts**, a separate evaluator earns its place back (`40-action-layer.md`, Keeley). Below it, the merged call stands.

</gemba>

---

## Buffer Integration

<buffer_integration>

### API Surface

Buffer's public API uses OAuth 2.0 access tokens; for a single-account integration, a personally-generated access token from the Buffer developer portal is sufficient. The endpoints used:

- `POST /1/updates/create` — schedule a new update
- `GET /1/profiles` — list connected channels (called once at setup; stored in config)
- `GET /1/updates/<id>` — check status of a scheduled post (used by webhook fallback)

### Rate Limiting

Buffer's documented limit: 60 requests per minute per access token.

Implementation: token-bucket limiter with a 50/min ceiling (10-request headroom).

<rate_limiter_pseudocode>

```python
class BufferRateLimiter:
    def __init__(self, max_per_minute=50):
        self.max = max_per_minute
        self.window_start = time.time()
        self.count = 0

    def acquire(self):
        now = time.time()
        if now - self.window_start >= 60:
            self.window_start = now
            self.count = 0
        if self.count >= self.max:
            sleep_for = 60 - (now - self.window_start)
            time.sleep(sleep_for)
            self.window_start = time.time()
            self.count = 0
        self.count += 1
```

</rate_limiter_pseudocode>

On a 429 response from Buffer despite the limiter, sleep 60 seconds and retry once. If the retry also fails, mark `buffer_posts.status = 'failed'` and emit a `#system` alert; do not retry further.

### Channel Routing

Single Buffer account. Multiple channels (LinkedIn, X, etc.) connected to that account.

Default channel chosen by content type:
- Long-form draft → LinkedIn
- Short-form (under 280 chars) → X
- Newsletter snippet → LinkedIn newsletter feature, or LinkedIn long-form

Override via `approval_queue.edit_notes` containing `channel: <name>` instructs Keeley Distribution to route differently.

Multi-channel posting (same draft to LinkedIn and X) is a v2 feature; v1 is single-channel per approval.

### Webhook for Publication Confirmation

Buffer doesn't push webhooks for individual post publication in all plans. The reliable pattern:

1. Keeley Distribution creates the post via API → receives Buffer post ID immediately, stores in `buffer_posts.buffer_id`
2. A polling job (`com.aiadaptive.buffer-status`, every 30 minutes) queries `GET /1/updates/<id>` for posts with status='scheduled' that have a `scheduled_for` in the past
3. When Buffer reports the post as published, the polling job transitions `content_pipeline.stage` to `published` and records `buffer_posts.posted_at`

This polling pattern is robust to webhook unavailability and easier to debug than webhook delivery failures.

</buffer_integration>

---

## Pipeline Latency Targets

<latency_targets>

These are targets, not guarantees. They inform agent priorities and alerting thresholds.

| Transition | Target latency | Why |
|------------|---------------|-----|
| discovered → pending_approval | < 1 hour | One Keeley call (triage + draft + self-check), fired after the Tartt batch completes, then straight to the approval queue |
| pending_approval → approved/declined | Operator-paced | Gemba point — no SLA |
| approved → scheduled | < 5 minutes | API call with rate-limit budget |
| scheduled → published | Buffer-paced | Whatever Buffer schedule says |

</latency_targets>

---

## Failure Modes

<failure_modes>

<failure id="F1" name="Keeley declines nearly everything">
**Symptom**: Items move `discovered → declined` at a high rate; the approval queue stays empty.
**Diagnosis**: the positioning/ICP `decisions` rows Keeley triages against are too narrow, or Tartt's threshold is admitting the wrong items.
**Response**: operator reviews recent `declined_reason` + `rationale` values. Adjust the `decisions` rows (or Tartt's interest threshold) — **not** a rubric in code, since triage now reasons from the stored positions. Re-run declined items.

> The old failure — rows piling up at `drafted` because Sam rejected everything — is structurally gone: there is no intermediate state to pile up in, and no automated gate to be miscalibrated. The pipeline's failure mode moved from *stalling* to *declining*, which is visible in the briefing rather than silent in a table.
</failure>

<failure id="F2" name="Buffer API outage">
**Symptom**: `buffer_posts.status = 'failed'` for new approvals; `#system` alerts.
**Diagnosis**: Buffer 5xx responses or connection errors.
**Response**: Approvals can still happen; Keeley Distribution holds approved items in a `scheduled_for` queue. Polling job retries when Buffer is reachable.
</failure>

<failure id="F3" name="Operator never approves">
**Symptom**: `pending_approval` queue grows; nothing publishes.
**Diagnosis**: Operator overload or vacation.
**Response**: Ted alerts after 7 days of growing queue. Items older than 14 days auto-decline with reason `stale`. (This prevents the pipeline from publishing weeks-old content if the operator returns and bulk-approves.)
</failure>

<failure id="F4" name="Tartt produces low-quality summaries">
**Symptom**: Keeley declines most items, or the drafts it does return are weak.
**Diagnosis**: Gemini Flash summarization is degrading, or source quality has shifted.
**Response**: Operator reviews recent content_items. May adjust Tartt's summarization prompt, lower interest threshold, or downgrade trust_score for problematic sources.
</failure>

<failure id="F5" name="Embedding model deprecated">
**Symptom**: the local FastEmbed model fails to load, or a deliberate embedder change is proposed.
**Diagnosis**: embeddings are **local bge-base-en-v1.5 @768 in-process** (2026-08-03) — there is no provider to deprecate them, so this is a code/model-load failure rather than an API event. The original provider-deprecation risk is retired.
**Response**: a load failure alerts `#system` and retries next run. A deliberate embedder change is a substrate event: **768 is a hard dimension commitment**, and switching means re-embedding the whole graph. Fallback on file is Voyage (`voyage/voyage-3.5`), a commented block in `cognee_setup.build_cognee_env`.
</failure>

</failure_modes>

---

## Engagement Feedback Loop (v2)

<engagement_loop>

In v1, the pipeline ends at `published`. No engagement metrics are pulled back.

V2 plan (defer to phase 4 in `70-build-order.md`):

1. Polling job pulls per-post engagement from Buffer API (likes, comments, reshares, click-throughs)
2. Engagement metrics written to `buffer_posts.engagement` (JSONB)
3. High-engagement posts trace back to source `content_items` and the `interest_signals` they were scored against
4. Successful signals get weight bumps; signals with consistently low engagement decay
5. Source `trust_score` also adjusts based on whether items from that source produced engaging posts

This closes the loop: the system learns what produces engagement and biases discovery accordingly.

**Why deferred**: V1 is about pipeline existence and operator workflow. Until you have weeks of published posts, there's no engagement data to learn from. Build the loop when there's something to feed it.

</engagement_loop>

---

## Shared Ingest Hardening and the Sources Table

<shared_hardening>

Two integration points added with the outreach CRM (`35-outreach-crm.md`, 2026-08-08):

1. **H1–H7 apply to this pipeline's ingest.** Tartt consumes scraped web content,
   and Keeley puts that content into an LLM prompt. The channel-agnostic ingest controls in `35-outreach-crm.md` §11 —
   typed short fields (H1), unicode/invisible-character stripping (H2), and
   **input screening before scraped chunks enter any prompt (H5)** — apply here
   exactly as they do to outreach. They land at shared ingest, not per-pipeline.

2. **`sources.trust_score` is now written by two consumers.** Tartt already
   multiplies interest scores by `trust_score`. H7 adds automatic decay: a source
   whose content repeatedly trips the H2/H5 screens is low-quality or hostile and
   loses trust without operator action. The outreach evidence poller and Trent
   Crimm reuse the same `sources` machinery (careers pages and company RSS as
   source rows), so trust semantics stay shared — one table, one meaning.

</shared_hardening>

---

## What This Pipeline Does NOT Do

<non_goals>

- **Cross-channel coordination**: No "post to LinkedIn then 3 hours later to X with adapted phrasing." V1 is one channel per approval.
- **Editorial calendar**: No "post on Tuesdays and Fridays." Posts schedule when approved; Buffer's queue dictates timing.
- **A/B testing**: no multiple drafts for the same source. Keeley returns one draft per item; the operator edits it at the gate or rejects it.
- **Newsletter assembly**: The Adaptive (Substack newsletter) is not in this pipeline. Newsletter drafting is a separate workflow that may borrow this pipeline's primitives in a future phase.
- **Direct social posting**: All posting goes through Buffer. No direct LinkedIn or X API integration.

</non_goals>
