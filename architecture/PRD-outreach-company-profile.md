# Outreach Market Discovery & Company Profiling — PRD & Build Spec

<doc:meta>
  <doc:phase>Track O — Part 0 is new and is now the centre of gravity; Parts 1–3 are unchanged in purpose and renumbered nowhere; Part 4 reverses the 2026-08-17 deferral</doc:phase>
  <doc:theme>Cast a wide net, learn from the operator's own accept/reject decisions which industries, firms, and problem spaces convert, and profile the survivors</doc:theme>
  <doc:duration>Part 0 ~4–5 days · Part 1 ~2 days · Part 2 ~2–3 days · Part 3 gated, ~2 days once accounts exist · Part 4 ~2 days</doc:duration>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>rev 2.6, 2026-08-27 — nine of ten decisions settled (§0.9); OQ-G open, gates Part 4 only. **PART 0 IS BUILT** (§0.10): schema, ICP v1, the decision core, the workbook importer, verification, two sourcing channels including bounded entity extraction, the daily loop, the Gate 0 review sheet, the segment-rating affordance, and the briefing line. Parts 1-4 remain. Rev 1 (2026-08-17) specified profiling of an existing 14-target list. Rev 2 makes discovery the primary job on operator revision, adds the daily-20 review surface and its field contract, widens the segment taxonomy from three to six, and un-defers the ICP feedback loop.</doc:status>
  <doc:depends_on>`35-outreach-crm.md` §2 (schema), §3 (staleness), §5 (intake), §7 (packet), §8 (capacity), §9 (surfaces), §10 (Trent Crimm), §11 (H1–H7); migration 0013 (`outreach_evidence`, `outreach_watch_signals`); `agents/_lib/retrieval.py` `Scope.TARGET`; `agents/tartt/`; `agents/_lib/ontology.py`; the operator CRM workbook `Education_LD_Leads_CRM_(current).xlsx`</doc:depends_on>
  <doc:blocks>nothing today. Part 0 becomes the top of the funnel that `35-` §5 intake assumes already exists.</doc:blocks>
</doc:meta>

## 0. What changed in rev 2, and what it costs

Operator revisions, 2026-08-19, taken as given:

1. **Cast a wider net.** This is exploratory work whose purpose is market
   intelligence — which industries, firms, and problem spaces fit the offering and
   offer the best penetration — not enrichment of a fixed list.
2. **A daily surface of 20 organizations**, carrying 13 named background fields
   (§0.3), and those same fields are what the Task Tinder card shows.
3. **Three new segments**: engineering consultancies, product design agencies, and
   MSPs / IT consultancies, joining the workbook's existing three.
4. **A feedback loop**: as the operator accepts and rejects candidates, the search
   criteria refine.

**Three consequences that are not free, stated here rather than buried:**

- **Rev 1 §4 deferred the ICP feedback loop on 2026-08-17. Rev 2 reverses that
  deferral on 2026-08-19, by operator instruction.** Recorded per
  `70-build-order.md` §Working convention rather than quietly overwritten. The
  reversal is also defensible on the merits, which matters more than the
  instruction: §4's stated objection was that a fit model has nothing to learn from
  — *14 targets, 0 touches, 0 replies*. Revision 2 removes exactly that objection.
  Accept/reject at 20 per day produces on the order of **100 labelled decisions per
  week**, and those labels arrive months before the first reply does. The loop
  learns from **selection**, not from outcomes. What §4 said was premature was
  outcome-learning, and outcome-learning stays premature — see §4.6.
- **20 per day is a discovery rate, not an outreach rate.** `35-` §8 caps
  `cold_live` at **15 concurrent sequences**, enforced at intake, and
  `v_outreach_capacity` reads `0 / 15` today. Twenty Gate 1 cards a day would be
  refused by the capacity check within one day. So the daily 20 is a **new gate
  ahead of the existing one** (§0.4, R0.6), and accepting one means *add to the
  pool*, not *start sequencing*.
- **One requested field collides with a settled rule.** *Suggested Pain Point
  (Outreach Hook)* is generated prose, and `35-` §7 settles that the packet is
  **assembled, never generated**. The collision is real and resolvable, but it is
  the operator's call to make, not this spec's — see OQ-A.

## 0.10 Build status — Part 0, as of 2026-08-20

| Artifact | State |
|---|---|
| **Migration 0018** `outreach_discoveries` | **Applied.** Idempotent on re-run, owned by `barry_agent`, audit + `updated_at` triggers attached, passes `verify_schema.sql`. |
| `agents/outreach/icp.py` | **Built.** v1 reproduces the workbook's 4.6 / 4.5 / 3.0 exactly; `explain()` components sum to the score. |
| `agents/_lib/outreach_discovery.py` | **Built.** Window with the 25% exploration reserve (R0.17), decisions, dedup across both tables, promotion guard. |
| `cli/discovery_import.py` | **Built and run.** 49 US rows imported unreviewed. |
| `tests/test_outreach_discovery.py` | **Built.** 45 tests. Suite 592 → 627, ruff unchanged at the 6 known pre-existing errors. |
| `agents/outreach/discovery/` | **Built.** Registry + `seed_list` + `news_query`. |
| `agents/outreach/discovery/extract.py` | **Built** (R0.21). Bounded Haiku extraction, H5-screened, ceiling-metered. |
| `agents/outreach/verify.py` | **Built.** Four checks; never fetches LinkedIn. |
| `agents/outreach/discover.py` + `loops/outreach-discover.md` | **Built**, ships disabled — the seed list is empty and `news_query` costs money, so enabling is deliberate. |
| **Migration 0019** | **Applied.** `outreach_segment_scores` (R0.20) + `outreach_discoveries.source_url` (R0.21). |
| `agents/discord_bot/cogs/outreach_discovery.py` | **Built.** Components V2 sheet, 12 rows/message, Review → modal, decided-state rendering, startup re-attach, and the interim contact editor (R0.22). Registered in `run.py`. |
| `agents/outreach/daily.py` | **Extended.** The briefing line now carries the Gate 0 queue, after the intake cards. |

**Third slice, 2026-08-20** — extraction sanctioned and built, the review sheet,
segment ratings, and the briefing. Suite 664 → 698. Live line reads
`🎯 Outreach: 1 card(s) awaiting a decision · 47 to review`.

**Part 0 is complete.** What remains is Parts 1–4, and one thing Part 0 cannot do
for itself: **the pool only grows as fast as its channels find firms.**
`seed_list` is empty and `news_query` needs the loop enabled.

**✅ Part 0 verified in production, 2026-08-20.** The operator confirmed the
sheets and modals render as designed, and the database agrees: **25 candidates
posted across three messages** (12 / 12 / 1), `surfaced_at` and
`review_message_id` set on all 25, composition coaching 16 · corporate L&D 7 ·
instructional design 2 — those 2 being the exploration reserve reaching a segment
pure ranking excluded (R0.17).

**The read path is proven; the write path is not.** `decided = 0`. Nothing has
exercised `decide()` in production — the modal submit, the reason CHECK, and the
double-submit guard are covered by tests and by a live INSERT attempt, but not yet
by a real click. **The first accept and the first reject are the remaining
end-to-end verification**, and the reject matters more: it is the one that has to
satisfy the reason constraint under a real interaction.

One correction recorded because it nearly produced a false failure report: the
handoff first told barry-agent to expect **47** posted rows. 47 are *eligible*;
the window is **25** (R0.18). Checking against the wrong number would have read
the design working as a bug — the same shape as the 2026-08-16 FALSE FAIL.

**Verified against §0.6 on the live database:**

- **V0-1** provenance — 0 rows with a NULL `discovered_via`.
- **V0-2** no reject without a reason — the CHECK genuinely refuses the INSERT,
  proven by attempting one, not asserted in prose.
- **V0-4** no overlap — 0 rows join `outreach_targets` on `company_domain`.
- Audit — 49 `outreach_events` rows, so every review will be attributable.
- 47 of 49 clear the two-kind verification bar; **ELI, Inc. and Broadcat clear
  only one** (no company LinkedIn URL) and correctly will not surface until
  re-verified.

## 0.9 Operator decisions, 2026-08-19

Eight of the ten open questions are settled. Recorded here as the binding answers;
each is also applied where it belongs in the text below.

| # | Question | **Decision** |
|---|---|---|
| **OQ-A** | Suggested Pain Point vs the no-generation rule | **Limited generation.** One bounded call, marked draft, operator-facing only, excluded from packet assembly. `35-` §7 must be amended to say so before the code lands (R0.12). |
| **OQ-B** | Is 20/day sustainable | **Accepted as a ceiling**, not a quota. Achieved rate reported weekly; a falling rate is a finding. |
| **OQ-C** | Geography | **US only, all six segments.** Changes R0.4 and puts 37 of the 86 workbook rows out of scope (R0.13). |
| **OQ-D** | Who assigns pain layer | **Definitions recorded (R0.14); the field is held out of the card and out of the score** until the operator has a clearer picture of the market. Column exists, nullable, unsurfaced. |
| **OQ-F** | Workbook backlog | **Import the 49 US rows as unreviewed** (revised 2026-08-20 once the OQ-C collision surfaced). The 37 non-US rows are not imported at all. |
| **OQ-H** | Does `defer` count as a label | **No label.** |
| **OQ-I** | Recency half-life (R4.5) | **8 weeks.** |
| **OQ-J** | Writing weights back to the workbook | **Reported for approval**, never written automatically. |

| **OQ-E** | Which surface carries the daily 20 | **Option A — sheet rows** (2026-08-20), with the row button opening a detail modal. Both open questions on it confirmed against Discord's reference and discord.py 2.7.1 — see R0.15. |

**All ten settled.**

| # | Question | **Decision** |
|---|---|---|
| **OQ-G** | Exploration reserve share | **25% — 5 of the daily 20** (2026-08-20), stepping down as each segment reaches R4.2's 30-label minimum. Implemented in Part 0's window rather than deferred to Part 4 (R0.17). |
| **OQ-K** | What ICP v1 scores an unscored segment | **Neither prior nor midpoint — surface them instead** (2026-08-20). Five further slots are added for candidates from unscored segments, taking the daily target to **25** (R0.18), and each unscored candidate gets a surface on which the operator can score. |

| **OQ-L** | The scoring workflow downstream of the card | **Per segment, on demand** (2026-08-20). The five daily slots are evidence-gathering, and a standing affordance lets the operator enter category ratings whenever he is ready — not gated on a threshold. R0.20. |

**All open decisions are settled.**

## 0.11 OQ-L — settled: per segment, on demand

The grain question is answered: **per segment**, matching what the six criteria
actually measure. Four of the six describe a market rather than a company, so
nobody rates one engineering consultancy's "market size".

The operator's framing, which settles the trigger as well as the grain:

> The intent is evidence-gathering until I'm able to rate the category, but also
> an affordance for me to input category ratings when I can provide them.

Two consequences, and the second is what makes this different from what I had
recommended:

- **Not threshold-gated.** I proposed accumulating examples until some count
  unlocked the rating. The answer is *on demand* — the affordance stands open and
  he uses it when he feels able. So there is no "enough evidence" rule to design,
  argue about, or get wrong, and the five daily slots keep doing their job whether
  or not he has rated anything yet.
- **Segment scores stop being a constant.** Today the six criteria per segment are
  hardcoded in `agents/outreach/icp.py` as a transcription of the workbook. An
  operator-enterable rating means they must be read from the database, with the
  hardcoded workbook values as the fallback. R0.20 states the shape.

## TL;DR

Rev 1 opened with the observation that the engine sees exactly one thing about a
company: its open reqs. Rev 2 adds the larger gap above it — **the engine has no
way to find a company at all.** All 14 targets were imported by hand from the
operator's workbook. There is no sourcing path, no segment model, and no record of
why a firm was worth looking at.

| Part | What it does | LLM | Gated on |
|---|---|---|---|
| **0 — Discovery + the daily 20** | Source candidate firms across six segments, verify they are real and operational, assemble the 13-field card, surface 20 a day for accept/reject | Bounded, per candidate | nothing |
| **1 — News observation** | Per-target Google News + newsroom RSS into the unclassified queue and the graph | **None** | nothing |
| **2 — Classification + promotion** | Trent Crimm classifies the queue; matches promote to typed `outreach_evidence` | One Haiku call per item (`35-` §10) | Gate 1 exercised once |
| **3 — Contact + firmographic enrichment** | Bought fields, and the contact email + confidence the card needs | None | `35-` §16 #3, #4 (R21) |
| **4 — The selection feedback loop** | Accept/reject labels refine segment weights and candidate ranking | None (deliberately) | ~100 labels, so ~1 week of Part 0 |

Parts 1–3 keep their rev 1 numbering, outcomes, and internal `R1.x` / `V5`
references unchanged. Only their **scope widens**: they now apply to the discovery
pool as well as to accepted targets, wherever a rule says so.

## The source artifact — what the operator workbook already settles

`Education_LD_Leads_CRM_(current).xlsx` is not just a lead list. It contains a
market model that this spec adopts rather than reinvents. Read on 2026-08-19:

- **100 verified organizations, 29 columns, all `Status = New`, all `Touches = 0`.**
- **Three segments:** Instructional Design / Learning Agency (37), Coaching &
  Leadership Development (33), Corporate L&D / Training (30).
- **Four countries:** US 59, UK 20, Australia 11, Canada 10.
- **A weighted segment-scoring model** — Market Size 0.10, Market Growth 0.10, Firm
  Profitability 0.15, **Ability to Pay 0.25**, Urgency/Pain 0.20, Offering Fit 0.20
  — producing a weighted score and a **suggested outreach weight** (40% / 40% /
  20%), with sourced market sizes. **This is the object Part 4 refines.** The loop
  does not invent a scoring model; it moves numbers the operator already wrote.
- **A three-layer pain taxonomy** — L1 Built but incoherent (36), L2
  Founder/principal-bound (21), L3 Shipped & straining (43) — with the note that L3
  is the cohort to work first. **This is the "problem space" axis** the revision
  asks the system to learn about, and it already exists.
- **A three-level email-confidence vocabulary** — `Public` (5), `Inferred
  (pattern)` (38), `General inbox` (57). Part 0 reuses these exact three values
  rather than inventing a scale.

**One finding worth recording, because it explains a live puzzle.** The workbook's
`Date Added` is **2026-06-10 for all 100 rows**, and the 14 imported targets took
that column as `trigger_date`. That is the origin of the uniform trigger date
recorded in `70-build-order.md` on 2026-08-17 — an import-batch stamp, not an
observed event. Part 0 must not repeat it: a discovered firm has **no trigger
date** until a trigger is observed, and R0.3 says what to do about that.

## Goal & Non-Goals (all parts)

**Goal:** a continuously refreshed, sourced, dated picture of a *market* — which
segments, firm profiles, and pain layers are worth the operator's attention —
built from firms the operator has personally accepted or rejected, with each
survivor carrying enough background to decide on it in one screen.

**Non-goals, spanning every part:**

- **No generation in the packet.** The packet remains assembled, not generated
  (`35-` §7). The one requested field that would breach this is isolated and put to
  the operator as OQ-A rather than assumed either way.
- **No LinkedIn scraping.** R14 is **Policy** and OQ1 is unchanged. Part 0 stores
  and displays LinkedIn **URLs**; it never reads a LinkedIn page programmatically.
  This is the single hardest constraint on the discovery design and it is not
  reopened here.
- **No ZoomInfo, Glassdoor, or Comparably scraping.** Unchanged from rev 1 (§3.4).
- **No automatic outreach.** Nothing discovered is ever contacted without passing
  the existing Gate 1 and its capacity check. Part 0 adds a gate; it removes none.
- **No automatic culling.** A rejected candidate is recorded with a reason and kept
  (R0.7). The rev 1 rule that dropping destroys accrued history is unchanged.
- **No new classifier.** `35-` §10 already specifies one.

---

# Part 0 — Discovery and the daily 20

<part_0>

**The gap this fills.** All 14 targets were typed in by hand from the workbook.
There is no code path that finds a company, no segment model in the database, and
no record of why a firm was worth a look. `35-` §5 intake assumes a populated
funnel; nothing populates it.

## 0.1 Outcome (S1)

When Part 0 is done, all of the following are observably true:

1. **A ranked queue of unreviewed candidate firms exists, and the operator sees up
   to 20 of them per day**, each rendered with all 13 fields of §0.3 or an explicit
   `unknown` where a field could not be established. Verifiable:
   `SELECT count(*) FROM outreach_discoveries WHERE reviewed_at IS NULL` is
   non-zero, and the daily surface renders exactly `min(20, unreviewed)` items.
2. **Every discovered firm carries its provenance**: which channel surfaced it,
   the query or list that produced it, and the date. No row exists without a
   source. Verifiable: `SELECT count(*) FROM outreach_discoveries WHERE
   discovered_via IS NULL` returns `0`.
3. **Every discovered firm is verified real and operational before it is shown**,
   on machine-checkable evidence, and the evidence is what the *Verification Note*
   field displays (R0.5). A firm that fails verification is never surfaced.
4. **Accept and reject are both recorded, and reject carries a structured reason**
   (R0.7). Verifiable: `SELECT count(*) FROM outreach_discoveries WHERE
   review_decision = 'reject' AND reject_reason IS NULL` returns `0`, enforced by a
   CHECK rather than by application code.
5. **No duplicate ever reaches the operator.** A firm already in
   `outreach_discoveries` or `outreach_targets` — matched on `company_domain` — is
   never surfaced a second time. Verifiable by running discovery twice and
   asserting the unreviewed count is unchanged.
6. **Nothing discovered is contacted.** Accepting at Gate 0 adds a firm to the
   pool; it does not create a touch, does not consume a capacity slot, and does not
   post a Gate 1 card unless a trigger exists (R0.6).
7. **The six-segment taxonomy is a database constraint, not a convention.**
   Verifiable: inserting a seventh segment value fails.

## 0.2 Non-goals (S2)

- **Not a scraper.** No LinkedIn, no ZoomInfo, no Glassdoor, no rendering
  headless browsers against sites that forbid it. Discovery uses published feeds,
  public APIs, and the companies' own sites.
- **Not an outreach rate.** See §0 above: 20/day is review throughput, and `35-`
  §8's ceiling of 15 concurrent sequences is untouched.
- **No auto-promotion to `outreach_targets`.** Accept is a human act, and even
  after acceptance a firm only becomes a target when it has a real trigger (R0.3).
- **No contact-level personal data beyond name, title, public work email, and
  public profile URL** — and even that is gated by R21 in Part 3. Part 0 populates
  the contact fields it can establish from a company's own site; the rest reads
  `unknown` until Part 3 lands.
- **No scoring model in v1.** The ICP Fit shown on day one is the workbook's
  existing weighted-segment arithmetic (R0.8). The learned model is Part 4, and it
  needs Part 0's labels to exist first.

## 0.3 The field contract — the 13 fields

This is the card. Every field is either established with a source or displayed as
`unknown`; **no field is ever guessed silently.**

| # | Field | Where it comes from | Available when |
|---|---|---|---|
| 1 | **Industry / Segment** | Assigned at discovery from the six-value taxonomy (R0.1); the sourcing channel usually determines it | Part 0 |
| 2 | **Description** (incl. best available ARR) | The company's own site and about page. **ARR is an estimated band with a stated basis, never a fact** (R0.9) | Part 0; ARR band improves with Part 3 headcount |
| 3 | **ICP Fit** | v1: the workbook's weighted segment score plus firm-profile modifiers (R0.8). v2: the learned score from accept/reject labels | Part 0 (rule); Part 4 (learned) |
| 4 | **Contact Name** | Company site leadership/about page | Part 0 where published; Part 3 otherwise |
| 5 | **Contact Title** | Same source as #4 | Part 0 / Part 3 |
| 6 | **Contact Email** | Pattern inference or a general inbox in Part 0; a verified address needs a provider | **Part 3** for anything better than `Inferred` / `General inbox` |
| 7 | **Email Confidence** | The workbook's own three values: `Public`, `Inferred (pattern)`, `General inbox` | Part 0 |
| 8 | **Company LinkedIn Page** | URL only, resolved from the company site or a public search. Never read | Part 0 |
| 9 | **Contact LinkedIn Page** | URL only, same rule | Part 3 mostly; `unknown` is common and acceptable |
| 10 | **Verification Note** | The evidence that the firm is real and operational (R0.5) | Part 0 |
| 11 | **Observed Signal** | The news/newsroom observation | **Part 1**; reads `none observed yet` until Part 1 runs |
| 12 | **Suggested Pain Point (Outreach Hook)** | One bounded generation from the observed signal, marked **draft — not for sending** (R0.12). Needs the `35-` §7 amendment first | Part 0, after Part 1 supplies a signal |
| 13 | **Touches to date** | `count(*)` from `outreach_touches`; always `0` for a pool row by construction | Part 0 |

**Read this table before estimating Part 0.** Seven of the thirteen fields are
fully available from Part 0 alone. Four depend on Part 3 (contact email, confidence
above `Inferred`, contact LinkedIn) or Part 1 (observed signal), and field 12
depends on field 11, so it is empty until Part 1 runs too. **A card rendered before
Parts 1 and 3 land is a real card with five fields reading `unknown`** — useful for
segment-level triage, thin for contact-level decisions. That is the honest
sequencing consequence and it argues for running Part 1 alongside Part 0 rather
than after it.

**Pain layer is deliberately not a field here** (OQ-D, R0.14). The taxonomy is
recorded and the column exists; it stays off the card and out of the score until the
market picture is clearer.

## 0.4 Settled rules

<settled>

**R0.1 — Six segments, CHECK-pinned.** `corporate_l_and_d`,
`coaching_leadership`, `instructional_design`, `engineering_consultancy`,
`product_design_agency`, `msp_it_consultancy`. The precedent is 0014, which pinned
`trigger_kind` to eight values after the spec referenced "the eight triggers" three
times and enumerated them nowhere. The first three carry the workbook's existing
lead counts and weights; **the three new segments start with no weight and no
history**, which is the point — Part 4 discovers what they are worth.

**R0.2 — The pool is a new table, `outreach_discoveries`, not a status on
`outreach_targets`.** The reason is a hard schema fact, not a preference:
`outreach_targets.trigger_kind` and `.trigger_date` are **NOT NULL**, and a
discovered firm has no trigger. There is precedent for the alternative — 0014 made
`stage` nullable rather than fabricate values — but two arguments settle it the
other way. First, a discovery carries fields a target never will: the review
decision, the reject reason, the discovery channel, the ICP-fit score and the model
version that produced it. Second, and decisively, **the uniform `trigger_date` of
2026-06-10 is what fabricating a trigger looks like in production** — it is still
in the database and it silently drove every score. Relaxing two NOT NULLs to admit
unreviewed rows into the scored table invites exactly that failure again.

**R0.3 — SUPERSEDED 2026-08-27 by the acceptance-date model (0023).** Originally:
a discovery could not be promoted without a real observed market trigger, to avoid
the fabricated batch date. That failure is now prevented differently and better:
`trigger_date` means **when the operator accepted the firm into the pipeline**,
which is a real dated decision, never fabricated. Promotion uses the discovery's
`reviewed_at` date and defaults `trigger_kind` to `operator_selected`; a real
market trigger, when Part 2 classifies one, overrides. The original rule below is
kept struck-through in spirit for the record.

**R0.3 (original) — A discovery never gets a fabricated trigger.** Promotion to
`outreach_targets` requires a real `trigger_kind` and a real `trigger_date` with a
`trigger_source_url`. An accepted firm with no observed trigger stays in the pool
as `accepted`, is polled by Part 1 for news, and is promoted the moment Part 2
classifies a trigger for it. This turns the trigger from an import formality back
into an observation, which is what `35-` §4 always assumed it was.

**R0.4 — Sourcing channels, named and bounded.** Each is a published or public
surface, and each stamps `discovered_via`:

- *Segment news queries* — Google News RSS per segment and geography, the same
  unauthenticated endpoint Part 1 already uses (R1.2). Finds firms in the act of
  doing something.
- *ATS board enumeration* — **corrected 2026-08-20: this does not exist.** No ATS
  provider publishes an index of boards; every adapter takes a board *token* and
  returns that board. So the seven adapters **verify** a firm already in hand —
  which is real value, and is the strongest verification kind available — but they
  cannot find one. The same correction applies to the two feed-shaped channels
  below: see `agents/outreach/discovery/__init__.py` for what each can and cannot
  do. **Finding boutique firms at this size is a research problem, not a fetch
  problem**, which is how the operator's own 100 rows came to exist, so the
  curated `seed_list` channel is the workhorse and the others become buildable
  once an entity-extraction step is sanctioned.
- *Award and ranking lists* — the sources the workbook already cites in its own
  Verification Notes: Selling Power, Training Industry Top 20, Forrester Wave, and
  the equivalent lists for the three new segments. High precision, low volume,
  dated, and citable.
- *Directory and association membership rolls* where a public listing exists.
- *The company's own site*, for everything after the firm is identified.

**Geography: United States only, across all six segments** (OQ-C, 2026-08-19).
Every sourcing query is US-scoped. The workbook's UK, Australian, and Canadian rows
stay in the file and out of the funnel — see R0.13. Reopening geography later costs
nothing that has been thrown away, which is why it is a cheap decision to revisit.

**Forbidden here as everywhere:** LinkedIn, and any source whose terms prohibit
automated access. This is the binding constraint on discovery volume, and with
`20/day` accepted as a ceiling rather than a quota (OQ-B), a falling achieved rate
is reported as a finding rather than padded around.

**R0.5 — Verification is evidence, not a status.** A firm is surfaced only when at
least two of four kinds hold, and the *Verification Note* names which. **Three of
the four were narrowed on 2026-08-20** because the code could not honestly do what
this rule first claimed — each correction is narrower than the original, never
wider (`agents/outreach/verify.py` carries the reasoning):

| Kind | What is actually checked |
|---|---|
| `open_req` | A supported ATS board returns **at least one** open role. The strongest kind — a live fetch against a structured API. A detected but *empty* board does not count; that is AIIR's exact situation. |
| `live_site` | The site answered a request. **Recency is NOT verified** — there is no generic way to date an arbitrary homepage, so the original "content dated inside 12 months" was a promise the code cannot keep. |
| `third_party_dated` | A citation **supplied by whoever sourced the firm**. Parsing an award or press mention out of free text by keyword would be guessing. |
| `linkedin_url_present` | A company LinkedIn URL is **on file**. Renamed from `linkedin_resolves`: LinkedIn is never fetched, because R14 is Policy and LinkedIn blocks automated requests. The weakest kind, named so nobody reads it as more. | **`35-` §3's discipline applies unchanged:** an
unverified firm shown as verified produces a confident, checkable, wrong outreach,
which is the failure mode the whole staleness model exists to prevent.

**R0.6 — Gate 0 is a new gate and does not touch Gate 1.** Gate 0 asks *is this
firm worth tracking?* Gate 1 (`35-` §5) asks *do we start a five-touch arc?*, is
capacity-bound at 15, and is unchanged. Accepting 20 firms a day is affordable
precisely because acceptance costs nothing but a row. **A Gate 0 accept never
posts a Gate 1 card by itself** — the existing 120-second intake poll still
requires `status='candidate'` and `treatment='work'`, which requires a real trigger
and a human S2–S5 judgement.

**R0.7 — Rejection requires a structured reason, CHECK-enforced.** The repo already
settles this pattern twice: the drain rule refuses to park a target without a
`stalled_reason`, and `35-` §9 lists skip-requires-reason as a database constraint
rather than an application promise. A rejection without a reason teaches Part 4
nothing, and Part 4 is the entire justification for the review effort. Reasons:
`wrong_segment`, `too_small`, `too_large`, `no_pain_signal`, `poor_contact_path`,
`geography`, `competitor_or_conflict`, `already_known`, `other` (free text
required). **The reason is the training label. Getting it wrong is not a UI
annoyance; it is corrupt data.**

**R0.8 — ICP Fit v1 is the workbook's arithmetic, not a new model.** The weighted
criteria (Ability to Pay 0.25, Urgency/Pain 0.20, Offering Fit 0.20, Firm
Profitability 0.15, Market Size 0.10, Market Growth 0.10) already exist and the
operator already set them. v1 scores a firm as its segment weight adjusted by
firm-level modifiers that are observable at discovery: headcount band, pain-layer
assignment where one can be inferred, and whether a trigger is present. **Every
score records the model version that produced it**, so Part 4 can measure itself
against v1 rather than replacing it blind.

**R0.9 — ARR is an estimated band with its basis attached, or it is absent.**
Private boutiques of 10–100 people do not publish revenue; the workbook's own
Employees column is itself an estimate (`25-50`, `~30`). The Description field
therefore carries a band derived from headcount times a segment revenue-per-head
assumption, **labelled as an estimate with both inputs shown**, or nothing at all.
This is the same rule that made increment 1b capture ATS `posted_at` for display
but refuse to use it as `first_seen_at`: a number whose precision is not real must
not be presented as though it were.

**R0.10 — Dedup on `company_domain`, across both tables.** `outreach_targets`
already has a UNIQUE index on it. The pool gets the same, plus a check against
targets before surfacing. Domain is the only stable identifier here; company names
collide and rebrand.

**R0.11 — The daily 20 is a ranked window, not a batch.** Items are ranked by ICP
Fit descending and surfaced up to 20; anything unreviewed at the end of the day
stays in the pool and re-ranks tomorrow rather than expiring. **A day that produces
fewer than 20 verified candidates surfaces fewer**, and reports why. Padding the
queue with unverified firms to hit a number would defeat R0.5.

**R0.12 — The pain hook is generated, and fenced** (OQ-A, 2026-08-19). One bounded
call per candidate produces one or two sentences from the observed signal and the
firm description. Four constraints make it safe to allow:

- It is stored in `outreach_discoveries`, **never in `outreach_packets`**, and
  packet assembly does not read the column. `35-` §7's rule holds where it matters.
- It renders marked **draft — not for sending**, on the operator surface only.
- It is bounded and hardened at the write boundary like every other observed text
  (H1, H2), and the source signal it was built from is displayed beside it.
- **`35-` §7 must carry the amendment before this code ships.** The rule becomes:
  generated text is permitted on an operator-facing decision surface and remains
  forbidden in anything a recipient sees. An unamended rule that the code already
  breaks is the failure the working convention exists to prevent.

**R0.13 — The workbook backlog imports US-only** (OQ-F revised against OQ-C,
2026-08-20). Of the workbook's 100 rows, 14 are already `outreach_targets` and
**86 remain: 49 US, 37 not** (UK 18, Australia 10, Canada 9). **Only the 49 US rows
import**, as **unreviewed** — they were assembled but never triaged, and importing
them as accepts would fabricate 49 labels Part 4 would then learn from.

**The 37 non-US rows are not imported.** They are not rejections — nobody judged
them — and recording them as rejections would corrupt the reject-reason
distribution V0-7 reads. They are not out-of-scope rows sitting in the pool either;
they simply stay in the workbook, which remains their system of record. If
geography reopens, the importer runs again with the filter widened. This is the
cheaper half of the earlier proposal and it keeps the pool meaning one thing: firms
that are in scope and awaiting a decision.

**R0.14 — The pain-layer taxonomy is recorded now and used later** (OQ-D,
2026-08-19). The operator's three layers, verbatim, assigned by LLM from a firm's
current AI product-offering maturity:

- **L1 — Built but incoherent.** Substantial codified IP (curricula, methods,
  libraries, multiple service lines) exists but is fragmented, stale, or
  underleveraged — not organised into a product motion. Raw material is out of heads
  but not coherent. Highest-leverage legibility-first targets.
- **L2 — Founder/principal-bound.** Value is locked in the founder's or named
  principals' persona and judgment; brand = person; revenue = their hours. Little
  transferable productised IP. Highest founder-identity risk — screen hard for
  delegation before engaging.
- **L3 — Shipped & straining.** Has launched, partnered on, acquired, or is actively
  pivoting to a product/platform/AI tool that is now a live source of strain.
  Best-qualified: budget + urgency + scar tissue. Pitch: legibility is why the
  shipped thing is not differentiating.

**Held out for now.** `pain_layer` exists as a nullable column and is **not** a card
field, **not** an ICP-fit input, and **not** a Part 4 factor, until the operator has
a clearer picture of the market. Recording the definitions now costs nothing and
means the axis is ready the moment it is wanted; scoring against a taxonomy before
the market is understood would bake in an assumption this whole exercise exists to
test.

**R0.16 — ICP v1 does not differentiate within a segment, and that is a
property, not a defect** (measured 2026-08-20). Scoring the workbook's 59 US rows
produced exactly three distinct values: coaching 84, corporate L&D 83,
instructional design 62. Every firm in a segment scored identically because
**all 59 fall inside the 10-100 headcount band**, so the only firm-level modifier
available at discovery awards all of them the same 20 points.

This is faithful to R0.8 — the operator's model *is* a segment model, and before
Part 1 supplies a signal and Part 3 supplies contact quality there is little
firm-level information to score on. But it has a consequence worth stating
plainly: **ranking the daily 20 by ICP fit ranks by segment.** The window would
be all coaching for roughly a day, then all corporate L&D, then the new segments
at 76, then instructional design — and *within* a segment the real ordering falls
to the `discovered_at` tiebreak, which is arbitrary.

**Not resolved here, deliberately.** The fix is window composition — interleaving
segments so each day is a mix — and window composition is exactly what OQ-G is
about. Choosing an interleave rule now would be picking OQ-G's answer quietly
under another name. The finding strengthens the case for a non-zero reserve and
is recorded for that decision. What v1 must not do is acquire invented modifiers
to manufacture spread; a score that varies for no observed reason is worse than
one that admits it cannot tell these firms apart yet.

**R0.17 — The exploration reserve is 25% of the daily window, and it lives in
Part 0** (OQ-G, 2026-08-20). Five of the twenty slots are reserved for
under-sampled segments; the other fifteen rank by ICP fit.

*Why here rather than in Part 4, where R4.3 put it.* The reserve governs **window
composition**, and Part 0 owns the window. Leaving it in Part 4 would mean the
operator's answer changed nothing until Part 4 is built, and every label
collected before then would come from a pure-exploitation window — precisely the
self-confirming sample R4.3 exists to prevent. Part 4 still owns the *step-down*:
as each segment reaches R4.2's 30-label minimum it stops being under-sampled and
the reserve shrinks on its own.

*The rule, stated so it is testable.* A segment is under-sampled while it has
fewer than 30 labelled decisions (R4.2). Reserve slots are filled round-robin
across under-sampled segments, ordered by fewest labels and then by the
segment's **best ICP score ascending** — worst-ranked segment served first —
each contributing its best-ranked unreviewed candidate. The ascending order is
load-bearing and was found by testing: ordering by name instead looks
deterministic but hands the reserve to whichever segment sorts first
alphabetically, which on day one (every segment at zero labels) can be the
segment ranking already dominates. **The reserve exists to reach what ranking
will not**, so it must serve the segments ranking excludes. **Unfillable reserve slots fall back to the ranked list**: returning
fewer than 20 to honour a reserve would waste review capacity, which is the
scarcest thing in this design. The reserve guarantees *inclusion in the window*,
not position within it.

*Measured effect on the current pool, so the number is not oversold.* Run
against the 47 surfaceable rows on 2026-08-20:

| Reserve | Window composition |
|---|---|
| 0% | coaching 16 · corporate L&D 4 · **instructional design 0** |
| **25%** | coaching 16 · corporate L&D 2 · **instructional design 2** |

The reserve pulls in a segment that ranking excluded entirely, which is exactly
its job. It does not dent coaching's 16, because coaching holds 16 of the pool's
47 rows and the ranked fifteen absorb them either way. **The reserve does its
real work once sourcing runs** and the three new segments have candidates that
would otherwise rank below every established one; on the imported backlog the
effect is real but small, and the backlog drains in about three days at 20/day
regardless.

**R0.18 — The daily window is 25 slots in three buckets** (OQ-K, 2026-08-20).

| Bucket | Slots | Filled from |
|---|---|---|
| Ranked | 15 | Highest ICP fit, any segment |
| Exploration reserve | 5 | Segments under R4.2's 30-label minimum (R0.17) |
| **Unscored segments** | **5** | Segments with **no workbook criteria score** |

**Under-sampled and unscored are different axes**, which is why they get separate
buckets rather than one larger reserve. Under-sampled is about *decisions not yet
made* — the loop has too few labels to report an accept rate. Unscored is about
*a rating the operator has never given* — the segment has no six-criteria score,
so every candidate in it inherits a prior rather than a judgement. A segment can
be one, the other, or both; today all six are under-sampled and three are
unscored.

Buckets fill in order — unscored, then reserve, then ranked — and each excludes
rows already picked, so a candidate never occupies two slots. **Unfillable slots
fall back to the ranked list** for the same reason as R0.17: a short window wastes
review attention.

*Two consequences, stated rather than discovered later.*

- **The unscored bucket is empty today**, and will be until sourcing exists. All
  49 pool rows are corporate L&D, coaching, or instructional design — every one a
  scored segment. The bucket does nothing until `agents/outreach/discovery/` can
  find an engineering consultancy, so the first firms that fill it arrive with the
  sourcing channels, not before.
- **Daily review load rises from 20 to 25**, which is a 25% increase against
  risk D2 (review fatigue), the risk this design already rates High. The operator
  set the number deliberately; it is flagged here so that if label quality
  degrades, the window size is a known suspect rather than a surprise.

**R0.19 — Upward-only governs Gate 0's window too** (O4 clarified, 2026-08-20).
The rule the rescore loop follows — *card upward crossings, record everything
else* — is a principle that carries across gates, not a second code branch. The
rescore loop does **not** read `outreach_discoveries`.

At Gate 0 it binds **re-surfacing**, which is what makes the never-delete rule
operable rather than merely archival:

- A **new** candidate surfaces on its own merit. It has never been seen, so there
  is no movement to be upward or downward.
- A candidate already **decided** — rejected, or accepted and not promoted — may
  re-enter the window **only when its ICP fit has risen** since the decision was
  taken. This is the mechanism behind the operator's O3 reasoning: *"the firm may
  change at some point down the road, making it newly able to survive the band."*
- **Downward movement never surfaces and never cards.** It is recorded and
  countable, nothing more.

*Not implementable yet, and deliberately not pre-built.* Nothing re-scores a
discovery today — v2 arrives with Part 4, and enrichment that could move a score
arrives with Part 3. **No migration is needed when it does:** `outreach_events`
already captures every `icp_fit_score` change on this table with a timestamp and
an actor, so the score as at the review is reconstructable from the audit log
rather than needing a denormalised column. A column can be added later for query
convenience; it is not needed for correctness, and adding it now would be
speculative schema for a comparison nothing can yet make.

**R0.20 — Segment criteria become operator-enterable, database-first**
(OQ-L, 2026-08-20). The scoring affordance stands open rather than unlocking at a
threshold, so the six criteria per segment can no longer be a Python constant.

| Layer | Role |
|---|---|
| `outreach_segment_scores` (new table) | What the operator has entered, per segment, with `rated_at` and the six criteria |
| `icp.SEGMENT_CRITERIA` | Fallback — the workbook transcription, unchanged, still the source for the three segments already rated |
| The workbook | Remains the system of record for the market model (R4.6); Part 4 reports proposed weights back to it for approval, never writes (OQ-J) |

A segment counts as **scored** once a row exists for it, which is also what
removes it from the unscored bucket (R0.18) — so rating a category has a visible,
immediate effect on the next day's window. That feedback is what makes the
affordance worth using, and it is the reason the bucket and the affordance are
the same mechanism rather than two features.

**Not built.** Next increment, alongside the sourcing channels. Stated here so it
is designed rather than discovered.

**R0.21 — Bounded entity extraction is sanctioned, and the operator validates the
name** (2026-08-20). The three feed-shaped channels R0.4 named can now be built:
one bounded LLM call turns news items and award-list entries into candidate
company names and likely domains.

The operator's condition is what makes this safe, and it is load-bearing:

> The entity name can be something that I validate as part of my scoring process.

So extraction does not have to be right — it has to be **cheap, bounded, and
caught when wrong**. Four properties make that true, and none of them is the LLM
being accurate:

1. **A hallucinated firm cannot surface.** Extraction proposes a name and a
   likely domain; `verify.py` then fetches that domain. An invented company has
   no site to answer, so it clears at most one verification kind and the two-kind
   minimum keeps it out of the window. **The verification bar, written for a
   different reason, is what makes extraction survivable.**
2. **The operator sees the name and the source.** Every extracted candidate
   carries the article URL that produced it, displayed on the review card beside
   the name, so a wrong entity is visible rather than inferred.
3. **It is metered like every other LLM path.** `agent_name='outreach-discover'`,
   `function_label='outreach_discovery'`, its own daily ceiling, one
   `agent_runs` row per call — so a runaway loop stops rather than bills.
4. **H5 is enforced at this prompt boundary.** Feed text is third-party content
   and this is the first place in Part 0 that a prompt exists. A signal whose text
   trips `screening.screen()` is quarantined and never placed in the prompt, which
   is the same rule Part 2 applies to the classifier.

**This changes the outreach LLM budget, which `35-` §14 states.** Outreach was
"$0.30/day, Trent Crimm only, the only outreach LLM spend". That sentence is no
longer true and has been corrected there rather than left to rot.

**What extraction must never do:** decide a segment (the query already fixes it),
decide fit, write a pain hook, or promote anything. It names a company and guesses
a domain. Everything downstream — verification, scoring, the operator's decision —
is unchanged and unaware that an LLM was involved.

**R0.22 — Contact correction is an INTERIM surface, deliberately narrow**
(operator decisions, 2026-08-21). Testing the review sheet showed what any real
dataset shows: some contact details are wrong or stale.

`35-` §9 assigns correcting records to **NocoDB** — increment 3, gated on
install, a dedicated role and Tailscale Serve. This is not that. It is the
interim path, and it is scoped to **contact fields only** so it cannot grow into
a second editor competing with the surface that is meant to own the job. When
NocoDB lands, this shrinks or goes; it does not get extended.

*Three Discord facts decided its shape, and the first two killed the obvious
design.* The operator asked for an Edit button inside the review modal:

- **A button cannot appear in a modal** — Discord confines buttons to messages.
- **A modal cannot open a modal.** Discord's reference: a modal response is
  *"Not available for `MODAL_SUBMIT` and `PING` interactions"*.
- **A modal holds five children**, and the review modal already uses four. Three
  contact fields do not fit beside them.

So editing lives outside the review modal, reached two ways, both costing the
sheet nothing:

| Entry point | Reaches |
|---|---|
| `/gate0-edit` with autocomplete | **Any record, any time** — including firms decided weeks ago |
| An Edit button on the ephemeral reply after a decision | The firm just reviewed, without a context switch |

The slash command is the primary. The case that actually matters is noticing a
bad contact *after* deciding, or days later while writing the email — by then the
row is on no live sheet at all. It also adds no component to the sheet, which
matters because every component there is another thing that must survive a
restart, and re-attach has already broken once.

**One surface, both record types.** All 14 current targets came from the CSV
import and have **no** discovery row, so a pool-only editor would have reached
none of the firms closest to being contacted — including AIIR, the one with a
live Gate 1 card. The editor therefore keys on `company_domain` and writes to
whichever records exist, in one transaction, so a promoted firm can never end up
with a corrected pool row and a stale target.

**Only what moved is written.** `TextInput.value` falls back to its prefilled
default, so an untouched field submits its current value. Without a diff against
what was shown, every edit would rewrite all five fields and the audit log — which
is the history — would record four changes that never happened.

**R0.23 — Verifying an address by hand raises its confidence** (operator
decision, 2026-08-21; migration 0020). `email_confidence` gains a fourth value,
`operator_verified`, ranking above `inferred_pattern`: an address the operator
confirmed is stronger evidence than a pattern guess, so correcting one should
raise its confidence rather than silently keep the old label.

**`outreach_targets` gained the column at the same time**, because it did not
have one. Without it a raised confidence would have been invisible exactly where
it matters — the packet reads targets, and the send decision happens there. The
migration therefore had to drop and recreate `v_outreach_scored`, which is
`SELECT t.*` with a frozen column list; that is the 0016 trap, and
`verify_schema.sql` confirms no drift.

**R0.15 — The review surface is Components V2 sheet rows, and the row button opens
a detail modal** (OQ-E, 2026-08-20). Both uncertainties flagged at wireframe time
are now resolved against Discord's component reference and the installed
discord.py 2.7.1:

*The per-message budget is 40 components,* counting nested children. One row costs
three — `Section` + `TextDisplay` + `Button` accessory — and the chrome is a
`Container` plus a header `TextDisplay`, so **12 rows fit** and the daily 25 posts
as three messages.

**There is deliberately no footer action row.** The wireframe carried one, with
"Accept all shown" among its buttons. Built, that turns out to be a one-click way
to fabricate training labels — **risk D1, the risk this design rates High** — so
it is gone, and every accept costs one deliberate modal. Dropping it also buys
back the four components that took the earlier estimate from 12 rows down to 11. `Separator` between rows
would cost a fourth component each and drop the ceiling to 8, so rows are separated
by styling rather than by a component. **This is enforced as a test, not as prose:**
the view builder counts its own components and fails over 40, because a silent
truncation by the API would look like missing candidates.

*Buttons cannot appear in a modal.* Discord restricts them to messages. The modal
therefore carries no Accept/Reject buttons, and the decision is captured by
components modals do allow — all present in discord.py 2.7.1:

| Modal child | Purpose |
|---|---|
| `TextDisplay` | The full 13-field detail block — the Option C card, rendered as text |
| `Label` + `RadioGroup` | The decision: accept · reject · defer |
| `Label` + `RadioGroup` (or `StringSelect`) | The reject reason, R0.7's nine values |
| `Label` + `TextInput` | Free-text note, required when the reason is `other` |

A modal holds at most five children (`discord.py` raises above five), so this fits
with one spare. **The reason-required-on-reject rule cannot be expressed in the
modal UI** — Discord has no conditional requirement — so it is enforced in
`on_submit` and, authoritatively, by the CHECK constraint from R0.7. That ordering
is deliberate and matches 0013's rule that an invariant is a database constraint
rather than an application promise.

*Consequences accepted:* row columns will not visually align, because Discord
renders proportional text and its only monospace context is a code block, which
cannot contain components. The row therefore carries five fields as a readable
line, not a table, and the modal carries the rest.

</settled>

## 0.5 What gets built

| Artifact | Content |
|---|---|
| **Migration 0018** | `outreach_discoveries` — `id`, `company_name`, `company_domain UNIQUE`, `company_url`, `careers_url`, `segment` (CHECK, six values), `country`, `hq_location`, `headcount_band`, `arr_estimate_low/high/basis`, `description`, `icp_fit_score`, `icp_model_version`, `contact_name`, `contact_title`, `contact_email`, `email_confidence` (CHECK: three workbook values), `company_linkedin_url`, `contact_linkedin_url`, `verification_note`, `verified_on` (array of evidence kinds), `pain_layer` (CHECK: L1/L2/L3, nullable), `discovered_via`, `discovery_query`, `discovered_at`, `surfaced_at`, `reviewed_at`, `review_decision` (CHECK: accept/reject/defer), `reject_reason` (CHECK enum), `reject_note`, `promoted_target_id FK`. CHECK: `review_decision='reject'` implies `reject_reason IS NOT NULL`. **`ALTER TABLE … OWNER TO barry_agent`** — the 0011 bug. Audit trigger `outreach_log_event()` attached, so every review is attributable. |
| `agents/outreach/discovery/` | **Built.** Channel registry behind a common `find(segment)` interface, plus `seed_list.py` reading the git-tracked `config/outreach/discovery/seeds.yaml`. `news_query` / `award_lists` / `directories` are **not built** — see the corrected R0.4 for why each needs either entity extraction or a bespoke per-source parser and terms review. Adding one is adding a file. |
| `agents/outreach/verify.py` | **Built.** The four R0.5 checks, each returning `(passed, note)`, pure except the fetch. Never raises; never fetches LinkedIn. |
| `agents/outreach/icp.py` | v1 scorer (R0.8), versioned. Part 4 adds v2 beside it; it does not edit v1. |
| `agents/outreach/discover.py` | **Built.** Source → scope-filter → dedup → verify → score → insert, across all six segments. A thin firm is recorded rather than skipped, so the next run dedups instead of re-probing. |
| `agents/_lib/outreach_discovery.py` | Decision core, Discord-free, mirroring `_lib/outreach_intake.py`: `list_for_review(conn, limit)`, `decide(id, action, reason)`, `promote(id, trigger)`. |
| `agents/discord_bot/cogs/outreach_discovery.py` | The Gate 0 surface (R0.15): a `LayoutView` of up to 11 `Section` rows per message, each with a Review button opening the detail modal that carries all 13 fields and captures decision + reason. Asserts its own component count against the 40 ceiling. |
| `loops/outreach-discover.md` | **Built**, `enabled: false`. Unusually there is a real reason beyond convention: the seed list is empty, so enabling it today buys network calls and a zero. |
| `cli/discovery_import.py` | Bulk-import the workbook's **49 US rows** into the pool as unreviewed (R0.13); the 37 non-US rows are skipped and reported, not stored. Part 4 then starts with real inventory rather than an empty table. |
| Tests | Pure: segment CHECK, dedup against both tables, reject-without-reason rejected by the database, ICP v1 determinism and version stamping, ARR band arithmetic and its absence when headcount is unknown, ranking and the `min(20, n)` window. Against live Postgres: idempotent re-run, audit rows on review, promotion refusing a target with no trigger. |

## 0.6 Verification (S3) — named before the build

```
# V0-1 — provenance on every row
psql aiadaptive_cos -c "SELECT count(*) FROM outreach_discoveries WHERE discovered_via IS NULL;"
# expect: 0

# V0-2 — no reject without a reason (should be impossible, not merely absent)
psql aiadaptive_cos -c "SELECT count(*) FROM outreach_discoveries WHERE review_decision='reject' AND reject_reason IS NULL;"
# expect: 0, and the equivalent INSERT must raise

# V0-3 — idempotency: run twice, compare
psql aiadaptive_cos -c "SELECT count(*) FROM outreach_discoveries;"
uv run python -m agents.outreach.discover
psql aiadaptive_cos -c "SELECT count(*) FROM outreach_discoveries;"
# expect: identical

# V0-4 — no overlap with existing targets
psql aiadaptive_cos -c "SELECT count(*) FROM outreach_discoveries d JOIN outreach_targets t USING (company_domain);"
# expect: 0

# V0-5 — the capacity ceiling is untouched by discovery
psql aiadaptive_cos -c "SELECT * FROM v_outreach_capacity;"
# expect: cold_live unchanged by any number of Gate 0 accepts
```

**V0-6 — field completeness, measured rather than assumed.** After the first
week, report the non-null rate of each of the 13 fields across surfaced
candidates. **This is the acceptance check that matters**: a card whose contact
fields are `unknown` 90% of the time is a segment-triage tool, not the decision
surface the revision asks for, and the honest response is to move Part 3 forward,
not to fill the fields with guesses.

**V0-7 — precision, judged by the operator.** After the first 100 reviews, the
reject-reason distribution is the measurement. A dominant `wrong_segment` means
sourcing queries are miscalibrated; a dominant `too_small` means the headcount
filter is wrong; a dominant `poor_contact_path` means Part 3 is the bottleneck.
**Each of these points at a different fix, which is why R0.7 makes the reason
mandatory.**

## 0.7 Open decisions (S4)

**None.** All twelve are settled (§0.9), the last two on 2026-08-20.

## 0.8 Risks

| ID | Risk | Severity | Mitigation |
|---|---|---|---|
| **D1** | **Fabricated labels.** Bulk-importing the workbook as accepts, or a UI that makes reject-without-thought the fast path, poisons Part 4 at the source. | **High** | OQ-F imports as unreviewed; R0.7 makes the reason mandatory; V0-7 watches the reason distribution for degenerate patterns |
| **D2** | **Review fatigue.** 20 decisions a day is real work, and a tired reviewer produces noisy labels — which is worse than no labels. | **High** | R0.11 makes 20 a ceiling; OQ-E keeps it off the Gate 1 surface; Part 4 must weight recent labels and watch for reviewer drift |
| **D3** | Sourcing exhaustion inside weeks (OQ-B) | Medium | Named up front as a finding rather than a failure; weekly achieved-rate report |
| **D4** | Contact fields largely `unknown` until Part 3, making the card thin | Medium | V0-6 measures it; the honest response is to resequence Part 3, not to guess |
| **D5** | A generated pain hook (OQ-A, option c) is copied verbatim into a real email | Medium | Only if OQ-A resolves to (c): the field is stored and displayed as `draft`, is excluded from packet assembly entirely, and never enters `outreach_packets` |
| **D6** | R20 — crafted text on a discovered company's site, displayed to the operator | Low | H1 bounding and H2 hardening at the write boundary, unchanged; source URL always shown |

</part_0>

---

# Part 1 — News observation (zero-LLM)

<part_1>

> **Rev 2 scope change — this part now covers the discovery pool as well as
> targets.** Everything below is rev 1 text and its rules, outcomes, `R1.x`
> numbering and verification steps are unchanged. Two things widen:
>
> - **Where it runs.** Rev 1 profiled every target not `archived` or `dropped`.
>   Rev 2 adds every `outreach_discoveries` row with `review_decision = 'accept'`.
>   An accepted firm with no trigger yet (R0.3) is precisely the row that most
>   needs watching, because a news observation is what promotes it to a target.
>   `profilable_targets()` therefore reads both tables.
> - **What it feeds.** Part 1 supplies **field 11, Observed Signal**, of the §0.3
>   card. Until Part 1 runs, that field reads `none observed yet` — which is why
>   §5 moves Part 1 alongside Part 0 rather than after it.
>
> The graph-node rule (R1.6) applies to pool rows too: an `Organization` node keyed
> on `company_domain` is created at discovery, not at promotion, so the news
> observed while a firm sits in the pool is already attached when it becomes a
> target.


## 1.1 Outcome (S1)

When Part 1 is done, all of the following are observably true:

1. **Every active target THAT HAS NEWS has a typed `Organization` node in the
   graph, and `cognee_node_id` holds that node's id.** The node is created on the
   first observed news item (`profile.py`), so a firm whose feed returns nothing
   has no node — and needs none, since the packet traversal would have nothing to
   show for it. Corrected 2026-08-21 after barry-agent's V1 run: 31 of 34 firms
   pinned, the 3 unpinned being targets with empty feeds. The honest invariant is
   *pinned iff observed*, not *all pinned*: `SELECT count(*) FROM outreach_targets
   t WHERE status NOT IN ('archived','dropped') AND cognee_node_id IS NULL AND
   EXISTS (SELECT 1 FROM outreach_watch_signals s WHERE s.target_id = t.id)`
   returns `0`.
2. **A bounded traversal from any target's `cognee_node_id` returns that
   company's observed news items, rendered with a title, a source URL, and a
   date** — through the existing `Scope.TARGET` path, with no LLM and no
   `agent_runs` row.
3. **Each news item observed for a target exists exactly once in
   `outreach_watch_signals`, unclassified, with `detected_at`, `source_kind`,
   `source_url`, and a ≤500-char `excerpt`** — and re-polling the same feed the
   next day creates no duplicate row.
4. **A news item's date never advances.** Unlike an open req, a news event is
   observed once; re-seeing the article does not make it newer. Verifiable by
   polling twice and asserting `detected_at` is unchanged.
5. **The loop makes zero provider calls.** Verifiable:
   `SELECT count(*) FROM agent_runs WHERE agent_name='outreach-profile'` returns
   `0` after any number of runs.
6. **A feed that cannot be read costs nothing but a warning.** No rows written, no
   state changed, the next cycle retries — the same asymmetric failure posture as
   the evidence poller.

## 1.2 Non-goals (S2)

- **No classification.** Part 1 does not decide whether an item is a funding round
  or an expansion. `classified_as` stays NULL; that is Part 2's job and Part 2's
  cost. Writing a keyword classifier here would be a second, worse Trent Crimm.
- **No `outreach_evidence` writes.** Nothing from Part 1 touches scoring, the
  packet arithmetic, or the `ready` guard. An unclassified headline is not a typed
  dated fact and must not be able to act like one.
- **No article fetch, no summarization.** Part 1 reads feed metadata only — title,
  link, publication date, feed description. Fetching the article body (trafilatura)
  and summarizing it (Gemini Flash) would make the loop provider-dependent and is
  deliberately deferred to Part 2, where it runs *after* an item is known to matter.
- **No mode-1 `cognify`.** Typed DataPoints only, local bge embed. Same reason.
- **No new feed types.** RSS/Atom only. No HTML scraping of newsrooms that lack a
  feed — a scraped page has no stable item id, and a drifting `dedup_key` is the
  failure that increment 1b already refused to accept for ATS boards.

## 1.3 Settled rules

<settled>

**R1.1 — `outreach_watch_signals` is the landing table, and its scope widens.**
The table already has exactly the right shape: `detected_at`, `source_kind`,
`source_url`, `excerpt` (CHECK ≤500), `dedup_key`, `UNIQUE (target_id, dedup_key)`,
and an index on `(detected_at) WHERE classified_at IS NULL` that is literally
named "the unclassified queue". It is empty and has no writer.

**Deviation from `35-` §10, stated rather than assumed:** §10 describes Trent
Crimm's inputs as targets on `watchlist` or `lost_to_hire`. Part 1 writes signals
for **every target not `archived` or `dropped`**. The reasoning: §10's scoping is
about which targets *Trent Crimm sweeps weekly*, not about what the table may
hold, and a funding round at an active `candidate` is more actionable than one at
a dormant watchlist entry. The consequence is load-bearing and belongs in Part 2:
**Trent Crimm must take its input from the unclassified queue, not from a target
status filter.**

**R1.2 — Two feeds per target, one derived and one stored.**

- *Google News*, derived from the company name unless overridden:
  `https://news.google.com/rss/search?q=<query>&hl=en-US&gl=US&ceid=US:en`, where
  `<query>` is `outreach_targets.news_query` when set, else the company name in
  double quotes. The RSS endpoint is a published, unauthenticated feed — this is
  not scraping and needs no account.
- *The company's own newsroom/blog feed*, stored per target in
  `outreach_targets.news_feed_url`, null when the company has none.

Both are optional per target; a target with neither is skipped with a warning, the
same way an `UNSUPPORTED` ATS board is reported today.

**R1.3 — `dedup_key` is the canonicalised article URL.** Lowercase the host, strip
the query string and fragment, keep the path case-sensitively (paths are
case-sensitive; this matches `content_item_id`'s existing reasoning). Google News
RSS links are stable per story, and newsroom feeds give real URLs, so one rule
covers both.

*Accepted cost:* a story that surfaces from both Google News and the company's own
newsroom produces two rows. That is honest — they are two independently-sourced
observations and the packet shows provenance for each. Collapsing them on a title
hash would risk merging two genuinely different events, which is the worse error.
Revisit only if the duplicate rate proves annoying in practice (§1.6, open #3).

**R1.4 — The event date is the publication date, and its basis is recorded.**
`detected_at` is when we saw it; the *event* date is what a packet would cite. Feed
entries carry a publication date; when it parses, store it as
`excerpt`-adjacent metadata with `date_basis='published'`. When it does not parse,
fall back to the observation date with `date_basis='discovered'`. **The arithmetic
must never claim a precision it does not have** — this is the same discipline that
made increment 1b capture ATS `posted_at` for display but refuse to use it as
`first_seen_at`.

**R1.5 — Signals are written once and never re-dated.** A new upsert helper does
`ON CONFLICT (target_id, dedup_key) DO NOTHING`, returning whether a row was
inserted. It deliberately does **not** reuse `upsert_evidence`, whose
`DO UPDATE SET last_seen_at = …, closed_at = NULL` semantics are correct for a
persistent *state* (a req that is still open) and wrong for a point-in-time
*event*. Advancing `last_seen_at` on a news item would make a six-month-old
funding article read as `fresh` in `v_outreach_evidence_display` forever — which
is R19's exact failure mode, arriving through a side door.

**R1.6 — News items become typed `ContentItem` nodes edged to the target's
`Organization` node.** This is the mechanism that makes outcome 2 true, and it
needs one additive ontology change:

- `Organization` gains a deterministic id: `uuid5(_ORG_NS, company_domain)`.
  Domain is already the targets table's unique dedup key, so re-runs upsert to one
  node instead of duplicating — the same pattern as `content_item_id`.
- **`ContentItem` gains `about_orgs: list[Organization] = []`** — an additive,
  default-empty edge field. `add_data_points` walks relationship fields into
  nodes and edges, which is what gives `get_neighborhood()` something to walk.

  *Why `ContentItem` and not `Fact`:* `Fact` already has `about_orgs`, so it looks
  like the cheaper choice. But the packet renders traversal results through
  `retrieval.normalize_nodes`, which reads `name`/`title` and
  `text`/`summary`/`description`. `ContentItem` has `title` and `summary` and
  renders as a readable line; `Fact` has `content`, which those lookups do not
  match, and would render as a bare uuid. Adding the edge to `ContentItem` is one
  line and leaves the display path untouched; using `Fact` would mean changing
  `normalize_nodes`, which every packet's background depends on.

**R1.7 — H1 and H2 apply at the write boundary; H5 does not apply here.**
`excerpt` goes through the existing `outreach.clean_field(..., max_chars=500)`,
which is where H1 bounding and H2 unicode-hardening already live. **H5 (input
screening) is a control on text entering a prompt, and Part 1 has no prompt** —
`screening.screen()` is run for its flags and the result logged, but it quarantines
nothing here. H5 is *enforced* at Part 2's prompt boundary. Saying this plainly
matters: an unenforced control that everyone believes is enforced is worse than an
absent one.

**R1.8 — Daily, not 12-hourly, and it ships disabled.** News is not a state that
closes, so the twice-daily cadence the evidence poller needs buys nothing here.
Per the `loops/README.md` convention, the manifest ships `enabled: false` and is
flipped by the operator once feeds are configured for a meaningful number of
targets.

</settled>

## 1.3a Spec-vs-reality corrections (recorded 2026-08-21, before build)

Part 1 was written when migrations stopped at 0016 and the discovery pool did not
exist. Three facts are now different, and one is load-bearing.

**C1 — the migration is 0021, not 0017.** 0017 was never used; 0018–0020 shipped
(pool, segment scores, operator-verified contacts). No behaviour change.

**C2 — `outreach_watch_signals` cannot hold a pool-row signal, and must.** The
table is `target_id NOT NULL REFERENCES outreach_targets`. Rev 2 widened Part 1 to
profile accepted discoveries, and that is not a nice-to-have: an accepted
discovery has no trigger, the ONLY way it gets one is Part 2 classifying a signal
about it (R0.3), and Part 2 reads this table. So a pool firm's signals must land
here or the firm can never be promoted — which is the operator's stated reason for
building Part 1 at all.

**Resolution (R1.9): make the parent polymorphic.** `target_id` becomes nullable,
a nullable `discovery_id` is added, and a CHECK requires **exactly one**. The old
`UNIQUE (target_id, dedup_key)` becomes two partial unique indexes, one per
parent, so dedup holds on both sides. The table is empty, so this is a reshape
with no data migration. This is the standard polymorphic-parent pattern, it is
reversible, and it fabricates nothing — the alternative (profile targets only)
would strand the 20 accepted firms this part exists to unblock, so it is not a
real option rather than a preference.

*Promotion carries the signals across.* When a discovery becomes a target,
`decide`/`promote` re-points its signals from `discovery_id` to the new
`target_id` in the same transaction, so the promoted target sees the history
observed while it was still in the pool, and nothing is orphaned.

**C3 — the graph half runs on barry-agent only.** cognee is not importable on the
build box (no `cognee` module, matching the original increment 1 split). So
outcomes 1 and 2, V1 and V5, `profile_graph.py`'s cognify path, and the
`ContentItem.about_orgs` edge are **code-complete and unit-tested for determinism
here, but their runtime verification is barry-agent's**, exactly as the evidence
poller's cognee path was. The SQL half — the watch-signal queue, the pure feed
functions, dedup, and idempotency — is fully built and verified on the build box.

## 1.4 What gets built

| Artifact | Content |
|---|---|
| **Migration 0017** | `outreach_targets` += `news_feed_url TEXT`, `news_query TEXT`, `news_polled_at TIMESTAMPTZ`. **Must `DROP` and `CREATE` `v_outreach_scored`** — it is `SELECT t.*` and its column list is frozen at creation, so `CREATE OR REPLACE` cannot absorb new base columns. `verify_schema.sql` updated. No `OWNER TO` needed (no new tables), but the view must be re-owned to `barry_agent` after recreation. |
| `agents/_lib/ontology.py` | `ContentItem.about_orgs` (additive, default `[]`). |
| `agents/outreach/profile_graph.py` | `organization_id(domain)`, `build_organization(target)`, `add_organization(target) -> node_id`, `add_news_item(url, title, published, org) -> node_id`. Pure builders separated from graph writes, mirroring `content_graph`. |
| `agents/outreach/news.py` | `google_news_url(company_name, query_override)`, `canonical_url(url)`, `dedup_key(url)`, `parse_published(raw) -> (date, basis)`, `feed_items_for_target(target)`. All pure except the fetch, which is `tartt.fetch.parse_feed` / `list_source_items` reused verbatim. |
| `agents/outreach/profile.py` | The poller: for each target → ensure Organization node + `cognee_node_id` → list both feeds → dedup → insert watch signals → add `ContentItem` nodes edged to the org → advance `news_polled_at`. Per-target try/except, same posture as `evidence.py`. |
| `agents/_lib/outreach.py` | `insert_watch_signal(conn, row) -> bool` (`DO NOTHING`), `set_cognee_node_id(conn, target_id, node_id)`, `profilable_targets(conn)`. |
| `loops/outreach-profile.md` | `schedule: "30 6 * * *"`, `enabled: false`, `command: uv run python -m agents.outreach.profile`. |
| Tests | Pure: URL canonicalisation and dedup stability, Google News URL construction (including quoting and the override), publication-date parsing and the `date_basis` fallback, `Organization`/`ContentItem` id determinism, the edge is present on the built DataPoint. Against live Postgres: insert-once idempotency, `detected_at` unchanged on re-poll, the 500-char excerpt CHECK rejects an over-long excerpt, `cognee_node_id` set exactly once. |

## 1.5 Verification (S3) — named before the build

**Unit** — `uv run pytest -q`, suite green, no new ruff errors beyond the six known.

**Runtime, on barry-agent** (the only account with credentials and cognee):

```
# V1 — every active target is pinned to a graph node
psql aiadaptive_cos -c "SELECT count(*) FILTER (WHERE cognee_node_id IS NULL) AS unpinned FROM outreach_targets WHERE status NOT IN ('archived','dropped');"
# expect: 0

# V2 — signals landed, unclassified, deduped
psql aiadaptive_cos -c "SELECT source_kind, count(*), count(*) FILTER (WHERE classified_at IS NULL) AS unclassified FROM outreach_watch_signals GROUP BY 1;"

# V3 — idempotency: run twice, compare
psql aiadaptive_cos -c "SELECT count(*), max(detected_at) FROM outreach_watch_signals;"
uv run python -m agents.outreach.profile
psql aiadaptive_cos -c "SELECT count(*), max(detected_at) FROM outreach_watch_signals;"
# expect: identical counts and identical max(detected_at)

# V4 — zero provider spend
psql aiadaptive_cos -c "SELECT count(*) FROM agent_runs WHERE agent_name = 'outreach-profile';"
# expect: 0
```

**V5 — the traversal actually returns something renderable.** This is the outcome
that justifies the whole part, and it is the one that could quietly fail: a typed
node with no edges traverses to nothing. Run `Scope.TARGET` recall from a target's
`cognee_node_id` at depth 2 and assert the rendered output contains at least one
news title and its source URL — **not** merely that the call succeeded.

**V6 — precision, judged by the operator, not by a test.** Google News matches on a
name, and a company with a common name will pull in other people's news. After the
first week, the operator reviews the accumulated signals for one broad-named target
and one narrow-named target and records the share that are genuinely about the
company. **Below roughly 70% for a target, that target needs a `news_query`
override** (adding a sector word, a founder name, or the domain). This is a named
acceptance check with a human in it, not a unit test, and it is the reason
`news_query` exists as a column rather than being derived unconditionally.

## 1.5a Build status — Part 1, as of 2026-08-21

**SQL half built and verified on the build box; graph half code-complete, awaiting
barry-agent** (C3). Suite 737 → 750.

| Artifact | State |
|---|---|
| **Migration 0021** | **Applied.** News columns on both tables, `cognee_node_id` on discoveries, polymorphic `outreach_watch_signals` (R1.9), view rebuilt, no drift. |
| `agents/outreach/news.py` | **Built + verified.** Pure feed/URL/dedup/date logic. |
| `agents/outreach/profile_graph.py` | **Built.** Org/ContentItem builders, id determinism and the edge unit-tested; `add_*` need cognee. |
| `agents/_lib/ontology.py` | **`ContentItem.about_orgs` added.** |
| `agents/_lib/outreach.py` | **Built + verified.** `profilable_firms` (34: 14 targets + 20 accepted discoveries), `insert_watch_signal`, `reparent_watch_signals`, `set_cognee_node_id`, `mark_news_polled`. |
| `agents/outreach/profile.py` + `loops/outreach-profile.md` | **Built**, ships disabled. `--no-graph` runs the SQL half on the build box. |
| `agents/_lib/outreach_discovery.py` | **Promotion now reparents signals** (R1.9). |

**Verified live on the build box** (`--no-graph`, one firm, then cleaned to zero):
AIIR's Google News feed returned **10 real items**, all 10 written as new
unclassified signals; **re-run wrote 0 and `detected_at` did not move** (outcomes
3, 4). One item was a namesake — the V6 precision case, in the flesh. The signals
were deleted afterwards so barry-agent verifies from a true zero with a consistent
SQL+graph state.

**Barry-agent still owns** (C3, needs cognee): V1 (`cognee_node_id` set on every
active firm), V5 (the `Scope.TARGET` traversal returns a rendered news title +
URL), and the full both-homes run. The `--no-graph` path is what the build box
can prove; the graph is not.

## 1.6 Open decisions (S4)

| # | Question | Recommendation | Blocking? |
|---|---|---|---|
| 1 | Locale parameters on the Google News feed — `US:en` fixed, or per-target? | Fixed `US:en` for now; revisit if a non-US target lands. | No |
| 2 | Should a target with **no** `careers_url` still be profiled? | Yes — news is the only observation available for the four targets on unsupported ATS platforms, which makes it more valuable there, not less. | No |
| 3 | Cross-feed duplicate collapse (same story from Google News and the newsroom) | Accept duplicates in v1 (R1.3). Reconsider with real counts. | No |
| 4 | Retention of news signals — do old, never-classified signals get pruned? | Undecided. Naming it here so it is not discovered as unbounded growth later. Volume is small; not urgent. | No |

## 1.7 Risks

| ID | Risk | Severity | Mitigation |
|---|---|---|---|
| **P1** | Google News name-collision fills the queue with the wrong company's news | Medium | V6 precision check; `news_query` override; provenance always displayed |
| **P2** | The typed `Organization` node has no edges, so `Scope.TARGET` returns nothing and the packet background stays empty while everything reports success | **High** | **V5 asserts rendered content, not call success.** This is the failure mode most likely to pass a naive check. |
| **P3** | Migration 0017 adds a column without recreating `v_outreach_scored`, so the view silently lacks it | Medium | `verify_schema.sql` already asserts the view exposes every base column — this fails the check rather than drifting |
| **P4** | R20 (crafted text in scraped content, displayed verbatim, copied by a human) now has a second inlet | Low | H1 500-char bound, H2 hardening, source URL always shown — unchanged controls, now covering one more channel |

</part_1>

---

# Part 2 — Classification and promotion to evidence

<part_2>

**Gated on:** Gate 1 having been exercised end-to-end at least once (a target
carded, accepted, sequenced, and a packet assembled). Part 2 spends money per item
and writes rows that scoring consumes; doing that before anyone has seen a packet
is tuning inputs to an untested machine.

`35-` §10 already specifies the classifier itself — weekly Sunday 19:00, one
Haiku call per detected item, forced tool call `{trigger_kind, confidence,
rationale}`, `function_label='outreach_watch'`, ceiling $0.30/day, cards only on a
`watch_trigger` match or `executive_departure`. **That part is not re-specified
here.** What §10 does not cover, and what Part 2 adds, is: where the input comes
from now that Part 1 exists, and what happens to a classified signal afterwards.

## 2.1 Outcome (S1)

1. **The classifier's input is the unclassified queue**, not a target-status
   filter: every `outreach_watch_signals` row with `classified_at IS NULL`, oldest
   first, bounded per run. Verifiable — after a run, `count(*) WHERE classified_at
   IS NULL` has fallen to zero or to exactly the per-run bound's remainder.
2. **A signal classified as one of the eight triggers is promoted into
   `outreach_evidence`** with the matching `fact_kind`, `source_kind='news_rss'`
   or `'newsroom_rss'`, the article URL, the bounded excerpt, and
   **`first_seen_at` = the event's publication date (R1.4), not the classification
   date.** Verifiable: every promoted row's `first_seen_at` equals the source
   signal's recorded publication date, and no promoted row has
   `first_seen_at > detected_at::date`.
3. **A promoted fact is never close-swept.** `close_absent_evidence` is called
   with `fact_kind='open_role'` today; it must stay that way. A funding round does
   not stop being true because it left an RSS feed. Verifiable by a test that runs
   a close sweep and asserts news-sourced rows are untouched.
4. **Classification is idempotent.** Re-running never re-classifies a
   already-classified signal, never double-promotes, and never spends twice on the
   same item. Verifiable: run twice, compare `agent_runs` counts and
   `outreach_evidence` counts.
5. **H5 is enforced here.** A signal whose excerpt trips `screening.screen()` is
   quarantined — marked, logged, and **never placed in the prompt**. Verifiable
   with a synthetic instruction-bearing excerpt that must produce zero
   `agent_runs` rows.
6. **Each target's leadership roster is cached as typed evidence**
   (`fact_kind='leadership_member'`, with a `since` in the payload where the source
   states one), so the two-tab `function_state` diagnostic starts from something
   rather than from nothing.

## 2.2 Non-goals (S2)

- **No automatic status or band changes.** A classified funding round does not
  move a target between statuses. The no-automatic-banding decision in
  `loops/outreach-daily.md` stands, for its original reason: intake only cards
  `candidate`, so an automatic move can trap a target with no way back until Gate 4
  exists.
- **No S4/S5 auto-scoring.** Those are human-judged by design (`35-` §6, "Judged —
  unbuyable"). Evidence informs the judgement; it does not replace it.
- **No new card type.** §10's Task Tinder card is the surface. Part 2 adds no
  Discord surface of its own.
- **No backfill of history.** Only what Part 1 has observed since it started
  running. Buying backfill is Part 3's question.

## 2.3 Settled rules

**R2.1 — Article fetch and summarization happen here, gated on classification.**
An item classified as a trigger is worth `trafilatura.fetch_url` +
`tartt.summarize.summarize` (Gemini Flash, existing label and ceiling); an item
classified `none` is not. This is where the deferred spend from Part 1 lands, and
gating it on classification is what keeps it proportionate.

**R2.2 — The summary enriches the graph node, not the evidence row.** The evidence
row stays typed and bounded (H1). The `ContentItem`'s `summary` field is updated —
its id is deterministic on the URL, so this upserts the existing node.

**R2.3 — `confidence` gates promotion, and the threshold is written down.**
Promote at `confidence >= 0.7`, matching Roy Kent's existing ICP gate so the system
has one number, not several. Below that: classified, stored, not promoted, still
eligible to surface as a card if it matches `watch_trigger`.

**R2.4 — A departure classified from a news source is evidence, not a resolution
of OQ1.** Press releases announce *arrivals* reliably and *departures* selectively.
Part 2 improves the signal; it does not close the open question, and the spec must
not later be read as if it did.

**R2.5 — The canonical publisher URL is the URL of record** (operator decision,
2026-08-27). Part 1 stores what the feed hands it, which for Google News is a
redirect link (`news.google.com/rss/articles/…`), not the publisher's own URL.
That is correct for Part 1 — it reads feed metadata only and never fetches, so it
cannot know the destination without following the redirect. **Part 2 already
fetches the article** (R2.1, trafilatura), and the fetch resolves the redirect to
the publisher's canonical URL. So Part 2 is where the canonical URL becomes known,
and it must record it:

- The `ContentItem` node's `url` is **updated to the canonical publisher URL** on
  classification. Its id is `uuid5` of the *original observed* URL and does not
  change, so the update is an in-place field write, not a new node.
- A promoted `outreach_evidence` row carries the canonical URL as its
  `source_url`, so the packet cites the publisher, not a Google redirect.

**One open decision this forces, and it is load-bearing** — added as open #4
below. The dedup key. Part 1 dedups on the observed (Google News) URL, which is
stable and fetch-free. If two different Google News links resolve to the same
publisher article, Part 1 sees two signals; only Part 2, post-fetch, can tell they
are one. Re-keying dedup on the canonical URL would collapse them but means the
identity of a signal changes after it is stored, which the whole first-seen
discipline is wary of. Recommendation: **keep the observed URL as the dedup key
(identity is fixed at observation) and treat the canonical URL as a display/record
field**, accepting the rare double as the honest cost — the same trade R1.3 already
made for the two-feed overlap. Decide at Part 2 build.

## 2.4 Verification (S3)## 2.4 Verification (S3)

Runtime, barry-agent, after one scheduled run:

```
# queue drained
psql aiadaptive_cos -c "SELECT count(*) FILTER (WHERE classified_at IS NULL) AS pending, count(*) FILTER (WHERE classified_at IS NOT NULL) AS classified FROM outreach_watch_signals;"

# promotions carry the EVENT date, not today's date
psql aiadaptive_cos -c "SELECT fact_kind, source_kind, first_seen_at, last_seen_at FROM outreach_evidence WHERE source_kind IN ('news_rss','newsroom_rss') ORDER BY first_seen_at DESC LIMIT 20;"

# spend is bounded and attributed
psql aiadaptive_cos -c "SELECT agent_name, function_label, count(*), round(sum(usd_cost),4) FROM agent_runs WHERE function_label='outreach_watch' AND started_at > current_date - 7 GROUP BY 1,2;"
```

Plus: the idempotency re-run (outcome 4), the close-sweep test (outcome 3), and the
H5 quarantine test (outcome 5) as unit/integration tests, not eyeball checks.

## 2.5 Open decisions (S4)

| # | Question | Blocking? |
|---|---|---|
| 1 | Per-run item cap — how many classifications per weekly run before the $0.30/day ceiling is the binding constraint? Needs a real queue depth from Part 1 to size. | Yes, at build |
| 2 | Does an unclassifiable signal ever get retried, or is `classified_as='none'` terminal? | Yes, at build |
| 3 | Leadership roster source — the company's own team/about page has no feed. Manual entry, or a bounded one-page fetch? A page fetch reintroduces the stable-id problem R1.2 avoids. | Yes, for outcome 6 |
| 4 | **Dedup identity vs canonical URL** (R2.5). Keep the observed Google News URL as the dedup key and store canonical as a record field (recommended — identity fixed at observation), or re-key on canonical post-fetch to collapse duplicate redirects? | Yes, at build |

</part_2>

---

# Part 3 — Contact and firmographic enrichment

<part_3>

> **Rev 2 scope change — this part is now on the critical path for the card.**
> Rev 1 treated it as optional enrichment gated on R21. That gate is unchanged and
> still binds. What changed is the consequence of it not being built: **fields 6
> (Contact Email above `Inferred`), 7 (`Public` confidence), and 9 (Contact
> LinkedIn) of the §0.3 card cannot be populated without it.** Part 0 can establish
> a contact name, title, and a pattern-inferred or general-inbox address from a
> company's own site; it cannot verify an address.
>
> **V0-6 is the decision point.** It measures the non-null rate of every card field
> after one week. If the contact fields are mostly `unknown`, the answer is to move
> Part 3 forward — not to lower the evidence bar in Part 0. R21's retention check
> still happens before any integration code, per `35-` §6.


**Gated on:** `35-` §16 decision #3 (enrichment stack) and #4 (**R21** — provider
retention terms and a deletion workflow for personal data in a permanent store
with no erasure path and no RLS). R21 is Open, and §6 is explicit that the check
happens **before** building the integration, not after.

**Note that Part 1 already puts exec names into the permanent store** by way of
news headlines. R21's scope is therefore slightly wider than it was when written;
that is worth saying out loud rather than discovering during the check.

## 3.1 Outcome (S1)

1. **Each target carries the firmographic spine as typed columns**, populated
   where the provider returns them and null where it does not: `sector` (0 of 14
   populated today), `headcount`, `headcount_asof`, `ownership_type`
   (`vc_backed` | `pe_backed` | `bootstrapped` | `founder_owned` | `public`),
   `total_raised_usd`, `last_round_at`, `last_round_type`, `lead_investor`,
   `founded_year`, `hq_location`.
2. **Attribute history is readable without a new table.** Because these are
   columns on `outreach_targets`, the existing `outreach_log_event()` audit trigger
   already records `{col: {from, to}}` with a timestamp and `session_user` on every
   change. Verifiable: change a headcount, then
   `SELECT changed, occurred_at FROM outreach_events WHERE entity_table='outreach_targets' AND entity_id=<id> ORDER BY occurred_at DESC`
   shows the before/after.
3. **Funding events land as `outreach_evidence`, not just as columns.** A round is
   a dated event with a source; the column is the current-state convenience, the
   evidence row is the observation. `stated_use_of_funds` — the press-release
   language the T12/T21 templates lean on — is captured as its own fact where the
   source provides it.
4. **No provider data is stored beyond what its terms permit**, and a documented
   deletion path exists for contact-level personal data.

## 3.2 Non-goals (S2)

- **No ICP scoring model here.** Scoring moved to **Part 4** in rev 2; Part 3
  supplies it with fields, and still performs no culling of its own.
- **No profile snapshot table.** The audit log is the cheap route and it is
  sufficient for reconstructing what a field was on a given date. If the deferred
  ICP work later needs point-in-time joins at scale, that is the moment to design
  the proper thing — not now, on speculation.
- **No JSONB blob column for firmographics.** Typed columns are what make outcome
  2 work: the audit trigger diffs *columns*, so a blob would log whole-object
  before/after and the history would be unreadable. This constraint is the reason
  for the column choice, and it should not be "simplified" later without noticing.
- **No Glassdoor/Comparably automation.** See §3.4.

## 3.3 Verify before writing code

Three probes, in the `PRD-outreach-gmail-channel.md` V1–V3 spirit — run against a
real account, before any integration code exists:

- **V1 — Terms.** Read the chosen provider's retention and caching terms. Confirm
  in writing whether storing returned fields in a permanent system of record is
  permitted, and for how long. **A "no" here changes the design, not the
  timeline** — return to the operator rather than storing anyway.
- **V2 — Coverage.** Run 5 of the 14 real targets through the provider's trial and
  count how many of the §3.1 fields come back non-null. `35-` §6 already warns
  that Apollo returns no job postings and no executive changes; the open question
  is whether it returns enough of the *rest* to be worth an account at this volume.
- **V3 — Backfill value.** For 3 targets with reqs already being polled, ask
  TheirStack's free tier for posting first-seen dates and compare against the
  dates the poller has observed since 2026-08-13. This measures the one thing
  backfill is actually for. If the provider's dates disagree with our own
  observations, **our observation wins** and the value of backfill is only for reqs
  that predate our polling.

## 3.4 Glassdoor / Comparably — settled as manual

No public API (Glassdoor retired theirs), both prohibit scraping, and the data is
lagging and self-selected. **The signal worth having is a trend, not a rating:**
CEO-approval direction, review-volume spikes, and reviews mentioning leadership
churn. That is a five-minute read during the two-tab `function_state` diagnostic
that is already a Tier-3 human activity — so it enters as an operator note, on the
same cadence as the S4/S5 judgement (30 days, per `v_outreach_scored.signals_stale`).

Building an automated path here would mean either an account-risking scraper or a
paid API that does not exist. Recording it as *deliberately manual* is the
decision, not a gap.

## 3.5 Open decisions (S4)

| # | Question | Blocking? |
|---|---|---|
| 1 | Provider — Apollo vs a cheaper contacts API (`35-` §16 #3, unchanged) | Yes |
| 2 | TheirStack free tier vs paid, decided by V3's result | Yes |
| 3 | Deletion workflow shape — a `cli/forget_contact.py` covering both Postgres and the graph, or contact data confined to Postgres so the graph never holds it? The second is simpler and probably right. | Yes (R21) |
| 4 | Does `ownership_type` need a CHECK constraint, given NocoDB issues raw UPDATEs? Probably yes, on the 0013 precedent that every invariant is a DB constraint. | At build |

</part_3>

---

# Part 4 — The selection feedback loop

<part_4>

**Status change, recorded rather than overwritten.** Rev 1 §4 deferred this on
2026-08-17. The operator un-deferred it on 2026-08-19. Rev 1's reasoning is kept
below in §4.6 because part of it still holds: what was premature was learning from
**outcomes**, and that is still premature. Learning from **selection** is not, and
that is what this part does.

## 4.1 Outcome (S1)

1. **Accept and reject rates are reported per segment, per sourcing channel, per
   headcount band, per pain layer, and per country**, each with its sample size,
   and each refusing to report a rate below a stated minimum sample (R4.2).
2. **The ICP Fit score has a v2 that uses those rates**, is versioned beside v1
   rather than replacing it, and stamps `icp_model_version` on every score it
   produces. Verifiable: two model versions coexist and any historical score can be
   attributed to the model that produced it.
3. **Every score explains itself.** A candidate's ICP Fit renders with the factors
   that produced it and their contribution. Verifiable by eye on any card, and by a
   test asserting the explanation's components sum to the score.
4. **The loop proposes; the operator disposes.** A proposed change to segment
   weights is written as an inactive model version with a diff against the active
   one. **It does not take effect until the operator activates it.** Verifiable:
   the active version changes only by an explicit action, recorded in
   `outreach_events`.
5. **Under-sampled segments keep getting surfaced** (R4.3). Verifiable: over any
   28-day window, no segment with fewer than the minimum sample receives zero
   surfaced candidates.
6. **No provider spend.** This is arithmetic over labels. Verifiable: zero
   `agent_runs` rows.

## 4.2 Non-goals (S2)

- **No automatic culling**, and no automatic change to what gets sourced. The loop
  reweights ranking; it never silently stops looking at a segment. The reasoning is
  the one `loops/outreach-daily.md` already gives for refusing automatic banding: an
  irreversible narrowing driven by an early, noisy number is expensive to undo and
  invisible once done.
- **No LLM, and no opaque model.** See R4.1.
- **No outcome learning yet** (§4.6).
- **No interaction effects in v2.** Single-factor rates only, until the sample
  supports more (R4.2).

## 4.3 Settled rules

**R4.1 — Interpretable arithmetic, not a model the operator cannot read.** The
object being refined is the workbook's own weighted-criteria table, which the
operator authored and can defend. A learned replacement he cannot inspect is worse
than the hand-set numbers even if it scores better, because he cannot tell when it
has gone wrong. v2 is accept-rate-per-factor with Laplace smoothing toward the v1
prior — one screen of arithmetic, fully inspectable.

**R4.2 — A rate is not reported below its minimum sample.** With roughly 100
labels a week across six segments, single-factor rates settle in a few weeks;
interaction effects (segment × pain layer × headcount) need far more data than this
funnel will produce for months. **Minimum 30 labelled decisions per cell to report
a rate, and v2 uses single factors only.** Reporting `0.0` accept rate from three
rejections would kill a segment on noise, and R4.3 exists because that failure is
the expensive one.

**R4.3 — An exploration reserve, fixed at a share of the daily 20.** Pure
exploitation is self-fulfilling: the three new segments start with no history, rank
last, are never surfaced, and never acquire the labels that would prove their
worth. **A fixed share of each day's window is reserved for under-sampled segments
and channels**, and stays reserved until every segment reaches R4.2's minimum
sample. The share is 25% (OQ-G, settled 2026-08-20) and is **already built in Part
0** (R0.17 reserve + R0.18 unscored bucket), so Part 4 verifies outcome 5 rather
than building it. This rule is the difference between market
intelligence and a machine that confirms the operator's starting assumptions — so if
the reserve is dropped, §4.6's caveat about optimising toward current taste becomes
the governing risk rather than a footnote.

**R4.4 — Reject reasons route to different knobs, and are not pooled into one
score.** `wrong_segment` is evidence about sourcing queries; `too_small` about the
headcount filter; `poor_contact_path` about Part 3, not about the firm at all;
`geography` about the country list. Pooling them into a single accept-rate discards
the information R0.7 was collected for. **A candidate rejected for
`poor_contact_path` is not evidence that its segment is bad.**

**R4.5 — Labels are timestamped and recent labels weigh more, with an 8-week
half-life** (OQ-I, 2026-08-19). The operator's own criteria will move as he learns;
that is the stated purpose of the exercise. A label from week one should not outvote
a label from week ten. The half-life is stated in the model version, so a change to
it is visible as a new version rather than as an unexplained shift in scores.

**R4.6 — The workbook stays the system of record for the market model.** Part 4
writes proposed weights back in the workbook's own vocabulary — the six criteria,
the weighted score, the suggested outreach weight — so the operator compares like
with like against the sheet he already trusts.

## 4.4 What gets built

> **Migration renumber (2026-08-27): the ICP-models table is 0022, not 0019 —
> 0019 shipped as `outreach_segment_scores` (R0.20). The exploration reserve
> (R4.3, outcome 5) is already built in Part 0, so it is not rebuilt here.**

| Artifact | Content |
|---|---|
| **Migration 0022** | `outreach_icp_models` — `version`, `active BOOL`, `factors JSONB`, `created_at`, `activated_at`, `activated_by`, `notes`. Partial unique index enforcing at most one `active`. Audit trigger attached. Owner `barry_agent`. |
| `agents/outreach/icp.py` | v2 scorer beside v1; `explain(candidate) -> list[(factor, contribution)]`. |
| `agents/outreach/learn.py` | Rate computation with smoothing, minimum-sample gating, recency weighting, and proposed-version writing. |
| `cli/icp_model.py` | `--report` (rates with sample sizes), `--propose`, `--diff`, `--activate`. Activation is a deliberate operator command, per outcome 4. |
| `loops/outreach-rescore.md` | Extended, not duplicated — the weekly Sunday sweep already exists as a spec and is the natural home for recomputing rates. |
| Tests | Minimum-sample gating refuses to report; smoothing pulls a 3-sample cell toward the prior; recency weighting; the exploration reserve surfaces an under-sampled segment; explanation components sum to the score; activation is required for a proposal to take effect. |

## 4.4a Build status — Part 4, as of 2026-08-27

**Built and verified on the build box** (no LLM, no cognee — pure arithmetic over
labels). Suite 760 → 774.

| Artifact | State |
|---|---|
| **Migration 0022** | **Applied.** `outreach_icp_models` (versioned, one-active partial-unique, audited, owner `barry_agent`), v1 baseline seeded active. Also repaired a latent 0019 bug: `outreach_log_event()` hardcoded `NEW.id` and failed on TEXT-keyed tables (`outreach_segment_scores`, this one); `entity_id` is now nullable and read from the row JSON. |
| `agents/outreach/learn.py` | **Built + verified live.** Recency-weighted, reason-routed accept rates with Laplace smoothing and `MIN_SAMPLE` gating; headcount buckets to ICP bands; pain_layer report-only (R0.14). |
| `agents/outreach/icp.py` | **`score_with_model` added.** v2 nudges v1 by at most `MODEL_MAX_SHIFT`; `model=None` and an unlearned value are exactly v1. |
| `cli/icp_model.py` | **Built + verified.** `--report / --propose / --diff / --activate`; propose refuses when no cell clears the minimum; activate is one-active and audited. |
| `agents/outreach/discover.py` | **Wired.** New scores read the active model, so an activated v2 takes effect with no code change; v1 baseline = no change. |

**Measured on the 24 live labels:** nothing is reportable — every cell is below
the 30-label minimum, so `--propose` correctly refuses. The loop is inert until
the labels accrue, which is R4.2 working, not a gap. **V4-1** (the held-out check
that v2 beats v1 before it can win) waits on real reportable rates, i.e. more
labels — the one piece that cannot be closed until the funnel runs.

## 4.5 Verification (S3)## 4.5 Verification (S3)

```
# rates with sample sizes, and nothing reported below the minimum
uv run python -m cli.icp_model --report

# a proposal exists and is inactive until activated
psql aiadaptive_cos -c "SELECT version, active, created_at, activated_at FROM outreach_icp_models ORDER BY created_at;"

# exploration reserve honoured over 28 days
psql aiadaptive_cos -c "SELECT segment, count(*) FILTER (WHERE surfaced_at > CURRENT_DATE - 28) FROM outreach_discoveries GROUP BY 1;"
# expect: every segment non-zero

# no spend
psql aiadaptive_cos -c "SELECT count(*) FROM agent_runs WHERE agent_name IN ('outreach-discover','outreach-learn');"
# expect: 0
```

**V4-1 — the loop beats the prior, measured rather than assumed.** Hold out the
most recent 20% of labels, score them with v1 and with v2, and compare accept-rate
lift in the top quintile. **If v2 does not beat v1, v1 stays active.** A feedback
loop that is never allowed to fail this test is decoration.

## 4.6 What stays deferred, and why that part of rev 1 was right

Rev 1 deferred this work on the grounds that *14 targets, 0 touches, 0 replies*
gives a fit model nothing to learn from. **For outcome learning that is still
exactly true**, and rev 2 does not change it: no reply has ever been received, so
nothing here can learn what converts to a conversation, a call, or revenue.

What rev 2 changes is the availability of a different, weaker, much faster signal:
the operator's own judgement, at roughly 100 decisions a week. That is enough to
learn **what he is willing to pursue**. It is not enough, and will not be for
months, to learn **what pays**. Those are different questions and the second is the
one that matters commercially.

**The honest consequence:** Part 4 optimises the funnel toward the operator's
current taste. If that taste is wrong about which segment pays, the loop will make
the system efficiently wrong, and faster. The correction is outcome data, which
arrives only after the first sequences run — so **the exploration reserve (R4.3) is
not a nicety, it is the thing that keeps the option open** until replies exist to
learn from. Revisit this section at 10 completed sequences, alongside E1.

## 4.7 Open decisions (S4)

| # | Question | State |
|---|---|---|
| **OQ-G** | What share of the daily 20 is the exploration reserve? | **Open.** Explained 2026-08-19; awaiting a share. My recommendation stands at 25% (5 of 20) while three of six segments have no history, stepping down as R4.2's minimum sample is reached. |
| **OQ-H** | Does an operator `defer` count as a weak reject? | **Settled 2026-08-19: no label.** A deferral usually means a missing field, not a judgement about the firm. |
| **OQ-I** | Recency half-life for R4.5 | **Settled 2026-08-19: 8 weeks.** |
| **OQ-J** | Are proposed weights written back to the workbook automatically? | **Settled 2026-08-19: reported for approval**, never written automatically. The workbook is the operator's and is hand-edited; an automatic write invites a silent conflict. |

</part_4>

---

# 5. Build order and dependencies

```
Part 0  ─────────────────────────────────►  no dependencies. Start now.
   │        (discovery, verification, the 13-field card, Gate 0)
   │
   ├──► Part 1 runs ALONGSIDE, not after ─►  no dependencies
   │        (field 11 is blank until it does; the pool needs news too)
   │
   │  (≈100 labels, so ≈1 week of Part 0 reviewing)
   ▼
Part 4  ─────────────────────────────────►  + labels from Part 0
   │        (rates, ICP v2, exploration reserve)
   │
   │  (Gate 1 exercised once)
   ▼
Part 2  ─────────────────────────────────►  + Gate 1 exercised once
            (classification; also promotes pool rows on a real trigger, R0.3)

Part 3  ─────────────────────────────────►  independent; + §16 #3, #4 (R21), V1–V3
            (contact email + confidence: fields 6, 9 stay `unknown` until this)
```

**Three sequencing points that changed in rev 2:**

1. **Part 1 moves from "first" to "alongside Part 0".** Rev 1 put it first because
   observed data accrues only forward — still true, and now it applies to the pool
   as well as to targets. But field 11 of the card is Part 1's output, so running
   Part 0 alone ships a card with a permanently blank Observed Signal.
2. **Part 4 moves ahead of Parts 2 and 3.** It needs only labels, which Part 0
   produces within a week, and it is the part the revision is actually for.
3. **Part 3 becomes more urgent than rev 1 assumed**, because two of the thirteen
   requested fields (contact email above `Inferred`, contact LinkedIn) cannot be
   populated without it. V0-6 measures how thin the card is without it; **if the
   measurement is bad, resequence Part 3 forward rather than guessing at contacts.**
   R21 still gates it, and that gate does not move.

---

# 6. Relationship to existing specs (S5)

Everything referenced here exists in the repo today.

| This spec | Defers to | For |
|---|---|---|
| Part 0 | `35-` §5, §8 | The intake gate and the capacity ceiling — Gate 0 sits **above** both and changes neither |
| Part 0 | `35-` §3 | The freshness discipline, applied to verification evidence (R0.5) |
| Part 0 | `loops/outreach-daily.md` | The reason a decision without a recorded reason is worthless later (R0.7) |
| Part 0 | migration 0014 | The precedent for CHECK-pinning an enumerated vocabulary (R0.1) |
| Part 1 | `35-` §11 (H1–H7), migration 0013 | Hardening controls and `outreach_watch_signals` DDL — both already built |
| Part 1 | `agents/tartt/fetch.py`, `content_graph.py` | Feed parsing and the typed-DataPoint pattern — reused, not reimplemented |
| Part 2 | `35-` §10 | The classifier's cadence, prompt shape, ceiling, and card rules — **not restated here** |
| Part 2 | `loops/outreach-daily.md` | The no-automatic-banding decision, which Part 2 does not overturn |
| Part 3 | `35-` §6, §16 #3/#4, R21 | Provider recommendations and the retention gate |
| Part 4 | `loops/outreach-rescore.md` | The weekly sweep this extends rather than duplicates |
| Part 4 | `35-` §8 (E1) | The precedent for running a numbered experiment with a stated falsification condition |
| All | `35-` §7 | Packet assembly and `Scope.TARGET` background traversal — the consumer of all of this |

**Specs this revision requires changing, listed so the change is deliberate:**

| Spec | Change | Why |
|---|---|---|
| `35-` §7 | **Amend the no-generation rule — required, OQ-A settled 2026-08-19.** The amendment must say that generated text is permitted on an operator-facing decision surface and remains forbidden in anything a recipient sees. **It lands before R0.12 ships, not after.** | A settled rule that gets quietly bent is worse than one that gets explicitly amended |
| `35-` §9 | Add Gate 0 to the surfaces table, and record OQ-E's answer about which surface carries it | §9 currently lists five surfaces and none of them is a triage queue |
| `35-` §15 / §16 | Add the six-segment taxonomy; note that the ICP feedback loop is no longer an open decision | S6 staleness — §15 already carried a shipped item as a build step once |
| `loops/outreach-rescore.md` | Extend with Part 4's rate recomputation | Written 2026-08-17, spec-only, and this is its natural second job |

**Not covered anywhere, and still not:** OQ1 (departure detection) remains open.
Part 2 improves the proxy; it does not resolve the question.
