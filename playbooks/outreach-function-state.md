---
name: outreach-function-state
description: The two-tab diagnostic — deciding whether a target's function is self-covered, under-led, or has a vacant seat.
applies_to: [outreach]
publish_to_memory: true
tags: [track-o, outreach, diagnostic, tier-3]
---

# The two-tab diagnostic

**Five minutes, two browser tabs, one judgement.** It sets
`outreach_targets.function_state`, and it is **Tier 3** (`35-` §13) — human-only.
The method is explicit that this cannot be bought or inferred: evidence *informs*
it, evidence does not *set* it.

Nothing can enter the arc without it. `outreach_targets_seq_ck` refuses
`status='in_sequence'` while `function_state` is NULL, and the intake card
refuses with "do the two-tab diagnostic first" rather than guessing.

> **Draft — 2026-08-14.** `35-` names this diagnostic and says it is two tabs and
> five minutes, but never writes down what the two tabs are or where the lines
> fall. What follows is reconstructed from what the spec *does* state (§2's
> `leadership_member` evidence "feeds function_state, S4"; §6's worked example
> "three AEs were hired and no VP Revenue appears in the leadership list"), and
> is meant to be corrected against real cases. Correct it here; this file is the
> reference the scoring CLI prints.

## The two tabs

| Tab | What you are looking for |
|-----|--------------------------|
| **1 — their team / leadership page** | Is there a named person who owns this function? At what level? Is one person visibly covering two functions? |
| **2 — their open roles** (the ATS board the evidence poller already reads) | Are they hiring *for* this function? At what level — the leader, or people below the leader? |

The poller supplies tab 2 automatically: `uv run python -m cli.outreach_gaps`
and the evidence rows show what is currently open and how long it has been open.
Tab 1 is the part only you can do.

## The three states

**`self_covered`** — the founder or an existing executive is doing this function
on top of their own job. Nobody is named as owning it, and nobody is being hired
to. This is the *earliest* state and often the hardest sell: they may not yet
feel the pain.

**`under_led`** — someone owns it, but below the level the company now needs. A
manager running what needs a VP; an agency; a capable generalist stretched across
two functions. Something exists, so "you have nobody" is the wrong pitch — T08
("The Current Arrangement") is written for exactly this.

**`vacant_seat`** — the seat is empty and visibly so. Either a leader left and
nobody replaced them, or there is an open req for the leader. **This is the
strongest state**, and it is the one the evidence poller can most nearly confirm
on its own: an open leadership req with real posting age is what T10 and T19 both
rest on.

## Deciding between them

- An open req **for the leader** → `vacant_seat`, almost always.
- Someone named on the leadership page **at the right level** → not vacant. Then
  ask whether they are stretched: two functions on one person is `under_led`.
- Nobody named and nobody being hired → `self_covered`.
- Hiring **below** the leader while the leader seat is empty → `vacant_seat`, and
  note it: that is the compound signal (see `outreach-scoring`), the strongest
  pattern in the method.

## What to do when you cannot tell

Leave it NULL. The intake card will refuse to sequence, which is the correct
outcome — a guessed function state picks the wrong template, and the wrong
template is a worse first impression than no message at all.
