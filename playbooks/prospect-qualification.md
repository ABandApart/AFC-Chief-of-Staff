---
name: prospect-qualification
description: How Roy Kent scores an inbound lead against the ICP and decides next action.
applies_to: [prospect]
publish_to_memory: true
tags: [w1, icp, roy-kent]
---

# Prospect qualification

Run by Roy Kent (Phase 6) when a lead arrives via the WordPress webhook.

## Steps

1. **Dedup** against `prospects.wordpress_profile_id`; skip if seen.
2. **Assemble context** — the inbound profile plus anything the graph already
   knows about the person or company (prior meetings, emails, mentions).
3. **Score ICP fit (0–1)** against the criteria:
   - professional-services SMB (law, accounting, advisory), ~5–50 people
   - a single high-friction, high-frequency workflow they'd pay to remove
   - decision-maker or close to one
   - stated pain, not just curiosity
4. **Extract pain points** → emit `icp_signals` (wide-net pattern, W2 substrate).
5. **Decide**:
   - fit ≥ 0.7 → create a `task_candidates` follow-up; surface in the briefing.
   - 0.4–0.7 → record, no action; revisit if reinforced.
   - < 0.4 → record and drop.
6. **Write** the `prospects` row with score + reasoning.

## Rules

- Qualification runs from the stored row, never inside the webhook request
  (ack-then-process). Never send anything outbound here — surfacing a follow-up
  is a proposal, not an action (B2).
