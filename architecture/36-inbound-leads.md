# Inbound Lead Handling

<doc:layer>implementation — proposed</doc:layer>
<doc:stability>OPEN — the design is deliberately unmade. Constraints are settled; the handling is not.</doc:stability>
<doc:version>0.1.1-open</doc:version>
<doc:depends_on>30-memory-layer.md, 35-outreach-crm.md, 40-action-layer.md</doc:depends_on>
<doc:referenced_by>35-outreach-crm.md, 70-build-order.md, 90-workflows.md</doc:referenced_by>

## Status

**OPEN.** This spec exists because merging the Outreach Engine with the WordPress
Lead Engine surfaced a genuine gap: **the Selector has no row for someone who just
filled in your form.** The constraint is settled — inbound does not run the cold
arc. What replaces it is not decided, and this file deliberately does not invent
an answer.

Read §1 and §2 as binding. Read §4 as options, not a design.

---

## 1. What is already settled

<settled>

<rule id="I1" name="Inbound never runs the cold arc">
`trigger_kind = 'inbound_enquiry'` targets **never materialise the five-touch
sequence.** The intake handler in `35-outreach-crm.md` §10 refuses. This is
enforced, not advisory.
</rule>

<rule id="I2" name="Inbound still gets the full record">
An inbound lead gets an `outreach_targets` row, is scored on the rubric, accrues
history through `outreach_events`, and lands on the watchlist if it goes cold.
Background, history, and rubric all apply. **Only the sequence differs.**
</rule>

<rule id="I3" name="Two scores, both retained">
`prospects.icp_fit_score` (Roy Kent, 0..1) — *is this the right kind of company.*
`outreach_targets` score (0..25) — *is this the right moment.* An inbound lead has
both. They are not merged and neither is derived from the other.
</rule>

<rule id="I4" name="A later trigger converts an inbound lead to a cold target">
If an inbound lead goes cold and subsequently throws a genuine trigger — a raise,
a departure, a stalled req — `trigger_date` resets, `trigger_kind` changes away
from `inbound_enquiry`, and the target becomes eligible for the cold arc as a
normal target. This is the second-raise mechanic working as designed and requires
no special handling.
</rule>

<rule id="I5" name="Speed is the binding constraint">
Inbound interest decays in **days**, not the 90 days the cold arc assumes.
Whatever design is chosen, its latency target is measured in hours.
</rule>

<rule id="I6" name="Inbound evidence is first-party, and that changes the calculus">
Added 0.1.1, following the generation removal in `35-` v0.3.0.

The cold arc's packet assembles **observed** evidence — scraped job postings,
careers pages, funding announcements — which carries provenance, freshness, and
residual injection concerns (`35-` §3, §11). An inbound lead's scorecard answers
are **first-party**: the prospect told you, in their own words, in a form you
control.

That material is stronger than anything the cold packet can assemble, and it has
none of the same problems — no staleness tier, no source-trust question, no
untrusted-display risk. Whatever design is chosen, **the prospect's own words are
the personalisation**, and the system's job is to surface them verbatim beside the
template, not to characterise them.

Consequence: an inbound packet, if one is built, is a *simpler* artifact than the
cold packet, not a variant of it.
</rule>

</settled>

---

## 2. Why the cold arc is wrong here — the reasoning, so it is not relitigated

<divergence_reasoning>

Three independent reasons, any one of which is sufficient:

**T1 contains no ask by design.** Its defining property — the thing that makes it
touch one rather than a cold pitch — is that it asks for nothing. "Be a known name
in sixty days." Someone who submitted a scorecard has already asked. Sending them
a no-ask recognition email is not restraint, it is non-responsiveness.

**The arc's timeline is wrong by an order of magnitude.** Days 1–7, 7–14, 14–30,
30–45, 60–90. An inbound enquiry answered on day 3 has already been answered late.

**The arc's competitive premise does not hold.** The cold arc positions against a
full-time hire that has not landed, and against a founder who believes they can
cover the gap one more quarter. An inbound enquiry means the prospect is already
looking, already aware the gap exists, and quite possibly already talking to
someone else. The message that works is not the message that works cold.

</divergence_reasoning>

> **Corollary worth stating.** The source method is a *cold* outreach system. It is
> excellent at its job and says nothing about inbound, because Hire A Fractional's
> workbook is not an inbound playbook. Extending the Selector with an "inbound row"
> would be inventing method and attributing it to a source that does not support
> it. That is why this is a separate spec.

---

## 3. What exists today

<current_state>

| Component | State | Reference |
|-----------|-------|-----------|
| WordPress Lead Engine forms — scorecard, contact, newsletter | Built (external) | — |
| `POST /webhook/leads` — HMAC, ack-then-process | Planned | `20-architecture-overview.md` DF3 |
| Roy Kent — inbound ICP qualifier (Haiku), writes `prospects`, ICP signals, `task_candidates` | Planned, Phase 4 | `40-action-layer.md` |
| `prospects` table with `icp_fit_score`, `fit_reasoning`, status machine | Built (migration 0001) | `30-memory-layer.md` |
| `outreach_targets` row creation for high-fit leads | **Specified, not built** | `35-outreach-crm.md` §4 D2 |
| **What happens next** | **Undesigned — this document** | — |

Note that `prospects.status` already has a state machine — `new`, `qualified`,
`contacted`, `discovery_booked`, `in_engagement`, `declined`, `cold` — that
predates the Outreach Engine work and may or may not be the right spine for
inbound handling. Reconciling it with `outreach_targets.status` is an open
question (§5, Q4).

</current_state>

---

## 4. Options — not a design

<options>

Presented as a decision surface. None is recommended here; the trade-offs are
stated so the choice can be made on evidence rather than by default.

<option id="A" name="Straight to reactive">

Treat an inbound enquiry as a reply that arrived before the outreach did. The lead
lands directly in the reactive layer: a Task Tinder card offering **T43** (book the
call — two specific times, no scheduling link) or **T44** (answer and advance —
answer their question fully, then ask for fifteen minutes).

- **Latency:** same business day.
- **Effort:** near-zero. The reactive card already exists for replies (`35-` §6);
  this reuses it with a different entry point.
- **Cost:** no LLM beyond Roy Kent's existing scoring, unless a draft is assembled.
- **Weakness:** no nurture path for a lead that is genuinely early. A newsletter
  signup is not a discovery-call request, and treating it as one will burn the
  contact.
- **Best when:** the form is high-intent — scorecard completion, contact form.

</option>

<option id="B" name="Intent-tiered — the form decides the path">

`prospects.source_form` already distinguishes `scorecard`, `contact`, and
`newsletter`. Route on it:

| Form | Intent | Path |
|------|--------|------|
| `contact` | High — they asked to talk | Option A, same day |
| `scorecard` | Medium — they self-diagnosed | Deliver the scorecard result, then one follow-up tied to *their own answers*, then reactive |
| `newsletter` | Low — they want content | No outreach. Content pipeline only. Watch for a later trigger (I4). |

- **Latency:** tiered — hours for contact, days for scorecard, never for newsletter.
- **Effort:** moderate. Needs a short scorecard-specific sequence, which is new
  copy, not Selector copy.
- **Strength:** the scorecard path is the most valuable and most defensible — the
  prospect has told you their pain points in their own words, and the follow-up can
  quote them. Per **I6**, that is stronger personalisation than anything the cold
  arc assembles, and it carries none of the provenance or freshness burden.
- **Weakness:** you are now authoring a second template pack.

</option>

<option id="C" name="Warm short arc — three touches, two weeks">

A compressed, purpose-written sequence for inbound: acknowledge and deliver value
(day 0) → substantive follow-up on their stated pain (day 3) → direct ask (day 10).
Then reactive or watchlist.

- **Latency:** day 0.
- **Effort:** highest — new copy, a second sequencing path, a second set of windows.
- **Strength:** closes the "no nurture" gap in Option A without pretending the cold
  arc applies.
- **Weakness:** a second sequencing engine is exactly the kind of scope growth the
  capacity discipline exists to prevent. Also: **does inbound count against the
  12–15 cap?** If yes, inbound competes with cold outreach for slots. If no, the
  cap stops being a true capacity limit. Neither answer is obviously right.

</option>

<option id="D" name="No system — inbound is Tier 3 entirely">

Roy Kent scores and files. A high-fit lead raises one Task Tinder card. Everything
after is the operator's judgement with no scheduled follow-up at all.

- **Effort:** zero beyond what is already planned.
- **Strength:** honest. At current inbound volume, a system may be premature —
  and building sequencing for two leads a month is the tooling equivalent of buying
  a sequencing platform for fifteen prospects.
- **Weakness:** the loop does not close. No history, no follow-up tracking, no
  measurable conversion. Everything `35-` was built to prevent.
- **Best when:** inbound volume is genuinely low — and **it should be measured
  before anything else is built.**

</option>

</options>

---

## 5. Open questions — resolve before designing

<open_questions>

| # | Question | Why it decides the design | How to answer |
|---|----------|---------------------------|---------------|
| **Q1** | **What is actual inbound volume, by form?** | Below ~2/month, Option D is correct and everything else is premature tooling. Above ~10/month, Option B or C pays for itself. | Count the last 90 days of WordPress submissions before writing any code. **This is the first thing to do.** |
| **Q2** | Does inbound count against the 12–15 capacity cap? | If yes, inbound crowds out cold outreach and the cap becomes the rationing mechanism between two channels. If no, the cap stops measuring real capacity and the operator's attention is over-committed silently. | Depends on Q1. At low volume, exempt with a Ted alert. At high volume, it must count — and that is a signal to stop cold outreach, not to raise the cap. |
| **Q3** | Is the scorecard result itself the first touch? | If the WordPress plugin already emails the result, the system's "first touch" is second and must not repeat it. | Inspect the Lead Engine's existing email behaviour. |
| **Q4** | Does `prospects.status` or `outreach_targets.status` own the inbound state machine? | Two status machines on one relationship is AP5 — drift by construction. | Recommend: `prospects.status` owns the *person*, `outreach_targets.status` owns the *company pursuit*, and a lead with no company pursuit has no target row at all. Needs confirming against how the operator actually thinks about it. |
| **Q5** | What is the SLA, and what enforces it? | I5 makes latency the binding constraint, but nothing currently measures it. | A Ted alert on any `prospects` row `new` for >4 business hours would be the cheapest possible version. |
| **Q6** | Does an inbound lead ever get a packet (`35-` §7)? | The cold packet's value is dated evidence plus computed arithmetic. Neither applies: an inbound lead has no trigger-date arithmetic and no observed evidence — it has **I6 first-party answers**, which are better material and need no freshness handling. | A different, simpler artifact: their scorecard answers verbatim, the template, and the failure mode. No evidence table, no staleness tiers, no arithmetic block. |
| **Q7** | Does the evidence poller (`35-` §6) run on inbound targets at all? | Polling a company's careers page is how the cold arc scores S4/S5 and computes posting age. For an inbound lead none of that drives the immediate response — but it becomes relevant if they go cold and later throw a trigger (I4). | Likely yes, but at a lower cadence, and only after the lead goes cold. Polling every inbound signup from day one wastes budget on people who will book a call this week. |

</open_questions>

---

## 6. What is safe to build now

<safe_now>

Independent of every open question above:

1. **Count inbound volume by form for the last 90 days.** Q1 gates everything and
   costs an hour.
2. **Create the `outreach_targets` row on high-fit leads** (`35-` §4 D2) with
   `trigger_kind='inbound_enquiry'`. Correct under every option — it is the record,
   not the handling.
3. **Enforce I1** in the intake handler — refuse to materialise a five-touch
   sequence for `inbound_enquiry`. Correct under every option and prevents the
   specific wrong thing.
4. **Ted alert on `prospects.status = 'new'` older than 4 business hours.** Makes
   Q5 measurable before it is designed.
5. **Log the first human response time** so conversion can later be correlated
   with latency. Cheap now, unrecoverable later.

**Do not build:** any inbound sequence, any inbound template pack, any capacity
rule for inbound, until Q1 is answered.

</safe_now>

---

## 7. Recommendation on process, not design

<process_recommendation>

Answer Q1 first, then handle the next five inbound leads **entirely manually**,
logging what was actually sent and how fast. That produces the copy for whichever
option is chosen — and if the honest answer turns out to be Option D, five manual
leads will have demonstrated it at a cost of zero engineering.

The cold arc took a published, tested method and made it operational. There is no
equivalent method for this. Inventing one from first principles and building it in
the same pass would be the same mistake as buying a sequencing platform for fifteen
prospects: infrastructure ahead of evidence.

</process_recommendation>
