# Architecture Evaluation — Security, Coherence, Complexity

<doc:meta>
  <doc:type>review — full-folder evaluation</doc:type>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:compiled_at>2026-08-08</doc:compiled_at>
  <doc:scope>All numbered docs 00–90, 35/36/37 (Track O), PRD-b2, PRD-b3, PRD-phase-4-discovery, decision log</doc:scope>
  <doc:method>Document review only. No code was audited; findings about code paths are inferences from the specs and are marked as such.</doc:method>
</doc:meta>

## Verdict, in four sentences

The trust-boundary skeleton (B1–B4) is better than most production systems this
size, and the decision-log discipline is the best thing in the repo. The security
gaps that exist are mostly *unspecified details* inside good boundaries — replay
protection, identity checks on approvals, retrieval-scope enforcement — each
cheap to close now and expensive to retrofit. The biggest structural problem is
not security but **staleness skew**: `40-action-layer.md` still describes the
pre-pivot world, and the two newest ingest specs don't schedule the hardening
controls that 35- §11 declares shared. The biggest complexity problem is the
content pipeline: five agents and a seven-state machine to produce ~5 drafts a
week for one approver.

---

## 1. Security findings

Ranked by (consequence × likelihood) ÷ cost-to-fix. Severity is about *this*
system — a solo operator holding client-confidential data — not abstract CVSS.

<finding id="SEC-1" severity="high" effort="hours">

### The gateway HMAC has no replay protection, no rate limit, and no size cap

`PRD-b3-tunnel.md` specifies a shared-secret HMAC signature header — and nothing
else. As specified:

- **Replay**: a captured signed request is valid forever. Anyone who ever sees
  one signed `POST /ingest` (proxy logs, the WordPress host, a network path) can
  re-send it, or a *flood* of it, indefinitely. Each replay is a cognify run
  (~$0.005) and a graph write. The soft breaker caps the daily damage but the
  graph poisoning persists — dedup only stops *exact* re-posts, so a replayed
  request with a mutated byte lands as new content.
- **Rate**: no per-route limit is specified anywhere. The daily spend ceiling is
  the only backstop, and it is post-hoc.
- **Size**: no maximum body length. One pasted 500KB "note" is one giant cognify
  fan-out inside a *single* invocation — the soft breaker blocks the *next*
  invocation, so the overshoot happens entirely within the guardrail.

**Fix (all three are small):** sign `timestamp + nonce + body-hash`; reject
timestamps older than 5 minutes; keep a nonce cache for the window. Add
Cloudflare WAF rate rules per route (free tier covers this). Enforce
`max_content_length` (e.g. 32KB per note) in FastAPI *before* auth even runs.
Add `gateway-hmac-secret` rotation to the quarterly checklist.

</finding>

<finding id="SEC-2" severity="high" effort="hours">

### B2 approval clicks have no identity check and one click ships outbound

`PRD-b2-approval-gate.md` guards idempotency (DB `WHERE status='pending'`) but
never names a **user-ID allowlist** on the button handlers. The stated model is
"private guild, one operator" — but the guard is then the guild's invite
hygiene, not code. A second account in the guild (misconfigured invite, a future
shared channel, a compromised operator session) can approve outbound email and
publishing. Note also that **Edit dispatches immediately** ("approve-with-
changes") — the edited payload executes without a second look.

Discord account compromise is the single cheapest way to defeat this entire
architecture: every other boundary (B1, B3, B4) funnels to a button click.

**Fix:** (1) one line in every handler: `interaction.user.id == OPERATOR_ID` or
silently ignore. (2) For high-consequence `item_type`s (email to a client,
publish), require a **typed confirmation phrase in a modal** rather than a bare
click — this also defeats fat-thumb approvals on mobile. (3) Consider 2FA on the
Discord account an *architectural requirement*, documented in 25-, not personal
hygiene.

</finding>

<finding id="SEC-3" severity="high" effort="half a day">

### Retrieval scoping is a convention, not a mechanism

B1's teeth depend on agents *remembering* to scope cognee searches: playbook
retrieval "scoped to the trusted dataset only," outreach traversal "scoped to
permitted datasets." Nothing enforces this. One forgotten parameter in one agent
mixes untrusted capture content into trusted-playbook retrieval, and nothing
detects it — the failure is silent and looks like a good answer.

The repo already solved this exact problem for LLM calls: *agents do not import
provider SDKs; everything goes through the cost helper.* Retrieval deserves the
same rule.

**Fix:** a single `_lib/retrieval.py` wrapper that takes an explicit dataset
enum and **defaults closed** (untrusted-only); direct `cognee.search` calls
outside the wrapper forbidden by a CI grep test, exactly like the SDK-import
rule. Half a day, and B1 becomes checkable instead of aspirational.

</finding>

<finding id="SEC-4" severity="medium-high" effort="config">

### The UI surface contradicts the confidentiality premise — use Tailscale for it

The stated privacy posture is "client transcript text never leaves the box" and
"the brain never leaves the box." But NocoDB behind a Cloudflare Tunnel means
**Cloudflare terminates TLS and sees every prospect record, packet, and grid
edit in plaintext** at their edge. That's an acceptable trade for the WordPress
webhook (low-sensitivity payloads, needs a public hostname for an arbitrary
third-party caller). It is a poor trade for the *operator-only* UI.

**Fix:** split the exposure by audience, which the docs already half-do:
**Cloudflare Tunnel for machine callers** (webhook, ingest) · **Tailscale Serve
for every human surface** (NocoDB, future admin pages). Tailscale is end-to-end
WireGuard — no third party sees UI traffic — and it's already the named
fallback. This also deletes most of R4's residual risk, since NocoDB stops
having a public hostname at all.

</finding>

<finding id="SEC-5" severity="medium" effort="hours">

### Backups are unencrypted dumps of everything, on whatever Time Machine touches

Nightly `pg_dump | gzip` of both DBs to `~/agents/backups/nightly`, "Time
Machine picks it up." The dumps contain the entire graph — client-confidential
meeting content included — and the docs never state whether the TM target (NAS?
external disk? cloud?) is encrypted. The backup path is now the softest copy of
the most sensitive data in the system.

**Fix:** pipe through `age` (one recipient key, key in barry-admin's keychain +
a paper copy) before writing; verify the TM target is encrypted; add a restore
drill to the quarterly checklist with a date, not just a command. Also
**auto-purge the BCC mailbox** after ingestion (Track O) so the third-party
mailbox holds ~nothing at steady state.

</finding>

<finding id="SEC-6" severity="medium" effort="scheduling only">

### The shared ingest hardening is specified but scheduled last

H1–H7 (`35-` §11) are explicitly "shared, apply to every channel" — yet they're
scheduled inside Track O (steps 7 and 17), and **`PRD-phase-4-discovery.md`
(the newest ingest spec) doesn't mention screening at all**. Meanwhile the
highest-volume untrusted ingest in the system is Tartt (arbitrary web content,
daily), and the WordPress webhook is a signed-but-compromisable source — WP
compromise is common, and a compromised WP signs valid HMAC requests straight
into Roy Kent's prompt and the graph. Injection there doesn't reach outbound
(B2 holds) but can quietly poison `icp_signals` → Nate Shelley → content topics
— a slow corruption of the intelligence layer with no detector.

**Fix:** pull H2 (unicode/invisible stripping) and H5 (pre-prompt screening)
out of Track O and land them in `_lib/ingest.py` *before* Phase 4 ships. They
are channel-agnostic by design; the scheduling should match.

</finding>

<finding id="SEC-7" severity="medium" effort="process">

### The authoring repo is mounted network-writable

`/Volumes/aiadaptive-cos` — the repo this review was just written into over SMB
from another machine. B4's provenance story ("authored in barry-admin →
committed → pulled") assumes the authoring working tree is only reachable from
barry-admin. A network share reachable from the laptop means any compromise of
the *laptop* can stage edits in the authoring tree. Commit is still the gate,
but unreviewed-working-tree → habitual `git add -A && commit` is exactly how
git-gates rot in practice.

**Fix:** keep the share mounted only during transfer sessions; before any
commit that follows external writes, `git diff` review in barry-admin is the
gate — say so in the checklist. Ensure barry-agent's runtime clone is never the
shared path.

</finding>

<finding id="SEC-8" severity="low" effort="awareness">

### Third-party data custodians are absent from the trust inventory

Granola holds meeting transcripts; Buffer holds drafts; the BCC mailbox holds
outreach bodies; Cloudflare sees webhook payloads; NocoDB (self-hosted, but a
large third-party codebase) holds DB credentials. None appear in any boundary
table. **Fix:** a ten-row "data custodians" table in 25- — what they hold, what
their compromise costs, and which boundary contains it. Half of security is
knowing who you've already trusted.

</finding>

---

## 2. Incoherence findings

Two structural, then errata. The errata are one-line fixes; batch them.

<finding id="COH-1" severity="structural">

### `40-action-layer.md` still describes the pre-pivot system

The July-6 file contradicts the post-pivot docs on at least five load-bearing
points: per-agent Anthropic keys / `KEY_BY_AGENT` (removed — single key + one
per subsystem); the cost helper "enforcing" pre-flight caps and ceilings (G1/G2
removed; soft breaker now); Briefing reading the `facts` table (dropped in
migration 0006); Tartt embedding via Gemini (now local bge, per the 2026-08-03
decision and Phase-4 PRD); per-agent venvs and the `agent` account name (single
uv env; `barry-agent`). Because 20- points to 40- as the authoritative agent
reference, a fresh reader (or a fresh agent session) building from 40- will
rebuild the deprecated design. **This is the highest-value doc fix in the
repo: one refresh pass, ~2 hours.**

</finding>

<finding id="COH-2" severity="structural">

### "W1–W7" means two different things in two files

`26-cognee-migration-plan.md` names its workstreams W1–W7; `90-workflows.md`
names the business workflows W1–W8. "W5" is simultaneously "recall rewrite" and
"daily briefing." Any cross-referencing conversation or agent prompt that says
"W5" is ambiguous. **Fix:** rename the migration workstreams M1–M7 (they're
history now anyway — see COH-3) and reserve W-prefix for workflows permanently.

</finding>

**Errata (one-liners):**

| # | Where | Problem |
|---|-------|---------|
| E1 | `25-`, `26-` headers | Still marked **PROPOSED / pending go/no-go** — the decision log shows the pivot executed, W7 deployed, B2 live. Flip to ADOPTED with an as-built note. |
| E2 | `50-` server layout | `#dashboard` is missing from the channel list, but Higgins (40-) and 80- both post to it. Either add it or fold Higgins into a Monday `#briefing` edition (recommended — one less channel). |
| E3 | `80-` | "Function labels match the four core swarm functions plus two for system work" — now seven labels; sentence contradicts its own table one row later. |
| E4 | `40-` Briefing spec | Inputs don't include the outreach views (`v_outreach_due`, capacity) though 35- §14 has the briefing carrying the outreach line. |
| E5 | `60-` failure mode F5 | Re-embedding remedy names `facts` (dropped) and Gemini embeddings (replaced by local bge). |
| E6 | `35-` calendar write-out | Names no calendar credential; 40-'s credential inventory has no calendar entry. Unspecified integration (also see CPX-3). |
| E7 | Ted cadence vs outreach alerts | "BCC poller silent >2h" alert is checked by a 6-hour Ted cycle — worst-case detection ~8h. Either accept and say so, or have the poller self-report failure to `#system` directly. |
| E8 | `35-` Selector authority | S1 bands declare the migration authoritative; nothing declares `selector.yaml` vs the doc's grid. State it: config wins. |

---

## 3. Unnecessary complexity

<finding id="CPX-1" severity="high-value cut">

### The content pipeline is five agents for five drafts a week

Tartt → Keeley Strategy → Keeley Content → Sam → Keeley Distribution, a
seven-state machine, re-draft cycles with a max-2 loop, latency targets, and an
evaluator (Sam) guarding a gate that a *human already guards*. Sam's entire
output — pass/fail against a rubric — is re-performed by the operator seconds
later in `#approvals`. At n≈5 drafts/week, Sam is a Haiku call, two state
transitions, a JSONB column, and a failure mode (F1) purchased to save the
operator ~30 seconds of reading a draft they must read anyway.

**Recommendation:** collapse triage + draft + self-check into **one Sonnet call**
returning `{verdict, draft, rationale}` — the model triages and self-evaluates
in the same context, cheaper than three calls and one fewer round trip. States:
`discovered → drafted → pending_approval → published/declined`. Keep Keeley
Distribution (deterministic Buffer work).

**Falsifiable re-add condition:** if the operator's rejection rate exceeds 30%
over the first 20 drafts, reinstate a separate evaluation step — that would be
evidence pre-filtering has value the single call isn't providing.

</finding>

<finding id="CPX-2" severity="review-date">

### Cognee's ongoing tax needs a usage-based keep/kill date

The pivot is done and the capability argument (pre-call briefs, graph-grounded
drafting) is real. But the standing costs are real too: every note is LLM spend;
telemetry structurally depends on a pinned litellm callback contract (M1);
recall is nondeterministic; two DBs to back up and restore; a config surface
(`cognee_setup`) that must be re-verified on every upgrade.

**Recommendation:** the architecture's own discipline — "every element traces to
a workflow" — applied with a number. Log recall/traversal invocations (they
already land in `agent_runs`). **Review 2026-11-01: if organic recall usage
averages <10/week, or briefing/drafting quality shows no measurable dependence
on graph grounding, fall back to Option C** (entities + join table, already
scoped at 3–5 days in 26-). Keeping the fallback scoped and the metric defined
makes this a decision, not a drift.

</finding>

<finding id="CPX-3" severity="cut">

### Calendar write-out duplicates the briefing for the cost of an OAuth integration

Track O step 10 writes five dates per target to a calendar — a fourth reminder
surface (briefing, NocoDB due view, Shortcut list, calendar) requiring a Google
OAuth scope that appears in no credential inventory, plus the R11 staleness
liability. The 6am briefing already nudges daily.

**Recommendation:** cut it from Track O. **Falsifiable re-add:** if
touches-on-schedule falls below 95% in the first four weeks without it, add it
back — that would prove the briefing nudge is insufficient.

</finding>

<finding id="CPX-4" severity="watch">

### Four queue-shaped tables with overlapping semantics

`task_candidates` → `tasks` → `follow_ups` (with escalation) plus
`outreach_touches`. The touches table is justified (different grain, different
lifecycle). The `tasks` vs `follow_ups` split — "accepted candidate" vs "open
commitment with escalation" — has never been exercised by a built phase, and
`tasks` carries FKs to both neighbors. **Recommendation:** before Phase 5/7
build these for real, collapse to `task_candidates` + one `commitments` table.
Cheaper now than after two phases write to them.

</finding>

**Endorsed as-is (deliberately not flagged):** DB-polling over LISTEN/NOTIFY in
v1 · the single-gemba approval design · Task Tinder's one-shot card pattern ·
the outreach drain/capacity mechanics · deterministic packet assembly · loop
manifests over per-job plists · Buffer's token-bucket (over-built but harmless).

---

## 4. Performance and token efficiency

<finding id="PERF-1">

**Prompt caching is absent from the architecture.** Briefing (32K in daily),
Higgins (16K weekly), Keeley drafting — all carry stable prefixes: system
prompt, `decisions` rows, style/voice rubrics, ICP criteria. Anthropic prompt
caching discounts cached reads ~90%. Structure every recurring prompt as
`[stable block | dynamic tail]` and mark the boundary. At today's volumes this
is single-digit dollars monthly — but it's also *latency* (cached prefixes skip
prefill), and the pattern must exist before volume does. Add one line to P9 in
10-: "recurring prompts are cache-structured."

</finding>

<finding id="PERF-2">

**Input bounding moved from guardrail to nowhere.** G1's per-run cap died in
the pivot; the soft breaker is post-hoc; and specs like Briefing's "facts from
last 24h, **all**" have no deterministic bound. The replacement for a pre-flight
token cap is **bounded queries**: every prompt-feeding query carries `LIMIT` +
per-field char truncation at the query layer. Same protection, zero runtime
machinery, and it composes with SEC-1's ingest size cap.

</finding>

<finding id="PERF-3">

**Batch Tartt.** One Gemini call per article is the spec. Flash's context fits
10–15 extracts per call with structured output; batching cuts calls ~10×,
shares prompt overhead, and matters doubly while Gemini stays on the free tier
whose *request* caps bite before token caps. One prompt change.

</finding>

<finding id="PERF-4">

**The box has no external witness.** Ted watches the agents, launchd watches
Ted's process — nothing off-box watches the mini. Power cut, disk full, macOS
update reboot-loop: the system dies silently until the operator notices no
briefing came. **Fix:** a dead-man's switch — the briefing loop pings
healthchecks.io (free) on success; missed ping → email/push. Fifteen minutes,
and it's the only alerting in the whole design that survives the box itself
failing. The single most cost-effective reliability item available.

</finding>

---

## 5. UI / UX

<finding id="UX-1">

**The briefing format will overflow Discord.** The 50- template (priorities,
prospects, reading, facts, tinder, system + now outreach) exceeds Discord's
2,000-char message limit on any normal day, which means auto-split messages and
a mangled first impression of the system every single morning. **Fix:** hard
budget — main message ≤1,800 chars, exactly three "do today" items on top,
everything else as thread replies under it. The thread *is* the collapse UI
Discord gives you for free.

</finding>

<finding id="UX-2">

**Recall answers need citations to be trusted.** `GRAPH_COMPLETION` returns a
synthesized string. An answer about an eight-month-old client decision with no
provenance is a liability — one hallucinated recall in a client context destroys
the habit of using it (and the habit is what CPX-2 measures). **Fix:** `/recall`
always renders source refs (node/source_ref list) under the answer. The outreach
packet already follows this rule; make it universal.

</finding>

<finding id="UX-3">

**Outcome capture is load-bearing and voluntary.** KR1 attribution — the number
the whole dashboard reports — depends on the operator remembering `/outcome`.
That's the system's own anti-pattern AP4 ("implicit human action") wearing a
different hat. **Fix:** make the briefing ask. If yesterday had a discovery
call or a signed engagement signal (calendar/Granola already know), the
briefing's last line is a one-button prompt: "Log an outcome for yesterday's
call? ✅/❌". Turn discipline into a click.

</finding>

<finding id="UX-4">

**Name the NocoDB views in the spec.** A bare grid invites fiddling and version
drift between what-you-see and what-matters. The spec should enumerate the four
saved views as deliverables: **Today** (due touches joined to packets),
**Intake** (candidates ≥20), **Watchlist**, **All targets** — plus render
`body_filled` as a markdown long-text field so the packet reads as a document,
not a cell. The 30-second-scan rule in 35- §7 needs a named owner: it's these
views.

</finding>

<finding id="UX-5">

**Tag untrusted-derived text in operator surfaces.** Anything in a briefing or
card that originated from ingest (a capture note, a scraped excerpt, a webhook
field) should render visibly as quoted material with its source — not because
the model needs it (B1 handles that) but because the *operator* is the last
injection target (R20), and provenance styling is the cheap defense. One
rendering convention, applied everywhere.

</finding>

---

## 6. Priority order

**This week (hours each):** SEC-2 allowlist + typed confirm · SEC-1 replay/rate/
size on the gateway PRD before it's built · PERF-4 dead-man's switch · E1–E8
errata batch.

**Before Phase 4 ships:** SEC-6 (H2/H5 into shared ingest) · SEC-3 retrieval
wrapper + CI grep · PERF-2 bounded queries · PERF-3 Tartt batching · COH-1
(40- refresh) · COH-2 (rename migration workstreams).

**Decisions to record, not build:** SEC-4 (Tailscale for human surfaces) ·
SEC-5 (encrypted backups + purge policy) · CPX-1 (collapse content pipeline —
before Phase 8, when the five-agent version would otherwise get built) · CPX-3
(cut calendar write-out) · CPX-2 (cognee review date 2026-11-01 with the
<10-recalls/week metric) · CPX-4 (queue-table merge before Phase 5/7).

**Explicitly fine as designed:** the four-layer split, B1–B4 as a model, the
git-gate, the capacity cap and drain, deterministic packets, selective
vectorization, model routing, the decision log itself.
