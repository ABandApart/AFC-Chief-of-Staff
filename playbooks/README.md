# Playbooks — control plane

SOPs / runbooks the agents follow ("how to qualify a prospect", "how to turn a
discovery call into a proposal"). Part of the control plane: authored in git,
trusted (boundary B4).

Playbooks are the **one hybrid artifact**: authored here (source of truth), and
optionally **published one-way into the cognee memory graph** so an agent can
retrieve the right SOP at runtime. Because the publish path is git → publish
only, a playbook can never arrive through the untrusted ingest channels (B1).

## Convention

One file per playbook: `playbooks/<name>.md`.

```yaml
---
name: <slug>                 # must match the filename stem
description: <one line>
applies_to: [<entity/workflow>, ...]   # e.g. [prospect], [meeting, proposal]
publish_to_memory: true|false          # if true, published to the trusted `playbooks` dataset
tags: [<tag>, ...]
---

# <Title>

<the SOP — steps the agent follows>
```

## Publish path (Track B / W5)

`cli/publish_playbooks.py` cognifies every playbook with
`publish_to_memory: true` into a dedicated **`playbooks` dataset** (the trusted
memory region, B1). Agents retrieve with a search scoped to that dataset only —
never mixed with untrusted ingest. Re-runs are hash-idempotent (only
changed/new playbooks re-cognify; `--force` overrides). Run it after changing a
published playbook:

```
uv run python -m cli.publish_playbooks            # publish changed/new
uv run python -m cli.publish_playbooks --dry-run  # preview, touch nothing
```

## Authoring

Obsidian may be pointed at this directory as an editor; git remains the source of
truth and the sync. Its document-link graph is unrelated to the cognee memory
graph.
