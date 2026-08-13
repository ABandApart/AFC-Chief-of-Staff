# AI Adaptive Chief of Staff — working instructions

Orientation: read `architecture/00-INDEX.md` for routes into the corpus, and
`architecture/70-build-order.md` for the **active build order** (the banner near
the top supersedes the numeric phase table) and the decision log.

## Spec-driven development — the standing convention

**Adopted 2026-08-12 by the operator.** Full definition in
`architecture/70-build-order.md` §"Working convention: spec-driven development"
(requirements S1–S6). The short version:

> Every build increment starts from a written spec that states an **outcome** —
> what is true when this is done, phrased so someone else could check it — plus
> its non-goals, its verification, and which decisions are still open. Code
> follows the spec. **When reality contradicts the spec, correct the spec; do not
> silently work around it.**

**The operator has explicitly asked to be held to this.** So:

- **Before starting a build increment, check that a spec exists** stating the
  outcome (S1) and how it will be verified (S3). If there isn't one, say so and
  offer to write it first. A short spec is fine; an absent one is not. Do not
  start coding and backfill the reasoning.
- **If the operator asks for an increment without one**, say so plainly and
  propose writing it — once, without nagging. If they choose to proceed anyway,
  that is their call: build it, and record the assumptions you had to make in the
  decision log so the gap is visible later rather than invisible.
- **When a spec collides with reality mid-build**, stop and write the deviation
  down — in the spec, or as a decision-log entry — with the reasoning. Never
  patch around a spec quietly.
- **When a spec is silent on something load-bearing**, that is an open decision,
  not licence to pick one. Surface it.

Good models already in the repo: `PRD-outreach-gmail-channel.md` (decisions
table, verify-before-build, risks), `36-inbound-leads.md` (settled rules and open
options rigorously separated), `PRD-b3-tunnel.md` (goal/non-goals/status).

## Build mechanics

- `uv run pytest -q` and `uv run ruff check .` before every commit. Ruff has
  **6 known pre-existing errors** (`_lib/runs.py`×3, `discord_bot/cogs/system.py`×1,
  `cli/spend.py`×2) — leave them; introduce none.
- The suite must pass **without the optional dep groups** (`cognee`, `mcp`,
  `gateway`, `tartt` are not synced on the build box). Optional deps are imported
  lazily; tests mock or `importorskip`.
- Migrations are numbered, applied via `psql aiadaptive_cos -f …`, and
  `verify_schema.sql` is updated alongside. **New tables need
  `ALTER TABLE … OWNER TO barry_agent`** — the runtime app connects as that role,
  and forgetting it makes writes fail silently (the bug migration 0011 fixed).
- `tests/test_no_raw_retrieval.py` is a build-failing grep: no raw
  `cognee.search` / `SearchType` outside `_lib/retrieval.py`, **including in
  comments and docstrings**. Reword prose mentions rather than working around it.
  (A second guard of the same shape — forbidding Gmail's send calls — is specced
  in `PRD-outreach-gmail-channel.md` G3 but not built.)
- Commits end with the `Co-Authored-By:` trailer for the model that wrote them.

## Two accounts, one repo

**barry-admin** builds, commits, pushes — and has **no runtime credentials**: no
keychain `db-url`, no API keys, no cognee. Use `psql aiadaptive_cos` (socket
superuser) for the database.

**barry-agent** is the runtime: it holds the keys and runs the bot, agents,
gateway, scheduler, and pollers. Anything needing a live credential, a service
restart, or a real LLM call is barry-agent's, handed over through
`/Users/Shared/afc-richmond/` — write a phase file, point `CURRENT-PHASE.txt` at
it, then `chmod 666` the file and `chmod 777` the directory.

Keychain writes are a **human** step in barry-agent's GUI session; the autonomous
agent cannot answer the login-keychain prompt.
