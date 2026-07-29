# Checklist — Track C, Channel 1: Granola meeting ingest (mode-1)

First Track C channel (`architecture/26-cognee-migration-plan.md`). A scheduled
poller reads Granola notes via the public REST API and ingests each into the
cognee graph (mode-1) through the shared `ingest_note` core. **Pull channel** →
untrusted ingest crosses **B1**; no inbound exposure (no **B3**) and no outbound
action (no **B2**). The structured `Meeting` node (hybrid) and Google Drive are
separate later increments.

> ⚠️ **Design correction:** the Phase-7 "Granola export folder" filesystem-watch
> model is obsolete — Granola encrypted its local cache (~Apr 2026) and now ships
> an official API. This channel is an **API poller** and supersedes the Phase-7
> meeting ingestion path.

## Builder-side — DONE (`main`, this commit)

- [x] `agents/_lib/granola_client.py` — stdlib-urllib REST client: `list_notes`
  (cursor/`hasMore`, `updated_after`), `iter_note_summaries` (pages + sorts
  oldest-first), `get_note(?include=transcript)`, 429 backoff, and the pure
  `assemble_note_text` (title/date/attendees header + summary + transcript). No
  new dependency. Schema confirmed against the docs 2026-07-29.
- [x] `agents/_lib/ingest.py` — `ingest_note` gained backward-compatible
  `dataset` / `label_agent` / `label_function` overrides (defaults reproduce
  Discord capture exactly). One channel-agnostic core, now multi-dataset.
- [x] `agents/granola/run.py` — one-shot poller: `configure_cognee()` → read the
  `granola` watermark → list notes `updated_after` it → per note, fetch + assemble
  → `ingest_note(dataset="granola", label_agent="granola")` → advance the
  watermark across the contiguous run of successes (stop at first failure so no
  note is skipped). Soft breaker `assert_under_ceiling("granola")` up front.
- [x] Migration **0007** `channel_state(channel PK, cursor, updated_at)` — the
  per-channel poll watermark (reused by Drive). `capture_messages` content-hash
  dedup stays the correctness backstop. `verify_schema.sql` → 20 tables.
- [x] `agents/_lib/runs.py` — `DAILY_CEILINGS["granola"] = 3.00`.
- [x] `loops/granola-poll.md` — `agent: granola`, `*/15 * * * *`, **`enabled:
  false`** (dormant until activated). Control plane validates.
- [x] Tests: `tests/test_granola_client.py` (assembly, backoff, pagination),
  `tests/test_ingest.py` (override defaults). Suite 103, ruff clean.

## Human / operator actions (credentials — barry-agent/operator)

- [ ] **Verify Granola plan tier** supports a **personal API key with
  transcripts** (Settings → Connectors → API keys; free/Basic excludes
  transcripts — Business/Enterprise needed). This is the one real unknown.
- [ ] Mint the personal API key (`grn_…`); barry-agent stores it as
  **`granola-api-key`** in its keychain.

## Runtime (barry-agent)

- [ ] `git pull`; apply migration 0007
  (`psql "$DBURL" -f migrations/0007_channel_state.sql`); `verify_schema.sql` → 20.
- [ ] **Manual test run:** `uv run python -m agents.granola.run` → confirms a
  recent note is cognified into the `granola` dataset; `/recall` about that
  meeting returns a graph answer; `agent_runs` shows `agent_name='granola'`,
  `correlation_kind='cognify_run'` spend; a second run ingests nothing new
  (watermark + hash dedup). Dump one note's JSON once to confirm the API field
  names match `granola_client` assumptions.
- [ ] **Activate:** once the manual run is green, barry-admin flips
  `loops/granola-poll.md` → `enabled: true`, commits; barry-agent pulls +
  **restarts the scheduler** (`launchctl kickstart -k …scheduler`) — it reads
  loop manifests once at startup. Confirm `granola-poll` in the schedule
  (`scheduler.log`).

## Next increments (not in this build)

- [ ] **Structured `Meeting` hybrid** — attach a typed `Meeting` node
  (title/date/participants from the API fields) via `add_data_points` alongside
  the mode-1 text. Gate on a runtime probe of `add_data_points` retrieval
  (`agents/test/ontology_shape.py`, still unrun).
- [ ] **Google Drive ingest** — OAuth installed-app + refresh token in Keychain
  (net-new OAuth infra), scope decision (`drive.file` vs `drive.readonly`),
  one-time operator OAuth bootstrap; reuses `ingest_note` + `channel_state`.
- [ ] Deferred further: Drive document *output* (needs the B2 `#approvals` cog —
  docs-only today), the B3 tunnel, email, inbound API/webhook.
