# Outreach Workflow — the map

<doc:layer>bridge — the workflow as a picture</doc:layer>
<doc:stability>medium — regenerate when 35- changes materially</doc:stability>
<doc:version>0.2.0</doc:version>
<doc:depends_on>35-outreach-crm.md, 36-inbound-leads.md</doc:depends_on>
<doc:referenced_by>90-workflows.md</doc:referenced_by>

## Purpose

`35-outreach-crm.md` is the specification and it is dense. This file is the map.
**Read this first, then go to 35- for the detail.**

Nothing here is new. If this document and `35-` disagree, `35-` is correct.

<changelog>

**0.2.0** — reflects the generation removal. **D6 (the injection surface) is
deleted** — there is no generated text in the system's outbound path, so the
diagram has no subject. It is replaced by **D6: the evidence lifecycle**, which is
where the risk actually moved: freshness. D3 loses the model call. D5 gains the
evidence table. Gate 2 changes from *read the packet* to *write the observation*.

</changelog>

<legend>

| Shape / mark | Meaning |
|--------------|---------|
| Rectangle | Automated step — Tier 1, no human |
| Diamond | Branch evaluated by the system |
| Rounded, thick border | **Human decision — Tier 2 or 3** |
| Cylinder | Persisted state |

Four human gates exist in the whole system. Everything else runs unattended.
**One LLM call remains** in outreach — Trent Crimm's trigger classification — and
it never touches outbound text.

</legend>

---

## 1. End to end

<diagram id="D1" name="End to end">

```mermaid
flowchart TD
    CSV["CSV / XLS import"] --> UPSERT
    MAN["Manual entry in NocoDB"] --> UPSERT
    ENR["Enrichment API<br/>contacts · firmographics · funding"] --> UPSERT
    WEB["Website lead magnet"] --> ROY["Roy Kent<br/>ICP fit score 0-1"]
    ROY --> INB{"inbound?"}
    INB -->|"yes"| SEP(["Inbound path<br/>see 36-inbound-leads"])
    INB -->|"no"| UPSERT

    UPSERT["Upsert on company_domain<br/>never a blind insert"] --> SCORE
    POLL["Evidence poller · every 12h<br/>first_seen · last_seen · closed"] --> EV[("outreach_evidence<br/>typed · sourced · dated")]
    EV --> SCORE
    SCORE["Score S1 to S5<br/>S1 derived · S4 and S5 human-judged"] --> BAND{"treatment"}

    BAND -->|"below 14"| DROP(["Drop — delete the row"])
    BAND -->|"14 to 19"| WATCH
    BAND -->|"20 to 25"| GATE1

    GATE1{{"GATE 1 · Task Tinder<br/>Work this company?"}}:::human --> CAP{"cold_live < 15?"}
    CAP -->|"no"| REQ(["Re-queue + alert to system"])
    CAP -->|"yes"| SEQ

    SEQ["Materialise 5 touches<br/>from the Selector · zero LLM"] --> CAL["Write 5 calendar events<br/>non-authoritative"]
    SEQ --> DAILY

    DAILY["05:45 outreach-daily<br/>assemble packets · NO LLM"] --> READY{"packet ready?"}
    READY -->|"no · operator slot unfilled<br/>or driving fact stale"| BLOCK(["Blocked — cannot be marked sent"])
    READY -->|"yes"| BRIEF["06:00 briefing<br/>one line and a link"]

    EV --> DAILY

    BRIEF --> GATE2{{"GATE 2 · Write the observation<br/>from the evidence, then send<br/>BCC outreach+token"}}:::human

    GATE2 --> IMAP["IMAP poller · every 15 min<br/>match Delivered-To token"]
    IMAP --> LOG[("sent_at · sent_body<br/>sent_via = bcc")]

    LOG --> REPLY{"reply logged?"}
    REPLY -->|"yes"| HALT["status = conversation<br/>SEQUENCE HALTS"]
    HALT --> GATE3{{"GATE 3 · Task Tinder<br/>T43 book · T44 advance · objection"}}:::human
    GATE3 --> ENG(["Engaged"])

    REPLY -->|"no · slots remain"| DAILY
    REPLY -->|"no · all 5 resolved"| DRAIN

    DRAIN["Drain · 14 days past last window"] --> STALL{"stalled_reason set?"}
    STALL -->|"no · slot stays occupied"| ASKW{{"Task Tinder<br/>What stalled it?"}}:::human
    ASKW --> STALL
    STALL -->|"yes"| WATCH

    WATCH[("Watchlist<br/>watch_until = +18 months")] --> TRENT["Trent Crimm · weekly<br/>the only LLM in outreach"]
    TRENT --> SIG{"trigger matched?"}
    SIG -->|"no"| TRENT
    SIG -->|"watch_until passed"| ARCH(["Archived"])
    SIG -->|"yes"| GATE4{{"GATE 4 · Task Tinder<br/>Re-engage with T47?"}}:::human
    GATE4 -->|"yes · bypasses the cold cap"| SEQ

    classDef human fill:#eb6834,stroke:#7a2f10,stroke-width:3px,color:#ffffff
```

</diagram>

**The four gates, and nothing else.** Gate 1 admits a company. Gate 2 is writing the
observation and sending. Gate 3 routes a reply. Gate 4 re-engages from the
watchlist. Every other box runs without you.

**The evidence poller feeds two things** — the score, and the packet. It is the only
component that must start early, because `first_seen_at` accrues forward and cannot
be bought retroactively.

**Where it deadlocks if you remove a piece.** Take out the drain and the capacity
gate blocks all intake permanently. Take out `stalled_reason` enforcement and the
watchlist becomes a list of companies you cannot re-engage, because T49 needs to
know what stalled.

---

## 2. Target status — the state machine

<diagram id="D2" name="Status state machine">

```mermaid
stateDiagram-v2
    [*] --> candidate: imported, enriched, scored

    candidate --> in_sequence: Gate 1 · Work this · capacity permitting
    candidate --> watchlist: Gate 1 · Watchlist · score 14-19
    candidate --> [*]: Gate 1 · Drop · below 14

    in_sequence --> conversation: reply logged · SEQUENCE HALTS
    in_sequence --> watchlist: drain · all 5 resolved + 14 days
    in_sequence --> lost_to_hire: they hired someone

    conversation --> call_booked: call scheduled
    conversation --> in_sequence: false start · resume
    conversation --> watchlist: went cold

    call_booked --> engaged: signed
    call_booked --> watchlist: went cold

    watchlist --> in_sequence: Gate 4 · re-engagement
    lost_to_hire --> in_sequence: Gate 4 · departure trigger

    watchlist --> archived: watch_until passed
    lost_to_hire --> archived: watch_until passed

    engaged --> [*]
    archived --> [*]

    note right of in_sequence
        COUNTS AGAINST THE CAP:
        in_sequence · conversation · call_booked
        Cold ceiling 15 · re-engagement ceiling 3
    end note

    note right of watchlist
        stalled_reason is NOT NULL enforced.
        The drain cannot complete without it,
        so the slot stays occupied until
        the question is answered.
    end note
```

</diagram>

Three statuses consume capacity, not one. A booked call still occupies a slot,
because it still occupies your attention — which is what the cap actually meters.

---

## 3. One touch, closed

<diagram id="D3" name="Closing the loop on a single touch">

```mermaid
sequenceDiagram
    autonumber
    participant L as outreach-daily 05:45
    participant E as outreach_evidence
    participant G as cognee graph
    participant DB as aiadaptive_cos
    participant B as Briefing 06:00
    participant O as Operator
    participant P as Prospect
    participant I as IMAP poller

    L->>DB: read v_outreach_due
    L->>E: typed facts for this target<br/>with freshness tiers
    E-->>L: open_role first seen 10 Jun,<br/>confirmed today · 3 ic_hire
    L->>G: bounded traversal from cognee_node_id<br/>NO completion, NO embedding query
    G-->>L: background nodes, read-only
    L->>L: compute arithmetic from first_seen_at
    L->>DB: write outreach_packets · ready?
    L->>B: counts and link
    B->>O: "5 due · 13/15 live · 1 not ready · 2 facts ageing"

    O->>DB: open the NocoDB view
    DB-->>O: evidence with dates and sources,<br/>arithmetic, template, failure mode, BCC address
    Note over O: GATE 2 — you write the observation.<br/>Tier 3. No model involved.
    O->>P: send from own mail client
    O-->>I: BCC outreach+token@

    I->>I: poll every 15 min
    I->>DB: match Delivered-To token → exactly one touch
    DB->>DB: sent_at · sent_body · sent_via = bcc
    Note over DB: LOOP CLOSED

    P-->>O: reply
    O->>DB: log reply · NocoDB or Shortcut
    DB->>DB: status = conversation · sequence halts
    DB->>O: Gate 3 card
```

</diagram>

**No LLM appears in this sequence.** Packet assembly is a deterministic query, so it
cannot fail from a provider outage and does not depend on cognee's completion path.

**Matching is on the BCC token**, never a heuristic. Recipient plus subject plus
nearest-due-date mis-attributes on an early send or two contacts at one company, and
that corrupts touch-of-first-reply silently.

**The LinkedIn hole.** Steps 12–15 do not exist for a LinkedIn send. That path
closes through the Apple Shortcut or NocoDB, permanently, not transitionally.

---

## 4. The arc — five windows anchored on the trigger

<diagram id="D4" name="The five-touch arc">

```mermaid
flowchart LR
    T(["trigger_date<br/>day 0"]) --> S1
    S1["Slot 1 · Recognition<br/>days 0-7 · due 3<br/><b>no ask at all</b>"] --> S2
    S2["Slot 2 · Relevance<br/>days 7-14 · due 10<br/>tie to their use of funds"] --> S3
    S3["Slot 3 · Proof<br/>days 14-30 · due 21<br/>narrow proof, one number"] --> S4
    S4["Slot 4 · The bridge<br/>days 30-45 · due 37<br/><b>the only direct ask</b>"] --> S5
    S5["Slot 5 · Close the loop<br/>days 60-90 · due 75<br/><b>highest converting</b>"] --> D["Drain<br/>+14 days"]

    style S1 fill:#2a78d6,stroke:#123c6d,color:#ffffff
    style S2 fill:#2a78d6,stroke:#123c6d,color:#ffffff
    style S3 fill:#2a78d6,stroke:#123c6d,color:#ffffff
    style S4 fill:#eb6834,stroke:#7a2f10,color:#ffffff
    style S5 fill:#eb6834,stroke:#7a2f10,color:#ffffff
```

</diagram>

**Every window is measured from `trigger_date`, never from the previous send.**
That is why snoozing one touch never shifts the others, and why a snooze may not
cross into the next slot's window.

**S1 scoring rides this same axis and is non-monotonic:** 5 inside day 14, 3 through
the middle, **5 again across days 58–68** — the day-60 hinge, which is slot 5's
window — then 1 past day 90. A naive "older is worse" ladder deletes the
highest-converting moment in the method.

---

## 5. Where the data lives

<diagram id="D5" name="Storage split">

```mermaid
flowchart TB
    subgraph COS["aiadaptive_cos · operational SQL"]
        A[("outreach_targets<br/>status · score inputs · watch")]
        EV[("outreach_evidence<br/>typed · sourced · first/last seen")]
        B[("outreach_touches<br/>schedule · sent · reply")]
        C[("outreach_packets<br/>assembled · arithmetic · ready")]
        D[("outreach_events<br/>audit · DB TRIGGER, not app code")]
    end

    subgraph COG["aiadaptive_cognee · knowledge graph"]
        F(["Organization · Person · Fact<br/>Meeting · Decision"])
    end

    subgraph GIT["control plane · git, trusted"]
        H["config/outreach/selector.yaml<br/>templates + placeholder partition"]
        I["playbooks/outreach-scoring.md<br/>publish_to_memory: true"]
        J["migrations · outreach_s1<br/><b>AUTHORITATIVE for the bands</b>"]
    end

    POLL["Evidence poller<br/>careers pages · RSS · enrichment"] --> EV
    EV --> C
    A -.->|"cognee_node_id TEXT<br/>app-code join, never a FK"| F
    F -->|"bounded traversal<br/>NO completion"| C
    H --> B
    J --> A
    I -.->|"published to the trusted dataset"| F

    style COS fill:#e8f1fb,stroke:#2a78d6
    style COG fill:#e6f6f0,stroke:#1baf7a
    style GIT fill:#fdefe8,stroke:#eb6834
```

</diagram>

**Operational state is SQL. Knowledge is graph. Instructions are git.** The three
never share a store, and the graph never mints an instruction — that separation *is*
the security model (B1, B4).

**Why evidence is SQL and not graph.** The packet needs *"the VP Revenue req, first
seen 10 June, still open today"* — an exact, dated, ordered fact. That is a
relational query. The graph holds what a transcript said; the evidence table holds
what was observed and when.

**The S1 bands exist in the migration and in playbook prose. The migration wins.**

---

## 6. The evidence lifecycle — where the risk moved

<diagram id="D6" name="Evidence lifecycle and freshness">

```mermaid
flowchart LR
    P["Poll · every 12h"] --> K{"dedup_key seen<br/>before?"}
    K -->|"no"| NEW["INSERT<br/><b>first_seen_at = today</b>"]
    K -->|"yes, present"| UPD["UPDATE<br/>last_seen_at = today"]
    K -->|"yes, absent now"| CLO["UPDATE<br/>closed_at = today"]

    NEW --> F
    UPD --> F
    F{"freshness"} -->|"confirmed ≤7d"| FR["<b>fresh</b><br/>cite freely"]
    F -->|"8-14d"| AG["<b>ageing</b><br/>amber · verify before citing"]
    F -->|">14d"| ST["<b>stale</b><br/>struck through<br/>EXCLUDED from arithmetic"]
    CLO --> CL["<b>closed</b><br/>history only<br/>never a live signal"]

    ST --> BLK(["packet ready = false<br/>if this is the driving fact"])
    CL --> BLK

    style NEW fill:#1baf7a,stroke:#0d6647,color:#ffffff
    style FR fill:#1baf7a,stroke:#0d6647,color:#ffffff
    style AG fill:#eda100,stroke:#7a5400,color:#ffffff
    style ST fill:#e34948,stroke:#7d1f1f,color:#ffffff
    style CL fill:#e34948,stroke:#7d1f1f,color:#ffffff
```

</diagram>

**This diagram replaces the injection surface.** With no generated text in the
outbound path, prompt injection has no subject — and the risk that took its place is
**staleness**.

A req that closed three weeks ago, displayed as "open 56 days," produces an email
that is specific, confident, and wrong. Worse than an odd sentence, because the
recipient can check it in one click. T10's own copy hedges it — *"Posted about [X]
weeks ago, if I am reading the date right"* — because the author knew.

**`first_seen_at` is the proprietary datum.** No provider reliably sells "when did
this req first appear"; you create it by watching. Which is why the evidence poller
should start before anything else — two weeks of polling is two weeks of
posting-age data that cannot be bought retroactively.

**The residual injection risk is low but not zero.** Crafted text in a job posting,
displayed verbatim and copied by a human, still reaches a prospect. The controls are
typed short fields with a 500-character excerpt cap, unicode stripping at ingest,
and always showing the source URL and date — `35-` §11, H1 and H2.

---

## 7. What is deliberately not here

- **Generated prose.** Removed in `35-` v0.3.0. The system assembles evidence; you
  write the sentence. One LLM call remains in outreach — Trent Crimm's trigger
  classification — and it never touches outbound text.
- **Inbound handling** — `36-inbound-leads.md`. The constraint is settled (inbound
  never runs the cold arc); the design is open, gated on measuring actual volume.
- **Departure detection** — flagged open in `35-` OQ1. No legitimate automated path;
  the careers-page proxy ships regardless, and no scraper is built.
- **`#outreach-today`** — dropped. The briefing carries one line and a link; the
  NocoDB view is the work surface. Every invariant it would have enforced is now a
  database constraint.
