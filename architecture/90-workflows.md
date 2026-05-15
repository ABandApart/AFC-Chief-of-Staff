# Workflows

<doc:layer>audience-facing — demonstrates value</doc:layer>
<doc:stability>medium — evolves as workflows are added or refined</doc:stability>
<doc:depends_on>all prior architecture files</doc:depends_on>
<doc:referenced_by>none — this is the outward-facing artifact</doc:referenced_by>

## Purpose

This file is the bridge between the architecture and the business. It defines the seven workflows that the system serves, tied to the north star and KRs, with the demo narrative for prospect and investor conversations.

A workflow is the unit by which the system earns its keep. Every architectural element must be traceable to at least one workflow. Architecture that doesn't serve a workflow is unjustified.

---

## North Star and Test

<north_star_and_test>

**Sustainable long-term contract engagements.**

Three key results:

- **KR1**: New contract engagements per quarter
- **KR2**: Dollar value per engagement
- **KR3**: Project → maintenance conversion rate

**The workflow test**: every workflow must trace to at least one KR. If a workflow can't be tied to KR1, KR2, or KR3, it's overhead and gets cut.

</north_star_and_test>

---

## Workflow 1: Inbound Prospect Intake

<workflow id="W1" name="Inbound prospect intake" kr_alignment="KR1">

**One-line value**: Every prospect from aiAdaptive.co is qualified, scored, and surfaced in the next morning's briefing with a suggested next action.

**Trigger**: A prospect fills out a form on aiAdaptive.co (scorecard, contact, newsletter signup) → WordPress Lead Engine creates a CRM profile.

**Discipline layers**:

| Tier | Work |
|------|------|
| Tier 1 (autonomous) | Capture lead, enrich profile, qualify ICP fit, write `prospects` row, emit `icp_signals` row if pain point present in scorecard answers |
| Tier 2 (prep + decide) | Surface high-fit prospects in briefing with suggested next action (Task Tinder) |
| Tier 3 (human only) | Decide whether to reach out, what to say, how to position |

**Automation flow**:

1. WordPress hook fires on new CRM profile or scorecard submission
2. Outbound webhook to Mac mini receiver
3. Roy Kent reads the profile, scores ICP fit against stored ICP criteria, writes to `prospects` table
4. If scorecard responses include pain-point free text, Roy Kent emits `icp_signals` rows
5. If ICP fit ≥ threshold: a `task_candidates` row is created with suggested next action ("send positioning doc to <name>")
6. Briefing agent reads `prospects` table next morning, surfaces under "New prospects" section
7. Task Tinder surfaces the suggested action with ✅/❌/⏰

**Connections**:

- WordPress Lead Engine (existing) → webhook → Mac mini receiver (new)
- New: `prospects` table (see `30-memory-layer.md`)
- Roy Kent agent (see `40-action-layer.md`)
- Existing: Briefing, Task Tinder, `icp_signals` (W2 substrate)

**Value tied to KRs**:

- **KR1 directly**: top-of-funnel automation. Without this, Lead Engine profiles sit unsurfaced until you manually check them.
- The value is *triage and surfacing*, not capture. WordPress already captures. The system adds a same-day qualification and surfacing layer.

**Demo narrative**: "A prospect fills out my scorecard on aiAdaptive.co. By tomorrow morning, they're on my Discord briefing with an ICP fit score and a suggested next move that's already in my Task Tinder."

</workflow>

---

## Workflow 2: ICP Work Intelligence

<workflow id="W2" name="ICP work intelligence" kr_alignment="KR1, KR2">

**One-line value**: A weekly synthesis of what the ICP is working on and where they hurt, drawn from every signal the system already touches.

**Trigger**: Continuous signal capture (every agent contributes); weekly synthesis cadence.

**Discipline layers**:

| Tier | Work |
|------|------|
| Tier 1 (autonomous) | Every signal-touching agent emits `icp_signals` rows as a side effect; Nate Shelley clusters weekly |
| Tier 2 (prep + decide) | Top themes surface in weekly dashboard and briefing |
| Tier 3 (human only) | Decide which insights to act on — content angle, outreach hook, advisory framework refinement |

**The wide-net pattern**: rather than a dedicated customer-research agent, every agent that touches ICP-adjacent input writes one or more `icp_signals` rows as a side effect of its primary work.

| Source agent | What it emits to icp_signals |
|--------------|------------------------------|
| Tartt | When summarizing an article tagged with ICP-relevant interest signals, extracts mentioned pain points |
| Roy Kent (W1) | Pain points stated in scorecard free-text and contact form messages |
| WordPress scorecard webhook | Open-text scorecard answers about challenges, friction, blockers |
| Meeting processor | ICP-relevant pain mentions in discovery call and client transcripts |
| Fact extraction | ICP-relevant pain mentions in #capture and email |
| Keeley Strategy | Marks which pain points the article it triages addresses |

Then one new agent — **Nate Shelley**, weekly cadence — does the synthesis: reads past 7 days of icp_signals, clusters by embedding similarity, surfaces top 5 themes with frequency and source diversity.

**Connections**:

- New: `icp_signals` table (see `30-memory-layer.md`)
- Existing agents enriched to emit icp_signals as side effect
- New: WordPress scorecard webhook → Mac mini → icp_signals
- Nate Shelley agent (see `40-action-layer.md`)

**Value tied to KRs**:

- **KR2 ($/engagement)**: positioning sharpens when you have current, specific language about ICP pain. "I help L&D consultancies turn expertise into scalable products" becomes "I help L&D consultancies whose biggest current pain is X." Specificity raises perceived value and price.
- **KR1 (new engagements)**: outreach has a current, evidence-based hook.

**Demo narrative**: "I can tell you the top three pain points my ICP talked about this month — across articles, my scorecard responses, discovery calls, and email — with citations. My positioning evolves with the market, weekly."

**Risk to validate after implementation**: If signal volume is sparse (<10/week), Nate Shelley's clustering produces noise. Mitigation: discovery call transcripts are the densest source; phase the workflow to start producing value once meeting processing is in place.

</workflow>

---

## Workflow 3: Content Discovery to Publication

<workflow id="W3" name="Content discovery to publication" kr_alignment="KR1, KR2">

**One-line value**: Substantive content publishes weekly without consuming a workday, with positioning quality gated before it reaches the publish queue.

**Trigger**: Tartt 5am daily.

**Discipline layers**:

| Tier | Work |
|------|------|
| Tier 1 (autonomous) | Discover, summarize, triage, draft, evaluate, schedule, measure engagement |
| Tier 2 (single gemba) | Approve or reject in Discord #approvals |
| Tier 1 (resumes after approval) | Publish via Buffer, capture engagement back into interest_signals (v2) |

**Automation flow**: Tartt → Keeley Strategy → Keeley Content → Sam → #approvals → ✅ → Keeley Distribution → Buffer. Full pipeline detail in `60-content-pipeline.md`.

**Connections**:

- Sources (curated RSS, HN, ArXiv, YouTube, newsletters)
- Brain: content_items, content_pipeline, approval_queue, buffer_posts, interest_signals
- External: Gemini (summarization, embedding), Claude (drafting, eval), Buffer (publish)
- Feeds: icp_signals (Keeley Strategy enrichment, supporting W2)

**Value tied to KRs**:

- **KR1**: published content drives inbound. Content → scorecard → CRM profile → W1 is the funnel.
- **KR2**: consistent, on-message content builds positioning equity. Value is in *cadence*, not the single artifact.

**Demo narrative**: "I publish substantive content every week without it consuming a day of my week. The pipeline surfaces sources, drafts in my voice, evaluates against my positioning, and waits for my approval in Discord."

</workflow>

---

## Workflow 4: Discovery Call Processing

<workflow id="W4" name="Discovery call processing" kr_alignment="KR1, KR3">

**One-line value**: After every discovery call, my next-morning briefing tells me exactly what I committed to and what they said matters most. Nothing slips.

**Trigger**: Granola produces a transcript after a discovery call.

**Discipline layers**:

| Tier | Work |
|------|------|
| Tier 1 (autonomous) | Process transcript; extract facts, follow-ups, decisions, icp_signals |
| Tier 2 (prep + decide) | Task Tinder surfaces proposed actions; you swipe |
| Tier 3 (human only) | Decide what to do with the prospect — proposal, walk away, nurture |

**Automation flow**:

1. Granola transcript appears in monitored folder
2. Meeting processor extracts:
   - Participants → link to `people`
   - Your commitments → `follow_ups` with escalation tracking
   - Their commitments → context recorded
   - Pain points → `icp_signals` (feeds W2)
   - Proposed actions → `task_candidates` (feeds Task Tinder)
   - Decisions → `decisions`
3. If the call participant was an inbound prospect from W1, link the meeting to that `prospects` record
4. Surface in next briefing: "Discovery call yesterday with X — N follow-ups extracted, M tasks proposed, top pain mentioned: <theme>"

**Connections**:

- Granola export folder on Mac mini
- Brain: meeting_transcripts, people, follow_ups, task_candidates, decisions, icp_signals, prospects
- Existing: Briefing, Task Tinder

**Value tied to KRs**:

- **KR1 directly**: discovery call follow-through is the single highest-leverage point for closing engagements. The follow-up gap is where deals die. Automating extraction + escalation closes that gap.
- **KR3**: same workflow processes maintenance check-ins. Patterns like "client said X is going well" or "Y is causing friction" become facts informing renewal conversations.

**Demo narrative**: "After every discovery call, my next-morning briefing tells me exactly what I committed to and what they said matters most. The follow-ups have escalation tracking — if I haven't acted by day 4, the system surfaces a draft message I can send. Nothing slips."

</workflow>

---

## Workflow 5: Daily Briefing

<workflow id="W5" name="Daily briefing" kr_alignment="KR1, KR2, KR3 (indirect)">

**One-line value**: My morning starts with a single Discord message that tells me the three things that matter today.

**Trigger**: Briefing agent 6am daily.

**Discipline layers**:

| Tier | Work |
|------|------|
| Tier 1 (autonomous) | Synthesize overnight: new prospects, ICP signal of the week, content pipeline status, discovery call follow-ups, top reading, new facts |
| Tier 3 (human only) | Read, decide focus, optionally accept tasks via Task Tinder |

**Automation flow**: documented in `40-action-layer.md` Briefing agent spec. Section composition includes contributions from W1 (new prospects), W2 (ICP signal of the week), W3 (content pipeline status), W4 (discovery call follow-ups).

**Connections**: reads everything; writes only to #briefing channel.

**Value tied to KRs**:

- **Indirect across all three KRs**: the briefing is the daily decision-quality lever. Right context → better attention allocation → more progress on what produces engagements.
- The briefing is only as good as its upstream workflows. It's a consolidator, not a standalone value generator.

**Demo narrative**: "My morning starts with a single Discord message: new prospects, top reading, what I owe people from yesterday's calls, this week's ICP signal of the week. No inbox triage, no dashboard hunting."

</workflow>

---

## Workflow 6: Knowledge Capture and Recall

<workflow id="W6" name="Capture and recall" kr_alignment="KR2, KR3">

**One-line value**: Every meeting, captured thought, and decision is searchable. Client-specific knowledge accumulates over the engagement.

**Trigger**: Discord #capture message, meeting transcript arrival, or any agent writing facts.

**Discipline layers**:

| Tier | Work |
|------|------|
| Tier 1 (autonomous) | Capture, extract, embed, store; retrieve on demand via hybrid search |

**Note**: this is more infrastructure than workflow. It's used by W1-W5 and supports W7's reporting. Including it as a workflow makes it visible to the audience; treating it as pure infrastructure understates its long-term value.

**Connections**: facts table, hybrid search function, Discord #capture cog, laptop CLI helpers, fact extraction agent.

**Value tied to KRs**:

- **KR2 ($/engagement)**: clients pay for accumulated judgment. When you can recall specific framework conversations, prior client lessons, and your own past positioning in seconds, the quality of advisory deliverables goes up.
- **KR3 (maintenance conversion)**: client-specific knowledge accumulates over the engagement, becoming the substrate for ongoing advisory value.

**Demo narrative**: "I asked my system 'what did we land on for X client's onboarding workflow' and got the answer in 3 seconds — across 11 meetings, 4 emails, and 6 captured thoughts."

**Honest limitation**: this workflow compounds slowly. Hard to demo in a single moment. The value is visible at the 6-month mark, not week one.

</workflow>

---

## Workflow 7: Weekly Performance Dashboard

<workflow id="W7" name="Weekly performance dashboard" kr_alignment="all KRs (meta)">

**One-line value**: Every Monday I see whether the system is generating new engagements, whether $/engagement is moving, whether projects are converting to maintenance.

**Trigger**: Higgins Mondays 7am.

**Discipline layers**:

| Tier | Work |
|------|------|
| Tier 1 (autonomous) | Compute metrics, generate digest, post to #dashboard |
| Tier 3 (human only) | Decide whether the system is earning its keep |

**Automation flow**: documented in `80-telemetry-layer.md`. Higgins reads agent_runs, content_pipeline, tasks, outcomes, icp_signals, prospects, follow_ups; synthesizes with Claude Sonnet; posts to #dashboard.

**Connections**: cross-cuts every workflow; writes only to #dashboard.

**Value tied to KRs**:

- **Meta-value**: it's the feedback loop that tells you which of W1-W6 is working. The dashboard makes the system self-evaluating against the north star.
- Headline metrics are the KRs themselves. Operational metrics are supporting evidence.

**Demo narrative**: "Every Monday I get a dashboard that shows whether new contract engagements are growing, whether my $/engagement is moving, and whether projects are converting to maintenance. Underneath, I can see exactly which workflow contributed — and how much I'm spending across all the AI calls that drive it."

**Dependency**: requires the operator to write to `outcomes` table via `/outcome` for KR1 attribution to populate. This is discipline, not automation.

</workflow>

---

## Workflow-to-Agent Map

<workflow_agent_map>

| Workflow | Agents involved |
|----------|-----------------|
| W1 Inbound prospect intake | Roy Kent, Briefing (surface), Task Tinder (via task_candidates) |
| W2 ICP work intelligence | Tartt, Roy Kent, Keeley Strategy, Meeting processor, Fact extraction (all emit icp_signals); Nate Shelley (synthesizes) |
| W3 Content discovery to publication | Tartt, Keeley Strategy, Keeley Content, Sam, Keeley Distribution |
| W4 Discovery call processing | Meeting processor, Task extractors, Briefing (surface) |
| W5 Daily briefing | Briefing (reads from all) |
| W6 Capture and recall | Discord bot capture cog, Fact extraction |
| W7 Weekly dashboard | Higgins, Ted (anomaly detection feeds Higgins) |

</workflow_agent_map>

---

## Workflow-to-KR Map

<workflow_kr_map>

| Workflow | KR1 (new engagements) | KR2 ($/engagement) | KR3 (maintenance conversion) |
|----------|------------------------|---------------------|-------------------------------|
| W1 Inbound intake | Direct | — | — |
| W2 ICP intelligence | Direct | Direct | — |
| W3 Content pipeline | Direct | Indirect | — |
| W4 Discovery calls | Direct | — | Direct |
| W5 Daily briefing | Indirect | Indirect | Indirect |
| W6 Capture and recall | — | Direct | Direct |
| W7 Weekly dashboard | Meta | Meta | Meta |

</workflow_kr_map>

Every workflow ties to at least one KR. No workflow exists without KR alignment.

---

## Demo Story (5-minute walkthrough)

<demo_story>

The workflows compose into a coherent narrative for a prospect or investor conversation:

1. **"A prospect fills out my scorecard."** (W1 starts)
2. **"They show up on my briefing the next morning with an ICP fit score and a suggested first message ready in Task Tinder."** (W5 reading W1)
3. **"I take the discovery call. The transcript becomes facts, follow-ups, and pain signals before I open my laptop."** (W4)
4. **"My next briefing shows the top three things I owe them — and if I haven't acted by day 4, the system surfaces a draft message I can send."** (W5 reading W4 with escalation)
5. **"This week, my ICP signal report tells me three pain themes my prospects keep mentioning — across articles, scorecards, and discovery calls. My content for next week speaks to one of them."** (W2 feeding W3)
6. **"My content publishes weekly, drafted in my voice, evaluated against my positioning, scheduled when I approve it in Discord."** (W3)
7. **"And when a client asks 'what did we decide about X eight months ago' — I have the answer in 3 seconds."** (W6)
8. **"Every Monday, Higgins shows me whether new engagements moved, whether my $/engagement is up, whether projects are converting to maintenance — and exactly what I spent on AI to get there."** (W7)

Eight beats. Every beat is a workflow. Every workflow ties to a KR.

</demo_story>
