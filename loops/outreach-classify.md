---
name: outreach-classify
schedule: "0 19 * * 0"
trigger_kind: scheduled
enabled: false
command: uv run python -m agents.outreach.classify
description: Classify the news queue into triggers; promote confident matches (Track O, Part 2).
---

# Outreach classification — Trent Crimm (Track O, Part 2)

Runs **Sunday 19:00** (`35-` §10), reading the unclassified queue Part 1 fills and
asking one Haiku call per item whether the headline is one of the eight triggers.
A confident match (≥0.7) promotes: it writes typed dated evidence, and for a firm
still in the pool it promotes the firm to a target — the trigger that finally
moves it out of the pool.

**One LLM call per item** — `function_label='outreach_watch'`, `$0.30/day`
ceiling. It needs anthropic credentials, so it runs on barry-agent; the build box
verifies the deterministic half (queue reader, verdict recorder, promotion,
evidence write, idempotency, H5 quarantine).

**Ships DISABLED.** It needs Part 1's queue to have depth and the anthropic key,
and it spends money — a deliberate flip, not a convention.

## Deliberate choices

- **`classified_as='none'` is terminal.** A signal judged not-a-trigger is never
  re-asked; re-classifying would spend again on a settled item.
- **Promotion anchors on the acceptance date, not the event date** (0023). The
  event's own date lives in the evidence row's `first_seen_at`.
- **H5 at the prompt boundary.** A crafted excerpt is quarantined and never placed
  in the prompt.
- **A promoted fact is never close-swept** — `close_absent_evidence` runs only on
  `open_role`, and these are `news_event`.

Trust: reads the queue, writes evidence and targets, sends nothing. Never
approaches **B2**. The one LLM call classifies; it proposes no outbound action.
