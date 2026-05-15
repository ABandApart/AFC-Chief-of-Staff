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

The `content_pipeline` table holds one row per content item that enters the pipeline. The `stage` column is the state.

```
       Tartt discovery
              │
              ▼
       ┌────────────┐
       │ discovered │  (item below threshold or skipped → stays here forever)
       └─────┬──────┘
             │ Keeley Strategy invoked
             ├─────────────────────────────► declined  (reason: not ICP fit)
             ▼
       ┌──────────┐
       │ triaged  │
       └─────┬────┘
             │ Keeley Content drafts
             ▼
       ┌──────────┐
       │ drafted  │
       └─────┬────┘
             │ Sam evaluates
             ├─────────────────────────────► (back to triaged for re-draft, max 2 cycles)
             ▼
       ┌─────────────┐
       │ sam_passed  │
       └─────┬───────┘
             │ approval_queue post created
             ▼
       ┌──────────────────┐
       │ pending_approval │   ◄── GEMBA: human decides via Discord
       └─────┬────────────┘
             │
       ┌─────┴─────────────────────────────┐
       │                                   │
   ✅ approve                          ❌ reject
       │                                   │
       ▼                                   ▼
   ┌──────────┐                       ┌──────────┐
   │ approved │                       │ declined │ (terminal)
   └─────┬────┘
         │ Keeley Distribution invoked
         ▼
   ┌────────────┐
   │ scheduled  │   (Buffer accepted the post)
   └─────┬──────┘
         │ Buffer webhook on actual publication
         ▼
   ┌────────────┐
   │ published  │   (terminal, but engagement_measured later)
   └────────────┘
```

</state_machine>

---

## Gemba Point

<gemba>

The pipeline has exactly one human gate: the `pending_approval` → `approved`/`declined` transition in Discord `#approvals`.

This is the only Tier 2 → Tier 3 boundary in this value stream. Everything upstream is Tier 1; everything downstream of approval is Tier 1.

**Why one gate, not multiple**: Multiple gates fragment attention and create approval fatigue. Sam's evaluation is automated specifically so that the human gate only sees drafts worth deciding on. If Sam pass-rates are too high (everything reaches the human), tighten Sam's rubric. If Sam pass-rates are too low (nothing reaches the human), loosen the rubric or upgrade Sam from Haiku to Sonnet.

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
| discovered → triaged | < 1 hour | Keeley Strategy should run shortly after Tartt batch completes |
| triaged → drafted | < 30 minutes | Keeley Content is event-driven |
| drafted → sam_passed | < 5 minutes | Sam is quick |
| sam_passed → pending_approval | < 1 minute | Just a Discord post |
| pending_approval → approved/declined | Operator-paced | Gemba point — no SLA |
| approved → scheduled | < 5 minutes | API call with rate-limit budget |
| scheduled → published | Buffer-paced | Whatever Buffer schedule says |

</latency_targets>

---

## Failure Modes

<failure_modes>

<failure id="F1" name="Sam rejects every draft">
**Symptom**: Pipeline rows pile up at `drafted` stage with no progress.
**Diagnosis**: Sam's rubric is too strict.
**Response**: Operator reviews recent sam_evaluation JSON. Adjusts rubric in code (committed to repo). Pushes to Mac mini. Re-runs failed evals.
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
**Symptom**: Keeley Strategy declines most items; Keeley Content produces weak drafts.
**Diagnosis**: Gemini Flash summarization is degrading, or source quality has shifted.
**Response**: Operator reviews recent content_items. May adjust Tartt's summarization prompt, lower interest threshold, or downgrade trust_score for problematic sources.
</failure>

<failure id="F5" name="Embedding model deprecated">
**Symptom**: Gemini API returns errors for text-embedding-004; new content_items have NULL embeddings.
**Diagnosis**: Provider deprecated the model.
**Response**: This is a substrate-level event. Pick new embedding model. Re-embed all existing `content_items`, `facts`, `interest_signals`, `meeting_transcripts`. This is a multi-hour migration; plan accordingly.
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

## What This Pipeline Does NOT Do

<non_goals>

- **Cross-channel coordination**: No "post to LinkedIn then 3 hours later to X with adapted phrasing." V1 is one channel per approval.
- **Editorial calendar**: No "post on Tuesdays and Fridays." Posts schedule when approved; Buffer's queue dictates timing.
- **A/B testing**: No multiple drafts for the same source. Sam either passes one draft or sends back for revision.
- **Newsletter assembly**: The Adaptive (Substack newsletter) is not in this pipeline. Newsletter drafting is a separate workflow that may borrow this pipeline's primitives in a future phase.
- **Direct social posting**: All posting goes through Buffer. No direct LinkedIn or X API integration.

</non_goals>
