# Phase 4: Discovery (Tartt) — PRD & Build Spec

<doc:meta>
  <doc:phase>4</doc:phase>
  <doc:theme>News/content discovery → the graph → reading recs + task candidates + ICP fuel</doc:theme>
  <doc:duration>~1–1.5 weeks</doc:duration>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>drafted — build after B2/B3</doc:status>
  <doc:depends_on>3.7 (cognee graph + local embed), `sources` table (0001), ontology `ContentItem`/`InterestSignal`, scheduler/loops, briefing</doc:depends_on>
  <doc:blocks>Phase 8 (content pipeline drafts from ContentItems), Phase 5 (Task Tinder trial), Phase 10 (Nate reads content-derived signals)</doc:blocks>
</doc:meta>

## TL;DR

Tartt polls content sources (RSS/HN/arxiv/YouTube/newsletters), extracts each
article, **summarizes it (Gemini Flash — news is Gemini's lane)**, and ingests it
into the cognee graph in a **hybrid, locally-embedded** shape exactly like the
Granola meeting content: mode-1 text **plus** a typed `ContentItem` node, on
Postgres+pgvector with the **local bge embedder** (no Gemini embeddings). It
interest-scores each item against `InterestSignal` vectors, surfaces the top few
as reading recommendations in the briefing, and proposes `task_candidates`
("write about X", "share Y") — which flow to **Task Tinder (Phase 5)** as the
first real trial of whether the task-candidate data structure holds up.

## Goal & Non-Goals

**Goal:** a scheduled agent that turns raw sources into (a) searchable + typed
content knowledge in the graph, (b) ranked reading recs in the briefing, and (c)
task candidates for Task Tinder.

**Non-goals:** no drafting/publishing (that's Phase 8/9, behind B2); no Buffer; no
Gemini embeddings (local bge only); Tartt *proposes* tasks, it doesn't act.

## Provider split (per the operator's plan)

| Step | Provider | Why |
|------|----------|-----|
| Article **summarization** | **Gemini `gemini-2.5-flash`** | **Chosen deliberately** — Gemini weights search + recency in its outputs, which is what news discovery wants. Tartt's one Gemini touchpoint. |
| Graph **entity extraction** (cognify) | **Anthropic** (`claude-haiku-4-5`, via litellm/M1) | Same as the Granola hybrid. |
| **Embedding** | **local FastEmbed bge-base-en-v1.5 @768** | Operator instruction: "local embedding, like the Granola content." No Gemini embeddings, no rate-limit exposure. |

> **Free-tier quality trial (operator, 2026-08-03).** Gemini stays on the **free
> tier on purpose** — the goal is to **evaluate the depth + quality** of Flash's
> search/recency-aware summaries before deciding whether to **expand (paid tier)
> or pivot to another model**. So: **do not upgrade to paid yet.** Keep trial
> volume within the free cap by starting with a **small source set + modest poll
> cadence** and **interest-gating** the expensive downstream work. Only
> *summarization* touches Gemini (embeddings are local); if the free cap bites
> before the quality read is done, throttle cadence rather than upgrade — the
> point is to judge quality, not throughput, first.

## Data model — the content hybrid

Knowledge → the **graph** (`aiadaptive_cognee`, pgvector, local bge); operational
pipeline state → **SQL** (`aiadaptive_cos`), linked by node-id (the entity↔
operational boundary).

- **Knowledge (graph, hybrid like Granola):** per article, ingest **(a) mode-1**
  the summary+extract via `cognee.add`+`cognify` (Anthropic extraction, local
  embed) *and* **(b)** a typed **`ContentItem`** DataPoint (`url, title, summary`,
  `mentions_signals` edges; `index_fields=[title,summary]`, local embed).
  Deterministic id = `uuid5(url)` so a re-seen URL upserts (dedup, like the
  meeting-hybrid person/meeting ids). `InterestSignal` nodes likewise become typed
  graph nodes (local bge).
  - **Cost gate:** running Anthropic `cognify` on *every* article can add up.
    Recommended: **typed `ContentItem` for all** (cheap, local embed), and
    **mode-1 cognify only for items above an interest threshold** (the ones worth
    deep semantic recall). Confirm at build.
- **Operational (SQL):** a `content_pipeline`-adjacent row (or a lean
  `content_seen` tracker) holds the pipeline stage + `content_node` TEXT (the
  ContentItem node-id) + `interest_score` + engagement. Reuses the `channel_state`
  watermark pattern per source for incremental polling. (The legacy vectorized
  `content_items` SQL table from 0001 is superseded by the graph ContentItem;
  a migration retires or repurposes it — decide at build.)

## Interest scoring
Cosine between each `ContentItem` and the `InterestSignal` vectors (both local
bge, same space) → `interest_score`. Top-N by score + recency surface in the
briefing's "new since yesterday / reading" section (extends `agents/briefing`).

## Feeds (the whole point of doing 4 first)
- **Task Tinder (Phase 5) — the data-structure trial.** Tartt writes
  `task_candidates` (`proposed_action`, `source_type='discovery'`, `source_ref`=
  content node-id, `evidence_text`=why, `confidence`). Task Tinder surfaces them
  with accept/decline. **This is the operator's explicit test** of whether the
  `task_candidates` shape holds up with a real producer — so Phase 4 must populate
  every field meaningfully, and Phase 5's accept→`tasks` promotion validates the
  round-trip. The content pipeline (Phase 8) will be a *second* producer into the
  same structure — build Phase 4's writes to that shared contract.
- **Content pipeline (Phase 8):** high-interest ContentItems are the drafting
  queue.
- **ICP (Phase 10):** content-derived signals are (until clients land) a primary
  ICP input — Nate reads ContentItem/InterestSignal nodes from the graph.

## Build outline
1. Sources: seed `sources` (RSS/HN/arxiv/…); a `tartt-poll` loop (scheduler) with a
   per-source watermark.
2. Fetch + extract (add `trafilatura` for HTML→clean text) → summarize
   (`runs.call_gemini`, label `tartt`/`news_aggregation`, new ceiling).
3. Content ingest: a `_lib/content_graph.py` mirroring `meeting_graph` — typed
   `ContentItem` builder (deterministic uuid5(url)) + `add_data_points`; optional
   mode-1 cognify above threshold. Local embed throughout.
4. Interest scoring (graph vector similarity) → operational row + score.
5. Briefing integration (reading recs) + `task_candidates` proposal.
6. Tests (pure): ContentItem builder + deterministic ids; interest-score math;
   task-candidate field mapping. Runtime: a source polls → ContentItem in the
   graph, `/recall` finds it, a task candidate appears, ledger shows Gemini
   summarize + zero Gemini embeddings.

## Open decisions (recommend, confirm at build)
- **Summarization provider — DECIDED: Gemini Flash, free tier** (operator: trial
  quality before scaling/pivoting — see the Free-tier quality trial box).
- **mode-1 cognify: all articles vs interest-gated** (recommended: gated — also
  keeps the trial cheap).
- ~~**Legacy `content_items` SQL table:** retire vs repurpose as the operational
  tracker.~~ **RESOLVED (2026-09-01): REPURPOSED — kept as the operational tracker.**
  It is not legacy: the poller reads it for URL dedup (`run.py`) and writes each
  processed item, the briefing reads it for the Reading section, and `content_node`
  links each row to its cognee graph node — the same SQL-structural / graph-semantic
  split Track O uses (`outreach_evidence`). Retiring it would break the poller's
  dedup and the briefing; the graph holds the semantics, this holds dedup + interest
  score + the graph link.
- **Source seed list + poll cadence** — start **small** (a handful of sources,
  slow cadence) for the free-tier quality trial.
