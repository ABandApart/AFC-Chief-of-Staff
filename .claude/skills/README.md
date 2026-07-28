# Skills — control plane

Reusable capability definitions, part of the **control plane** (see
`architecture/25-target-state.md`). Authored in git, loaded **as-is** — a skill
is exactly what you wrote, never embedded, never fuzzy-matched, never published
to the cognee memory graph.

## Convention

One directory per skill: `.claude/skills/<name>/SKILL.md`.

```markdown
---
name: <kebab-case slug>          # must match the directory name
description: <one line — what it does and when to reach for it>
---

# <Title>

<the procedure the skill encapsulates>
```

## Trust

Skills are **trusted** because they come through git review (boundary B4): nothing
writes a skill at runtime, and the memory graph never mints one. That is why a
skill must never be sourced from, or merged with, ingested content.

## Skills vs playbooks

- **Skill** — a reusable capability/procedure loaded deterministically. Think
  "how to run a spend review."
- **Playbook** (`playbooks/`) — a domain SOP the agents follow, optionally
  *published* into the memory graph for runtime retrieval. Think "how to qualify
  a prospect." Playbooks can publish; skills never do.
