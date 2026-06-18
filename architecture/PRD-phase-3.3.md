# Phase 3.3: Recall CLI — PRD & Build Instructions

<doc:meta>
  <doc:phase>3.3</doc:phase>
  <doc:parent_phase>3 — Capture and Recall</doc:parent_phase>
  <doc:theme>Hybrid search over facts (the "recall" half of capture-and-recall)</doc:theme>
  <doc:duration>~2 hours + a few cents smoke</doc:duration>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>drafted — implementation gated on 3.2 going green (validates call_embedding live)</doc:status>
  <doc:depends_on>3.2 (facts with embeddings), Phase 2 cost helper, fixed call_embedding (dc04cc6)</doc:depends_on>
  <doc:blocks>3.4 (/outcome), 3.5 (briefing reads facts)</doc:blocks>
</doc:meta>

## TL;DR

`cli/recall.py "what did I decide about the newsletter"` → embeds the query
(gemini-embedding-001, 768-dim, via the cost helper) and runs **hybrid search**
(full-text + vector) over the `facts` table, printing the top matches. This is
the read side that closes the capture→recall loop from Phase 3.2.

---

## Goal & Non-Goals

<goals>

**Goal**: From a terminal (barry-agent, where the keys live — or any account
with `db-url` + `gemini-api-key`), run a natural-language query and get back
the most relevant captured facts, ranked by a blend of lexical and semantic
similarity.

</goals>

<non_goals>

- **Discord `/brain query` slash command** — that's a thin wrapper added later
  (Phase 3.4+ or whenever the bot surfaces recall); 3.3 is the CLI core.
- **Recall over `content_items` / `meeting_transcripts`** — same pattern, but
  those tables aren't populated until Phase 4 / Phase 7. 3.3 targets `facts`.
- **A Postgres `hybrid_search_facts()` function** — the architecture suggests
  promoting the SQL to a reusable function. 3.3 inlines the SQL in the CLI;
  promote to a function (migration 0002) when a second caller appears
  (briefing in 3.5 is the likely trigger).
- **Re-ranking / LLM answer synthesis** — recall returns facts, not prose. A
  "summarize these facts" mode is a later enhancement.
- **Write operations** — recall is read-only.

</non_goals>

---

## Acceptance Criteria

<acceptance>

| # | Criterion | How to verify |
|---|-----------|---------------|
| AC1 | `uv run python -m cli.recall "<query>"` prints ranked facts | Run it against a populated brain |
| AC2 | Round-trip: a fact captured via #capture (3.2) is findable by a semantically-related query that shares no exact words | Capture "Q3 newsletter theme is AI for small teams"; recall "what's the plan for the third-quarter mailing" → returns it |
| AC3 | Exact-term query (a proper noun) ranks the matching fact highly via the lexical half | Capture a fact mentioning "Alex Mendez"; recall "Alex Mendez" → returns it near top |
| AC4 | The query embedding is generated through the cost helper (an `agent_runs` row appears) | `SELECT * FROM agent_runs WHERE agent_name='recall'` after a query |
| AC5 | `--limit N` controls result count; default 5 | `... "query" --limit 3` returns ≤3 |
| AC6 | Empty/no-match query degrades gracefully (no crash, clear "no results") | Query gibberish → "No matching facts." |
| AC7 | Unit test for the result-formatting / ranking-merge logic passes | `uv run pytest tests/test_recall.py` |

</acceptance>

---

## Deliverables Manifest

```
aiadaptive-cos/
├── cli/recall.py                       # NEW — the recall CLI
├── agents/_lib/runs.py                 # +DAILY_CEILINGS["recall"], +KEY_BY_AGENT? (no — embedding only)
├── tests/test_recall.py                # NEW — formatting/merge unit tests
├── architecture/PRD-phase-3.3.md       # this file
└── checklists/CHECKLIST-phase-3.3.md
```

Note: `recall` only calls `call_embedding` (Gemini, shared key) — it makes no
Anthropic call, so it needs a `DAILY_CEILINGS["recall"]` entry but **no**
`KEY_BY_AGENT` entry.

---

## Design

### Hybrid search SQL (from `30-memory-layer.md`)

Inlined in `recall.py`, parameterized by query text, query embedding, and
limit. Weights 0.4 lexical / 0.6 semantic (the architecture's starting point):

```sql
WITH query_input AS (
    SELECT plainto_tsquery('english', %(q)s) AS tsq,
           %(emb)s::vector(768) AS query_embedding
),
fts AS (
    SELECT f.id, ts_rank_cd(f.content_tsv, qi.tsq) AS lex
    FROM facts f, query_input qi
    WHERE f.content_tsv @@ qi.tsq
),
vec AS (
    SELECT f.id, 1 - (f.embedding <=> qi.query_embedding) AS sem
    FROM facts f, query_input qi
    WHERE f.embedding IS NOT NULL
    ORDER BY f.embedding <=> qi.query_embedding
    LIMIT 50
),
combined AS (
    SELECT COALESCE(fts.id, vec.id) AS id,
           COALESCE(fts.lex, 0) * 0.4 + COALESCE(vec.sem, 0) * 0.6 AS score
    FROM fts FULL OUTER JOIN vec USING (id)
)
SELECT f.id, f.content, f.domain, f.confidence, f.created_at, c.score
FROM combined c JOIN facts f ON f.id = c.id
ORDER BY c.score DESC
LIMIT %(limit)s;
```

### Query embedding

`recall.py` wraps the embed in the cost helper so the read is observable:

```python
with agent_run("recall", "infrastructure", trigger_kind="manual") as run:
    qvec = run.call_embedding([query])[0]   # gemini-embedding-001, 768, normalized
```

Because stored fact embeddings are L2-normalized (3.2 fix) and the query
embedding is normalized the same way, cosine distance (`<=>`) is consistent.

### Output

Plain text, ranked, e.g.:
```
3 facts for: "newsletter plan"
  [0.71] (decision) Q3 newsletter theme is AI for small teams.
  [0.55] (idea) Consider a recurring "tool of the week" section.
  [0.49] (contact) Alex Mendez prefers async updates over standups.
```

---

## Architectural Decisions

1. **Inline SQL, not a DB function (yet).** One caller in 3.3; promote to
   `hybrid_search_facts()` (migration 0002) when briefing (3.5) becomes the
   second caller. Avoids a migration round-trip now.
2. **`recall` is its own agent name** in the ledger so query-embedding spend
   is attributable and a daily ceiling applies. Gemini-only → no Anthropic key.
3. **Runs where the keys are.** Needs `db-url` + `gemini-api-key`; in our
   single-box setup that's barry-agent. (If we later want recall from
   barry-admin, copy those two items there.)
4. **Lexical + semantic both required.** Vector alone misses proper nouns;
   FTS alone misses paraphrase. The FULL OUTER JOIN keeps facts that match
   either side.

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| call_embedding still flaky after 3.2 fix | Low (validated in 3.2 re-test) | High | 3.3 implementation is gated on 3.2 going green |
| HNSW recall quality poor at low fact volume | Medium | Low | Few facts = exact-ish search anyway; quality matters at scale (Phase 4+) |
| Weights 0.4/0.6 not tuned for this corpus | Medium | Low | Expose `--lex-weight`/`--sem-weight` flags for experimentation; default 0.4/0.6 |
| Query with no FTS match AND no vector (empty brain) | Low | Low | Graceful "No matching facts." |

---

## Task Breakdown (sequenced)

<tasks>

1. **[BARRY-ADMIN]** Add `DAILY_CEILINGS["recall"]` (e.g. $0.50/day) in runs.py.
2. **[BARRY-ADMIN]** Write `cli/recall.py` (argparse: query, `--limit`,
   `--lex-weight`, `--sem-weight`; embed via cost helper; run hybrid SQL; print).
3. **[BARRY-ADMIN]** `tests/test_recall.py` — unit-test the row→line formatting
   and any pure merge/score helpers (DB + embedding mocked).
4. **[BARRY-ADMIN]** Commit builder-side, push.
5. **[BARRY-AGENT]** `git pull`, `uv sync`, run `cli.recall` for the round-trip:
   recall a fact captured in 3.2 by a paraphrased query (AC2) and by an exact
   term (AC3). Report results.
6. **[BARRY-ADMIN]** Close 3.3; create PHASE-3.4.md (/outcome slash command).

</tasks>

---

## Definition of Done

<dod>

3.3 is complete when a fact captured in #capture during 3.2 can be retrieved by
`cli/recall.py` using a semantically-related query that shares no exact words,
the query-embedding spend shows up in `agent_runs`, and exact-term lookups also
rank correctly.

</dod>

## What Phase 3.4 Will Do With This

3.4 adds the `/outcome` Discord slash command (writes to the `outcomes` table)
— independent of recall, but completes the "capture surfaces" set. Recall may
later get a `/brain query` slash wrapper that calls this same CLI core.
