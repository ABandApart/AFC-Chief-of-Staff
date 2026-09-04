# Outreach CRM

<doc:layer>implementation</doc:layer>
<doc:stability>medium — edit as the outreach workflow matures</doc:stability>
<doc:version>0.3.0-draft</doc:version>
<doc:depends_on>10-strategy.md, 20-architecture-overview.md, 25-target-state.md, 30-memory-layer.md, 40-action-layer.md, 50-channel-layer.md</doc:depends_on>
<doc:referenced_by>36-inbound-leads.md, 37-outreach-workflow.md, 70-build-order.md, 80-telemetry-layer.md, 90-workflows.md</doc:referenced_by>

## Purpose

The Outreach Engine — trigger-driven qualification, five-touch sequencing, and
closed-loop completion tracking — implemented against the existing four-layer
architecture. Source material is the FractionalOS Outreach Engine workbook and
Selector (Hire A Fractional SEZC).

**For the workflow as a picture, read `37-outreach-workflow.md` first.**

<changelog>

**0.3.0 — the generation removal**

The observation sentence is **no longer generated.** The packet assembles typed,
sourced, dated evidence and the operator writes the sentence. Consequences:

- **Packet assembly contains no LLM call.** It is a deterministic query. It cannot
  fail from a provider outage, and it does not depend on cognee's completion path.
- Retrieval for the packet is a **scoped graph traversal from the target node**,
  not `GRAPH_COMPLETION`. No embedding query, no synthesis.
- **Prompt injection into outbound mail, and cross-client leakage via generated
  text, are eliminated by construction.** R2 retired.
- `read_at` gate **dropped**; the two-week observation trial **retired**; output
  screens for URLs and imperatives **retired**. `ready` survives unchanged (R1).
- **The observation moved from Tier 1 to Tier 3** (§12).
- **New dominant risk: staleness** (R19). Every displayed fact carries
  `observed_at`, and the packet marks anything over seven days.
- **New `outreach_evidence` table** (§2) — typed facts with first-seen/last-seen
  dates. This is the proprietary longitudinal data no provider sells (§6).
- **New §11 — ingest hardening.** The surviving controls, which apply to every
  channel rather than just outreach.
- Outreach LLM budget drops from $0.80/day to **$0.30/day** (Trent Crimm only).

**0.2.0** — capacity drain adopted · `#outreach-today` dropped, invariants moved
to database constraints · inbound moved to `36-` · BCC shown not to require B3 ·
departure detection flagged open.

</changelog>

> **One rule inherited from the source method.** Twelve to fifteen targets in
> genuine multi-touch flight at any one time. Capacity is the constraint every
> other element here exists to enforce, and §8 makes it structural.

---

## 1. Placement in the architecture

<placement>

Outreach is **operational state** — status machines, queues, cadence — so it lives
in `aiadaptive_cos` as plain SQL. What goes to the cognee graph is *background*:
what the company does, who works there, what was said. The operational row carries
the cognee node-id as a TEXT column and joins in app code — never a cross-DB FK.

| Concern | Home | Why |
|---------|------|-----|
| Target status, score inputs, touch schedule, completion | `aiadaptive_cos` SQL | Queried structurally. No retrieval benefit from vectors (P4). |
| **Typed, dated evidence about a target** | `aiadaptive_cos` — `outreach_evidence` | Needs `first_seen_at` ordering and exact recall, not fuzzy recall. |
| Company background, people, what was said in a meeting | `aiadaptive_cognee` graph | Semantic recall, human-read. |
| Scoring rubric, as prose | `playbooks/outreach-scoring.md` (`publish_to_memory: true`) | Control plane, git-trusted (B4). |
| Selector grid and template index | `config/outreach/` in git | Deterministic lookup — code-versioned config, not a DB table. |
| S1 recency bands, as executable truth | `migrations/` — `outreach_s1()` | **Authoritative.** See R6. |

> **Why evidence is SQL and not graph.** The packet needs *"the VP Revenue req,
> first seen 10 June, still open as of today"* — an exact, dated, ordered fact.
> That is a relational query. The graph holds what a meeting transcript said about
> the company; the evidence table holds what was observed and when.

</placement>

<score_separation>

**Two scores, never merged.** `prospects.icp_fit_score` (Roy Kent, 0..1) answers
*is this the right kind of company* and is stable. `outreach_targets` score (0..25)
answers *is this the right moment* and decays. The source deck: "Fit is not a
stable property of the company. It's a moment. The same company is a 2 in March
and a 9 in June."

</score_separation>

---

## 2. Schema

<schema>

House convention — `TEXT` with an enumerating comment. Migration numbering
continues from 0006.

> **Enforcement note.** With `#outreach-today` dropped (§9), no bot mediates
> writes. NocoDB writes raw UPDATEs; the Shortcut writes through a thin endpoint.
> **Every invariant is therefore a database constraint or a trigger, not
> application logic.**

### Targets

```sql
CREATE TABLE outreach_targets (
    id                      BIGSERIAL PRIMARY KEY,
    company_name            TEXT NOT NULL,
    company_domain          TEXT NOT NULL,   -- normalized; THE IMPORT DEDUP KEY (§5)
    company_url             TEXT,
    careers_url             TEXT,            -- polled by the evidence loop (§6)
    sector                  TEXT,

    stage                   TEXT,            -- 'seed'|'series_a'|'series_b_plus'|'mature'
                                             -- NULLABLE since 0014: an unknown stage
                                             -- scores as absent, never as wrong.
                                             -- Required before in_sequence (seq_ck).
    function_state          TEXT,            -- 'self_covered'|'under_led'|'vacant_seat'
                                             -- NULL until the two-tab diagnostic is done

    contact_name            TEXT,
    contact_role            TEXT,
    contact_email           TEXT,
    contact_linkedin_url    TEXT,

    trigger_kind            TEXT NOT NULL,   -- the eight triggers, plus 'inbound_enquiry'
                                             -- (enumerated below — CHECK-pinned in 0014)
    trigger_date            DATE NOT NULL,   -- the arc anchors HERE
    trigger_source_url      TEXT,

    -- Scoring. S1 is DERIVED (§4) and deliberately absent as a column.
    s2_stage_fit            SMALLINT,
    s3_sector_match         SMALLINT,
    s4_leadership_gap       SMALLINT,        -- human-observed, informed by evidence
    s5_team_build_below     SMALLINT,        -- human-observed, informed by evidence
    signals_observed_at     DATE,

    status                  TEXT NOT NULL DEFAULT 'candidate',
                                             -- 'candidate'
                                             -- 'in_sequence'   · COUNTS AGAINST CAP
                                             -- 'conversation'  · COUNTS
                                             -- 'call_booked'   · COUNTS
                                             -- 'engaged' | 'watchlist' | 'dropped'
                                             -- 'lost_to_hire' | 'archived'

    is_reengagement         BOOLEAN NOT NULL DEFAULT false,
    prospect_id             BIGINT REFERENCES prospects(id),   -- inbound — see 36-
    cognee_node_id          TEXT,            -- traversal root for background (§7)

    sequence_started_at     DATE,
    sequence_completed_at   DATE,
    stalled_reason          TEXT,
    watch_trigger           TEXT,
    watch_until             DATE,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT outreach_targets_stalled_ck
        CHECK (status <> 'watchlist' OR stalled_reason IS NOT NULL),
    CONSTRAINT outreach_targets_watch_ck
        CHECK (status NOT IN ('watchlist','lost_to_hire') OR watch_until IS NOT NULL),
    CONSTRAINT outreach_targets_scores_ck
        CHECK (COALESCE(s2_stage_fit,1) IN (1,3,5)
           AND COALESCE(s3_sector_match,1) IN (1,3,5)
           AND COALESCE(s4_leadership_gap,1) IN (1,3,5)
           AND COALESCE(s5_team_build_below,1) IN (1,3,5)),
    CONSTRAINT outreach_targets_seq_ck
        CHECK (status <> 'in_sequence' OR (function_state IS NOT NULL
                                           AND sequence_started_at IS NOT NULL))
);

CREATE UNIQUE INDEX outreach_targets_domain_idx ON outreach_targets (company_domain);
CREATE INDEX outreach_targets_status_idx        ON outreach_targets (status, trigger_date DESC);
CREATE INDEX outreach_targets_watch_idx         ON outreach_targets (watch_until)
    WHERE status IN ('watchlist','lost_to_hire');
```

### The eight triggers

<triggers>

**Added 2026-08-13 (operator).** Versions up to 0.3.0 referred to "the eight
triggers" in three places — here, §10, and `40-action-layer.md`'s Trent Crimm
spec — and enumerated them nowhere. Unconstrained, the vocabulary drifted on
first contact with real data (`request_open_past_45_days` in the seeded set vs
`req_open_45d` in an example). They are now CHECK-pinned by migration 0014, and
this table is the source both the constraint and `cli/outreach_import.py` follow.

| `trigger_kind` | Notes |
|----------------|-------|
| `executive_departure` | The highest-converting trigger in the method; detection remains open (OQ1) |
| `request_open_past_45_days` | Feeds S4's top band and T10's posting-date mechanic — the evidence poller's whole purpose |
| `new_executive_hire` | |
| `second_raise` | The second-raise mechanic (`36-` I4) |
| `funding_announced` | |
| `restructuring_or_layoffs` | |
| `market_or_region_expansion` | |
| `product_launch` | |
| `inbound_enquiry` | **Not a cold trigger.** Roy Kent's hand-off (§5 D2); never materialises the arc (`36-` I1), and the CSV importer deliberately cannot mint it. |

</triggers>

### Evidence — the new core table

```sql
-- outreach_evidence: typed, sourced, DATED facts. One row per observed fact,
-- updated in place as polling confirms it. first_seen_at is the datum the whole
-- method depends on and the one no provider reliably sells (§6).
CREATE TABLE outreach_evidence (
    id              BIGSERIAL PRIMARY KEY,
    target_id       BIGINT NOT NULL REFERENCES outreach_targets(id) ON DELETE CASCADE,

    fact_kind       TEXT NOT NULL,   -- 'open_role'          feeds S4, T10, the arithmetic
                                     -- 'leadership_member'  feeds function_state, S4
                                     -- 'ic_hire'            feeds S5, the compound signal
                                     -- 'funding_round'      feeds trigger_date, T2, T12
                                     -- 'stated_use_of_funds' feeds T12, T21
                                     -- 'expansion' | 'departure' | 'headcount'
    payload         JSONB NOT NULL,  -- typed per fact_kind. SHORT BOUNDED FIELDS ONLY (§11)

    source_kind     TEXT NOT NULL,   -- 'careers_page'|'theirstack'|'crunchbase'|'apollo'
                                     -- |'rss'|'manual'|'granola'
    source_url      TEXT,
    source_excerpt  TEXT,            -- verbatim, ≤500 chars, for provenance display

    first_seen_at   DATE NOT NULL,   -- PROPRIETARY — only longitudinal observation creates it
    last_seen_at    DATE NOT NULL,   -- refreshed every confirming poll
    closed_at       DATE,            -- when it stopped appearing
    dedup_key       TEXT NOT NULL,   -- stable identity across polls

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (target_id, fact_kind, dedup_key),
    CONSTRAINT outreach_evidence_dates_ck CHECK (last_seen_at >= first_seen_at),
    CONSTRAINT outreach_evidence_excerpt_ck CHECK (length(source_excerpt) <= 500)
);

CREATE INDEX outreach_evidence_target_idx ON outreach_evidence (target_id, fact_kind, last_seen_at DESC);
CREATE INDEX outreach_evidence_open_idx   ON outreach_evidence (target_id, first_seen_at)
    WHERE closed_at IS NULL;
```

**Poll semantics — this is what generates the proprietary data.** Each poll upserts
on `(target_id, fact_kind, dedup_key)`: a new key sets `first_seen_at = today`; an
existing key advances `last_seen_at`; a key absent from this poll but previously
open sets `closed_at = today`. After two weeks of polling you hold posting-age data
that cannot be bought retroactively.

### Packets

```sql
-- outreach_packets: the assembled work payload. NO GENERATED CONTENT (v0.3.0).
CREATE TABLE outreach_packets (
    id                  BIGSERIAL PRIMARY KEY,
    touch_id            BIGINT NOT NULL REFERENCES outreach_touches(id) ON DELETE CASCADE,
    assembled_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    subject_line        TEXT NOT NULL,
    body_filled         TEXT NOT NULL,    -- template with `auto` placeholders substituted;
                                          -- `observed` and `operator` slots left OPEN
    evidence_ids        BIGINT[] NOT NULL,-- the typed facts shown, in display order
    arithmetic          JSONB NOT NULL,   -- precomputed: posting age, search-window math
    staleness_days      INTEGER,          -- age of the OLDEST displayed fact (R19)
    unresolved_slots    TEXT[],           -- `operator` placeholders awaiting the human
    failure_mode        TEXT NOT NULL,    -- verbatim from the template index
    ready               BOOLEAN NOT NULL DEFAULT false
);
```

Removed in 0.3.0: `observation`, `observation_sources`, `read_at`.

### Touches, watch signals, history

Unchanged from 0.2.0 — `outreach_touches` (five per target, `bcc_token`, send/skip/
reply state, all CHECK constraints), `outreach_watch_signals`, and `outreach_events`
with the `outreach_log_event()` AFTER trigger on targets and touches.

**Add the audit trigger to `outreach_evidence` as well**, so a corrected fact is
traceable:

```sql
CREATE TRIGGER outreach_evidence_audit
    AFTER INSERT OR UPDATE OR DELETE ON outreach_evidence
    FOR EACH ROW EXECUTE FUNCTION outreach_log_event();
```

</schema>

---

## 3. Staleness

<staleness>

**With no generation, freshness is the dominant correctness attribute.** A req that
closed three weeks ago, displayed as "open 56 days," produces an email that is
specific, confident, and wrong — worse than an odd sentence, because the recipient
can check it in one click.

T10's own copy hedges this: *"Posted about [X] weeks ago, if I am reading the date
right."* The author knew.

```sql
CREATE VIEW v_outreach_evidence_display AS
SELECT e.*,
       CURRENT_DATE - e.last_seen_at        AS days_since_confirmed,
       CURRENT_DATE - e.first_seen_at       AS age_days,
       CASE WHEN e.closed_at IS NOT NULL              THEN 'closed'
            WHEN CURRENT_DATE - e.last_seen_at > 14   THEN 'stale'
            WHEN CURRENT_DATE - e.last_seen_at > 7    THEN 'ageing'
            ELSE 'fresh' END                AS freshness
FROM outreach_evidence e;
```

**Display rules, enforced in the packet:**

| Freshness | Rendering |
|-----------|-----------|
| `fresh` (≤7 days) | Normal, with "confirmed 4 Aug" |
| `ageing` (8–14) | Amber, "last confirmed 12 days ago — verify before citing" |
| `stale` (>14) | Struck through; **excluded from `arithmetic`** |
| `closed` | Shown only as history — "req closed 22 Jul" — never as a live signal |

**A packet whose only `open_role` evidence is `stale` or `closed` sets
`ready = false`.** Sending T10's posting-date mechanic on unconfirmed data is the
specific failure this prevents.

**Ted alerts** when the evidence loop has not run in 48h, or when more than 25% of
displayed facts across live targets are `ageing` or worse.

</staleness>

---

## 4. Scoring

<scoring>

Unchanged from 0.2.0. S1 is a `STABLE` function, not a generated column — Postgres
requires generated-column expressions to be IMMUTABLE and S1 depends on
`CURRENT_DATE`.

```sql
-- Bands are NON-MONOTONIC by design: 5 → 3 → 5 → 1. The second 5 is the day-60
-- hinge — the touch-five window, the highest-converting moment in the method.
CREATE FUNCTION outreach_s1(p_trigger_date DATE, p_asof DATE DEFAULT CURRENT_DATE)
RETURNS SMALLINT LANGUAGE sql STABLE AS $$
  SELECT (CASE
    WHEN p_asof - p_trigger_date <= 14             THEN 5
    WHEN p_asof - p_trigger_date BETWEEN 58 AND 68 THEN 5   -- THE HINGE
    WHEN p_asof - p_trigger_date > 90              THEN 1
    ELSE 3
  END)::SMALLINT;
$$;
```

Interpolations stated: days 15–29 and 69–90 are unspecified in the workbook and
score 3. The hinge is widened from "crossing day 60 now" to 58–68 so a weekly sweep
cannot step over it.

`v_outreach_scored` exposes `s1_trigger_recency`, `score`, `treatment`,
`compound_signal`, `signals_stale`, and `days_since_trigger`. Score is NULL unless
all of S2–S5 are set.

| Signal | Source | Refresh |
|--------|--------|---------|
| S1 | **Derived** from `trigger_date` | Continuous |
| S2, S3 | Stored at intake | Rarely |
| S4, S5 | **Human-judged, evidence-informed** | 30-day cadence → Task Tinder card |

**Evidence informs S4/S5; it does not set them.** `outreach_evidence` can tell you
three AEs were hired and no VP Revenue appears in the leadership list. Whether that
constitutes a leadership gap is the two-tab diagnostic, and the method is explicit
that it is five minutes of human judgement. The packet shows the evidence beside
the current score so the re-check is cheap.

</scoring>

---

## 5. Intake

<intake>

**D1 — CSV/XLS import.** UPSERT on `company_domain`, never a blind insert.
`trigger_kind`/`trigger_date` overwrite only if the incoming trigger is more
recent. `s2`–`s5`, `function_state`, `status`, `stalled_reason` are **never**
overwritten by import — human observation outranks a spreadsheet.

**D2 — Website lead magnet.** `POST /webhook/leads` → Roy Kent → `prospects`; if
`icp_fit_score >= 0.7`, also an `outreach_targets` row with
`trigger_kind='inbound_enquiry'`. **Inbound does not run the cold arc — see
`36-inbound-leads.md`.**

**D3 — Manual.** NocoDB grid or a slash command.

**The intake gate — Task Tinder.** Any target reaching `treatment='work'` posts one
card:

```
Outreach candidate — score 23/25   ⚡ COMPOUND SIGNAL

Cadence Health · Series A · Vacant seat
Trigger: Req open past 45 days (first seen 10 Jun · 56 days · confirmed today)
Contact: Marcus Oyelaran, Founder
Evidence: VP Revenue req open 56d · 3 AE hires since May · no VP on leadership list

Capacity: 13 of 15 cold live

[ ✅ Work this ]  [ 👁 Watchlist ]  [ ❌ Drop ]
```

`✅ Work this` sets `status='in_sequence'`, stamps `sequence_started_at`, and
materialises five `outreach_touches` from the Selector — a deterministic
`(stage, function_state, slot) → template_code` lookup. **Zero LLM calls.**

| Slot | Name | window_opens | due_date | window_closes |
|------|------|--------------|----------|---------------|
| 1 | Recognition | trigger + 0 | trigger + 3 | trigger + 7 |
| 2 | Relevance | trigger + 7 | trigger + 10 | trigger + 14 |
| 3 | Proof | trigger + 14 | trigger + 21 | trigger + 30 |
| 4 | The bridge | trigger + 30 | trigger + 37 | trigger + 45 |
| 5 | Close the loop | trigger + 60 | trigger + 75 | trigger + 90 |

Slots whose window already closed at admission are created
`skipped_at = now(), skip_reason = 'admitted_after_window'` so the completion
metric is not poisoned by a decision never offered.

</intake>

---

## 6. Evidence acquisition — bought, observed, judged

<acquisition>

Three kinds of data, and only one is buyable.

| Kind | What | Source | Cost |
|------|------|--------|------|
| **Bought** | Company name, domain, sector, headcount, funding round/date/amount/lead investor, contact name/title/email/LinkedIn | Apollo, Crunchbase, or any enrichment API | Low |
| **Observed** | **When a req first appeared. When it closed. When IC hiring started.** | **Your own polling** | Free, but only accrues forward |
| **Judged** | Function state — self-covered / under-led / vacant seat | You, five minutes, two tabs | Unbuyable |

**On Apollo specifically.** Its enrichment API returns industry, revenue estimates,
employee count, location, funding data, and contacts — **job postings, open roles,
and executive changes are not among the returned fields.** Apollo solves the header
of the packet and none of the signal. Worth having for contacts and firmographics;
it will not tell you a req has been open 56 days.

**The load-bearing datum is `first_seen_at`,** and it is what most providers do not
give you. T10's entire mechanic is the posting date; S4's top band is "posted 45+
days." A snapshot API tells you a req exists, not when it appeared.

**Two providers do carry first-seen:**

- **TheirStack** — 86% of new postings discovered same-day, 98% within 48h,
  closures detected with exact dates. **Free tier; paid from ~$49/mo.** The
  proportionate choice at this volume.
- **PredictLeads** — 270M historical job records since 2016 across ~2.7M companies,
  refreshed every 36h, explicit first-seen/last-seen. A separate News Events product
  is the closest legitimate thing to the OQ1 departure signal. Pricing is
  demo-gated, which usually means team-priced.

**The narrow buy case.** Trent Crimm's careers-page poller already generates
`first_seen_at` as a byproduct — free, and yours. The only genuine gap is **at
import**, when a company enters the list and you do not know how long its req has
been open. So: **buy backfill at import, observe thereafter.** Subscribing to a data
platform to watch thirty companies is the same error as buying a sequencing tool for
fifteen prospects.

**Recommended stack:** Crunchbase for funding · Apollo (or a cheaper enrichment API)
for contacts and firmographics · TheirStack at import for posting backfill · your
own poller for everything ongoing · your eyes for S4/S5.

<open_risk>

**Check before building the integration, not after.** Enrichment providers commonly
restrict caching and retention, and some prohibit storing data in a permanent
system of record. You are writing into a permanent brain with **no deletion
workflow and no RLS**. If any contact is EU-resident, personal data in a permanent
store with no erasure path is a real exposure — unglamorous, and much cheaper to
design for now than to retrofit. Tracked as R21.

</open_risk>

</acquisition>

---

## 7. The work packet — assembled, not generated

<packet>

**Zero LLM calls. A deterministic query.** It cannot fail from a provider outage and
does not depend on cognee's completion path.

| Element | Source |
|---------|--------|
| Header — company, contact, stage/state, score, days since trigger, slot N of 5 | `v_outreach_scored` |
| Subject line | Template, `auto` substitutions |
| Body, pre-filled | `config/outreach/templates/<code>.md`, `auto` slots resolved |
| **Typed evidence, with dates and provenance** | `v_outreach_evidence_display`, scoped to this target |
| **The arithmetic, already done** | `arithmetic` JSONB, computed from `first_seen_at` |
| **Background** | Scoped graph traversal from `cognee_node_id` — **read-only, human-read** |
| Failure mode | Template index, verbatim |
| Last touch sent, verbatim | `outreach_touches.sent_body` (BCC-captured) |
| The BCC address for this touch | `bcc+<bcc_token>@aiadaptive.co` (dedicated mailbox, §8) |

### Substitution classes

- **`auto`** — `[Company Name]`, `[First Name]`, `[Role Title]`, `[function]`,
  `[month]`, `[X] weeks`. Resolved from columns and dates.
- **`observed`** — `[specific observation]`. **You write this.** The packet supplies
  the evidence; the sentence is yours (Tier 3).
- **`operator`** — `[specific outcome with a number]`, `[Client 1]`,
  `[Portfolio Company]`, `[range]`. From your own book, ungeneratable. Fabricating
  them is the documented failure mode of T13 and T15.

> **Guard: `ready = false` blocks send-marking on every path** — Shortcut endpoint,
> NocoDB (trigger-enforced), and the BCC matcher. Set false when any `operator`
> slot is unresolved, or when the packet's only `open_role` evidence is stale or
> closed (§3). Unchanged from 0.2.0; still the cheapest catastrophic-failure guard
> in the design.

### The arithmetic

The highest-value element, and now purely computed. At slot 4 on a vacant-seat
target:

> *Req first seen 10 Jun — 56 days, confirmed open today. Searches at this level run
> 90–120 days, then ramp. Revenue is effectively unled through Q3, the quarter the
> raise was meant to accelerate.*

Constants live in `config/outreach/arithmetic.yaml`. The method's guidance is that
doing the arithmetic out loud converts better than asserting urgency, because the
reader finishes the calculation themselves.

### Background retrieval — traversal, not completion

`graph_recall`'s `GRAPH_COMPLETION` path is **not used here.** The packet runs a
bounded traversal from `outreach_targets.cognee_node_id` — N hops, scoped to the
permitted datasets (§11) — and displays the retrieved nodes as read-only context
with their source refs.

No embedding query, no synthesis, no LLM. This is simultaneously the cheaper design
and the one where cross-client leakage is structurally impossible.

### Layout is now a usability problem

With no generated summary, the packet is evidence. **If it reads as a wall of
excerpts it will stop being opened.** Ordering, from the top: the arithmetic · the
two or three facts that drive this template · everything else collapsed · the
failure mode · the BCC address. Target: scannable in thirty seconds.

</packet>

---

## 8. Closing the loop · capacity

<loop_and_capacity>

Both unchanged from 0.2.0; restated in brief.

**BCC to brain.** Each touch carries `bcc+<bcc_token>@aiadaptive.co`. A
scheduled IMAP poller matches the token from `Delivered-To` to exactly one touch
and writes `sent_at`, `sent_body`, `sent_via='bcc'`. Token-exact, because
heuristic matching corrupts touch-of-first-reply silently.

> **The BCC target is a DEDICATED, SEPARATE mailbox — not plus-addressing the
> sending account (corrected 2026-08-28; address `bcc@aiadaptive.co` provisioned by
> the operator 2026-08-29).** `bcc@aiadaptive.co` is its own Google Workspace user,
> distinct from the sending identity `barry@aiadaptive.co`; the `<bcc_token>` rides
> as a plus-address ON that dedicated mailbox (`bcc+<token>@…`), so token-exact
> `Delivered-To` matching is unchanged. This resolves `§16` #2 in favour of a dedicated mailbox and supersedes
> the earlier `outreach+<token>@aiadaptive.co` reading. **Why dedicated, not
> plus-addressing barry@:** the Gmail channel (`PRD-outreach-gmail-channel.md` G2)
> reads mail with `gmail.metadata` and deliberately cannot read the sending
> mailbox's bodies — plus-addressing barry@ would land every BCC body in exactly
> the mailbox G2 refuses to read. A separate account has no blast radius into client
> mail, which is the property being protected. The poller reads this one mailbox in
> full over IMAP; that is where `sent_body` comes from (Gmail metadata scope cannot).

**It is a pull channel: it needs neither B3 nor the full Track C email channel.**
Prerequisites are the dedicated Workspace mailbox above, IMAP credentials in
Keychain, and a poller — about a day. Verify on day one that the token survives in
`Delivered-To` before writing any code; the fallback is matching the stored
`subject_line`.

**Fallbacks:** Apple Shortcut (phone, LinkedIn, post-call) · NocoDB (desktop, bulk
correction, skip-with-reason). LinkedIn sends can never be BCC-captured —
permanent, not transitional.

**Reply halts the sequence.** `status='conversation'`; the generator excludes
anything not `in_sequence`. Prevents the day-62 breakup email to a prospect whose
call was booked on day 32. `reply_kind` is recorded at **touch grain**.

**Capacity.** `cold_live` ceiling 15, enforced at intake. The drain —
all five touches resolved, no reply, 14 days past the last window → `watchlist` —
cannot complete without `stalled_reason` (CHECK-enforced), so an unanswered card
costs a capacity slot. Deliberate friction.

**E1 — re-engagement allowance of 3, running as an experiment.** Hypothesis:
re-engagements convert above cold, and 3 concurrent absorbs detected triggers
without starving cold outreach. Review at 10 completed re-engagements or 2
quarters. **Falsified if conversion is not materially above cold** — or, more
importantly, **if the allowance is never hit**, meaning detection (§10) is the
binding constraint and attention belongs there instead.

</loop_and_capacity>

---

## 9. Surfaces

<surfaces>

| Surface | Role |
|---------|------|
| Task Tinder | **Decisions.** Intake · reactive · re-engagement · stale-signal re-check · stalled-reason prompt |
| **`#outreach`** | **Daily contact worklist** (revived 2026-09-03 — see the amendment). One card per due touch: read the packet, **Contact** (mark working today), or **Defer** (snooze + required note). Two actions, narrow by design. |
| Morning briefing | **One line and a link.** "5 touches due · 13/15 live · 1 not ready · 2 facts ageing"; the link points at `#outreach` |
| NocoDB filtered view | **The work surface.** Read packets, write the observation, mark sent, log replies, skip with reason, import, correct. *(Correcting CONTACT fields has an interim Discord path until NocoDB lands — `PRD-outreach-company-profile.md` R0.22. Deliberately narrow so it does not become a competing editor.)* |
| Calendar | **Dumb reminder.** Five dates at sequence start, explicitly non-authoritative |
| Apple Shortcut | **Fast write from a phone** (requires B3) |

> **AMENDED 2026-09-03 — `#outreach-today` un-dropped as `#outreach`
> (`PRD-outreach-daily-surface.md`).** The earlier decision below dropped a
> Discord daily surface in favour of NocoDB plus a briefing link. It is revived
> as a **narrow** surface: it shows only today's due touches (no worklist
> backfill, no decision cards — those stay in Task Tinder) and offers only
> **Contact** (an advisory `marked_working_at` intent flag; it never sends) and
> **Defer** (snooze + a required `snooze_note`). There is **no Skip** — a touch
> whose window closes unsent drains. Crucially the invariants stay where they
> are: every action writes columns NocoDB already writes, subject to the same
> constraints, so this is a view over the invariants, not a re-home of them. It
> is not a second editor of target/contact fields — NocoDB stays the editor of
> record.

The original decision, retained for the record: *`#outreach-today` is dropped.
Invariants it would have enforced are database constraints:
skip-requires-reason · watchlist-requires-stalled-reason · sent-XOR-skipped ·
reply-implies-send · snooze-cannot-cross-windows · not-ready-cannot-be-sent.*
Those constraints are unchanged; the revived surface relies on them rather than
replacing them.

**Snooze** never shifts other slots — the arc anchors on `trigger_date` — and may
not exceed `window_closes`. **On-schedule** is
`sent_at::date BETWEEN window_opens AND window_closes`, not `= due_date`.

</surfaces>

---

## 10. Watchlist monitoring — `Trent Crimm`

<watch_agent>

Weekly Sunday 19:00, ahead of Nate Shelley. Inputs: targets on `watchlist` or
`lost_to_hire` with `watch_until >= CURRENT_DATE`. Outputs:
`outreach_watch_signals`, Task Tinder cards on matches, archival on expiry.

**This is now the only LLM in the outreach system** — one Haiku call per *detected
item* to classify whether an excerpt constitutes one of the eight triggers.
Forced tool call, `function_label='outreach_watch'`, ceiling $0.30/day. Only items
matching `watch_trigger` or classified `executive_departure` surface as cards.

Detection sources, ranked: careers-page/job-board polling (best automatable yield —
a reopened req at a company that hired instead of engaging is a strong departure
proxy, and it doubles as the `outreach_evidence` feed) · funding and press RSS ·
Google Alerts per company · quarterly manual sweep.

`watch_until` defaults to `sequence_completed_at + 18 months`; on expiry, `archived`
and polling stops.

<open_question id="OQ1" name="Departure detection">

**FLAGGED OPEN — not designed, not scheduled.** Executive departure is the
highest-converting trigger in the method and there is **no good automated path.**

LinkedIn offers no API for profile changes. **Scraping is off the table** — it
violates their terms and risks the account the entire outreach motion depends on.
Sales Navigator job-change alerts, forwarded to the BCC mailbox and parsed, are the
only legitimate automated path known; it is an unmade paid-subscription decision
whose alert format and forwardability are all unverified. PredictLeads' News Events
product (§6) is a second candidate worth pricing at the same time.

Until resolved, watchlist monitoring runs on the careers-page proxy, RSS, and a
quarterly sweep. **That is a working system, not a blocked one** — but materially
weaker than the method assumes, and the gap should be named rather than papered
over.

</open_question>

</watch_agent>

---

## 11. Ingest hardening

<ingest_hardening>

**These are the controls that survived the generation removal.** None is specific
to outreach; all apply to every channel — `#capture`, Granola, the evidence
pollers, and the Track C email and Drive channels when they land. They belong at
shared ingest.

<control id="H1" name="Typed DataPoints, short bounded fields">
Pollers and enrichment adapters write **typed fields**, never raw page dumps.
`source_excerpt` is capped at 500 characters by constraint. A paragraph-long
instruction payload has nowhere to live, and this is now the primary defence
against hostile scraped content — there is no model to instruct, but there is a
human who will read and copy.
</control>

<control id="H2" name="Unicode normalization and invisible-character stripping">
At ingest, before storage: NFKC normalize; strip zero-width characters, bidi
overrides, and the U+E0000 tag block; flag homoglyph-heavy strings. Log anything
removed.

**Rationale changed in 0.3.0.** This is no longer about steering a model. You are
copying text into an email to a founder. A bidi override makes displayed text
differ from what gets pasted; zero-width characters survive copy-paste; a homoglyph
domain in a link is a live phishing vector. Deterministic, fast, universal.
</control>

<control id="H3" name="Datasets as an authorization boundary">
Extend the existing `capture` / `playbooks` split with `outreach_public` for
scraped company and job content, and keep client work in its own dataset. The
outreach traversal (§7) is scoped to permitted datasets and **cannot read client
datasets at all.** Cross-client leakage becomes structurally impossible rather than
statistically unlikely — and this holds for human display just as it did for
generation.
</control>

<control id="H4" name="Scoped traversal, never free-form completion">
Packet background is a bounded traversal from a known node id. No semantic search
over the whole graph. Cheaper, deterministic, and it removes the retrieval path
that could surface unrelated material.
</control>

<control id="H5" name="Input screening for the LLM calls that remain">
Outreach no longer generates, but Trent Crimm, Tartt, Roy Kent, and Keeley all
still consume scraped content. Screen retrieved chunks before they enter any prompt:
instruction-like patterns (`ignore`, `instead`, `you must`, `system:`), base64
blobs, abnormal instruction-to-content ratio. Quarantine and log.

**Moved from outreach to shared ingest**, which is where it belonged.
</control>

<control id="H6" name="Provenance retention">
Every evidence row keeps `source_url`, `source_excerpt`, `source_kind`,
`first_seen_at`, `last_seen_at`. Serves freshness (§3), the "was this right"
question, and — because the graph changes under you — the only way to reconstruct
what a packet actually showed on a given morning.
</control>

<control id="H7" name="Screen failures decay source trust">
A source whose content repeatedly trips H2 or H5 is low-quality or hostile. Decay
`sources.trust_score` automatically — the column and the mechanism already exist
for Tartt.
</control>

**Retired in 0.3.0**, all moot without generation: typed-observation template
rendering · grounding substring checks · entity allowlists on output · the honeypot
node · the observation red-team pass · output screens for URLs and imperatives ·
the `read_at` gate · the two-week observation trial.

> These controls generalise beyond outreach. If the Track C channels land as
> planned, promote H1–H7 into their own file and reference it from `50-`.

</ingest_hardening>

---

## 12. Risk register

<risks>

| ID | Risk | Severity | Mitigation | Status |
|----|------|----------|------------|--------|
| **R1** | Unresolved `operator` placeholder reaches a prospect — literal `[Client 1]` in a founder's inbox | **High** | `ready=false` blocks send-marking on every path (§7). Unaffected by the generation removal. | Designed |
| **R2** | ~~Prompt injection via the observation~~ | — | **RETIRED 0.3.0.** No generation, no prompt, no path. Replaced by R20. | **Retired** |
| **R19** | **New, and now dominant.** Stale evidence produces a specific, confident, checkable falsehood — "open 56 days" on a req that closed three weeks ago | **High** | Freshness tiers and display rules (§3) · stale evidence excluded from `arithmetic` · `ready=false` when the driving fact is stale · Ted alert on a silent evidence loop | Designed |
| **R20** | **New, replaces R2 at much lower severity.** Injection now targets the *human*: crafted text in a job posting, displayed verbatim, copied into an email | **Low** | H1 typed short fields (no room for a payload) · H2 unicode stripping · provenance and source URL always shown · no free-text blobs rendered | Designed |
| **R21** | **New.** Enrichment-provider retention terms, plus personal data in a permanent store with no erasure path or RLS | Medium | Check provider terms **before** building the integration; decide a deletion workflow for contact data (§6) | **Open** |
| R4 | NocoDB shared views unauthenticated by default; shared-view password additionally weak (CVE-2026-47379, fixed 2026.5.1) | **High** | Cloudflare Access **at the hostname**, not a path · shared views disabled in config · version floor 2026.5.1 · no Access bypass rules | **Open — verify at install** |
| R5 | NocoDB writes to derived columns | Medium | S1/score/treatment exposed only through `v_outreach_scored`; no UPDATE granted | Designed |
| R6 | Rubric drift between `outreach_s1()` and playbook prose | Medium | **The migration is authoritative**; the playbook does not restate the bands | Designed |
| R7 | Capacity gate deadlocks with no drain | High | Drain rule, §8 | Resolved |
| R8 | Duplicate targets from CSV re-import inflate the live count | Medium | Upsert on `company_domain` | Designed |
| R9 | Sequence continues after a reply | High | Status halt + generator filter | Designed |
| R10 | Touch-of-first-reply corrupted by loose logging | Medium | Token-exact matching · reply at touch grain · `sent_via` provenance | Designed |
| R11 | Calendar events stale after a snooze (AP5) | Low | Link + minimal context, non-authoritative, rewritten nightly | Designed |
| R12 | Shortcut HMAC secret on the phone | Medium | Separate `gateway-hmac-shortcut`, write-only on touch logging | Designed |
| R13 | ~~Orphaned Discord message~~ | — | Retired with `#outreach-today` | Retired |
| R14 | Watch loop scrapes LinkedIn | **High** | **No scraper is built.** See OQ1. | **Policy** |
| R15 | Timezone skew | Low | Session TZ pinned; dates are DATE | Designed |
| R16 | ~~Observation spend scales with pipeline width~~ | — | **Retired** — no observation call | Retired |
| R17 | Malformed NocoDB edit corrupts state silently | Medium | Every invariant a CHECK or trigger; `outreach_events` with actor attribution | Designed |
| R18 | IMAP poller double-logs a send | Low | `bcc_token` UNIQUE; matcher idempotent; second match is a no-op logged to `#system` | Designed |

</risks>

---

## 13. Tiers and trust boundaries

<tiers>

| Activity | Tier | Change in 0.3.0 |
|----------|------|-----------------|
| CSV import, dedup, upsert | 1 | |
| Evidence polling, first/last-seen maintenance | 1 | **New** |
| Enrichment API calls | 1 | **New** |
| S1 recomputation, band-change events | 1 | |
| Sequence materialisation from the Selector | 1 | |
| **Packet assembly** | 1 | **No LLM. Deterministic query.** |
| BCC matching and send logging | 1 | |
| Watch-signal detection and classification | 1 | The only remaining LLM |
| Drain rule evaluation | 1 | |
| **Intake decision** | **2** | |
| **Re-engagement decision** | **2** | |
| **Reactive routing on reply** | **2** | |
| **Stalled-reason capture** | **2** | |
| **Function state — the two-tab diagnostic** | **3** | |
| **Writing the observation sentence** | **3** | **Moved from Tier 1** |
| **Sending the message** | **3** | |

</tiers>

<b2_rule>

> **Anything addressed to the operator is exempt from the `#approvals` gate.
> Anything addressed to a third party is not.**

**Exempt:** packets, the NocoDB view, calendar events on your own calendar, the
briefing line, self-addressed digests, Drive documents in your own folder.
**Gated:** any message whose recipient is not you.

**Consequence for v1:** you write the observation and send every message
personally, so **outreach never crosses B2.** Written in terms of *recipient*, not
channel or content type, so it stays decidable when a new channel lands.

</b2_rule>

---

## 14. Loops, playbooks, config

<control_plane>

```
loops/
├── outreach-daily.md      05:45 — assemble packets (no LLM), briefing line,
│                          calendar refresh, drain rule
├── outreach-evidence.md   every 12h — poll careers pages, upsert evidence,
│                          maintain first/last-seen, close disappeared facts
├── outreach-bcc.md        every 15 min — IMAP poll, token match, log sends
├── outreach-rescore.md    Sunday 18:00 — band-change record, stale-signal
│                          cards. NOT "recompute S1" (corrected 2026-08-17):
│                          S1 is evaluated live in v_outreach_scored, so
│                          nothing is stored to recompute. Full spec, with
│                          four open decisions: loops/outreach-rescore.md
└── outreach-watch.md      Sunday 19:00 — Trent Crimm

playbooks/
├── outreach-scoring.md          publish_to_memory: true
├── outreach-function-state.md   publish_to_memory: true
└── outreach-objections.md       publish_to_memory: true

config/outreach/
├── selector.yaml          the 9-row grid + swap-in rules
├── templates/<code>.md    body + placeholder partition (auto/observed/operator)
├── arithmetic.yaml        search-duration constants
└── evidence-sources.yaml  per-provider field mapping into fact_kind + payload
```

`outreach-daily` runs at 05:45, before the 06:00 briefing, so the briefing can
carry counts and the link. **Regenerate, never edit** — packets are rebuilt each
morning from current state.

</control_plane>

<telemetry>

- `function_label='outreach_watch'` — Trent Crimm classification. **Ceiling
  $0.30/day.** (`function_label='outreach'` is retired along with the observation
  call.)
- `function_label='outreach_discovery'` — **added 2026-08-20.** Bounded entity
  extraction turning news items and award-list entries into candidate company
  names (`PRD-outreach-company-profile.md` R0.21). Ceiling **$0.25/day**,
  `agent_name='outreach-discover'`. **This retires the claim that Trent Crimm is
  "the only outreach LLM spend"** — it was true when written and is not now.
  Total outreach LLM budget is **$0.55/day**, still below the $0.80 of 0.2.0.
  The call names a company and guesses a domain; it decides no segment, no fit,
  and writes no prose. A hallucinated firm fails verification (R0.5) and never
  reaches the operator.
- **Ted alerts:** `cold_live > 15` or `reengagement_live > 3` · touch past
  `window_closes` unsent and unskipped · packets `ready=false` >48h · drain blocked
  on a missing `stalled_reason` >7 days · **evidence loop silent >48h** · **>25% of
  displayed facts `ageing` or worse** · BCC poller silent >2h · watch loop silent
  >8 days · **re-score sweep silent >8 days** (added 2026-08-20 — the list covered
  every other loop and not this one; a weekly loop that silently stops is the
  08-15 failure mode, invisible because nothing surfaces it).
- **Higgins weekly:** touches on schedule (window-based) · touch-five completion,
  tracked separately · new targets scoring 20+ (target 3–5) · conversations opened ·
  calls held · cold live vs cap · E1 allowance usage · **evidence freshness
  distribution**.
- **Higgins quarterly, gated at 40 completed sequences:** conversation rate · call
  rate · engagement rate · watchlist conversion · **touch-of-first-reply
  distribution** · cold vs re-engagement conversion (E1).

</telemetry>

---

## 15. Build order

<build_order>

| Step | Scope | Effort | Depends on |
|------|-------|--------|-----------|
| 1 | **Header test** — one manual BCC; confirm the token survives | 10 min | — |
| 2 | Migration 0007 — tables incl. `outreach_evidence`, constraints, `outreach_s1()`, views, audit triggers | 2 days | — |
| 3 | `config/outreach/` — Selector, templates, placeholder partition, arithmetic constants | 1 day | — |
| 4 | NocoDB — dedicated role, **shared views off**, **≥2026.5.1**, Access at hostname (R4) | 1 day | 2 |
| 5 | CSV import with domain upsert | 0.5 day | 2, 4 |
| 6 | **Evidence poller** — careers pages, upsert, first/last-seen, close-detection | 1.5 days | 2 |
| 7 | **Ingest hardening H1–H2** — typed writes, unicode stripping | 0.5 day | 6 |
| 8 | Intake card + sequence materialisation + capacity gate + drain + stalled-reason card | 1.5 days | 2, 3 |
| 9 | **Packet assembly** — evidence query, arithmetic, scoped traversal, `ready` guard, freshness tiers | **1 day** (was 2 — no LLM) | 3, 6 |
| 10 | Briefing line + link; calendar write-out | 0.5 day | 8, 9 |
| 11 | **BCC mailbox + IMAP poller + token matcher** | 1 day | 1, 2 |
| 12 | Reply logging + reactive card | 0.5 day | 8 |
| 13 | B3 tunnel | 2–3 days | — (parallel) |
| 14 | Apple Shortcut endpoints + scoped secret | 0.5 day | 13 |
| 15 | Weekly re-score sweep + band events + stale-signal cards | 0.5 day | 2 |
| 16 | Enrichment adapter — contacts/firmographics, TheirStack backfill at import | 1 day | 6 |
| 17 | Trent Crimm + H3–H7 hardening | 2.5 days | 2, 6, 15 |
| | **Closed loop with evidence and BCC (1–12)** | **~11 days** | |
| | **Full system (1–17)** | **~17 days** | |

**Step 6 should start as early as possible** regardless of everything else:
`first_seen_at` only accrues forward. Two weeks of polling before you send anything
is two weeks of posting-age data you cannot buy retroactively.

`36-inbound-leads.md` is sequenced separately and gated on its own decisions.

</build_order>

---

## 16. Open decisions

<open_decisions>

| # | Decision | Blocking |
|---|----------|----------|
| 1 | **Departure detection** — Sales Navigator or PredictLeads News Events trial, or accept the quarterly sweep permanently? See **OQ1**. | No — careers-page proxy ships regardless |
| 2 | ~~**BCC mailbox provider**~~ **RESOLVED (2026-08-28):** a dedicated Google Workspace user, separate from the sending identity, token via plus-addressing on it — provisioned as `bcc@aiadaptive.co` (2026-08-29). See §8 and `PRD-outreach-gmail-channel.md` §3. | Settled |
| 3 | **Enrichment stack** — Apollo vs a cheaper contacts API; TheirStack free tier vs paid for import backfill | Step 16 |
| 4 | **R21** — provider retention terms and a deletion workflow for contact data | Step 16 |
| 5 | **E1 re-engagement allowance of 3** — running with stated falsification conditions | No |
| 6 | **Inbound handling** — see `36-inbound-leads.md` | That spec |

</open_decisions>
