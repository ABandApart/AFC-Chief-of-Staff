---
name: discovery-call-to-proposal
description: How a discovery-call transcript becomes a grounded, voice-matched proposal draft.
applies_to: [meeting, proposal]
publish_to_memory: true
tags: [w4, content, proposal]
---

# Discovery call → proposal

Spans the meeting processor (Phase 7) and the content pipeline (Phase 8). The
point of the graph is that the proposal is grounded in what was actually said,
in your voice — not a template.

## Steps

1. **Extract** from the transcript → `meeting_transcripts` plus facts,
   follow-ups, decisions, task_candidates, icp_signals; link participants to
   `people` (and to a `prospect` if matched).
2. **Assemble proposal context from the graph**: this client's named pains, the
   one workflow they want removed, their stated constraints (e.g. data
   residency), and the relevant `decisions` about positioning and voice.
3. **Draft** fixed-scope: the one workflow, priced fixed-scope, maintenance
   retainer after; a data-residency section if the client raised it; a
   separately-priced phase-two outline for anything deferred.
4. **Voice-match** against prior sent proposals / drafts (style grounding).
5. **Evaluate** (Sam) against the positioning + voice rubric; up to 2 redraft
   cycles.

## Rules

- Lead with the one workflow, not the platform (per positioning decisions).
- The draft is a draft: it goes to #approvals, never to the client, without a
  human yes (B2).
- Cite only what the graph supports; never invent client specifics.
