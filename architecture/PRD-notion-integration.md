# Notion integration: OKR master, initiatives, to-dos, dashboard, Tartt review, writing — PRD & Build Spec

<doc:meta>
  <doc:theme>Notion as the operator's planning and writing surface; Postgres keeps everything the agents compute</doc:theme>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:status>DRAFT rev 2, 2026-10-16 — revised to the operator's vision of 2026-10-16 (initiatives tier, Notion master OKR document, to-do push, writing workflow, build order). N1 BUILT 2026-10-16 (branch `notion-integration`) and verified live 2026-10-28 (`cli.notion_check` PASS). N2 setup RUN live 2026-10-28 with the Q4 seed; N2 OKR pull BUILT 2026-10-28 (`agents/notion/okr_pull.py`, migration 0033 applied, loop `notion-okr-pull` shipped disabled), first live run pending barry-agent. N3–N6 not started. Rev 2.1, 2026-10-28: N4 spec completed after review (metric registry, scoring, `okr_snapshots`, one-way relations, quarter rollover); N2 known gaps recorded. N-D8…N-D12 settled 2026-10-16; N-D13 (status thresholds) settled 2026-10-28.</doc:status>
  <doc:depends_on>`15-okrs-q4-2026.md`, `PRD-outcome-attribution.md`, `PRD-phase-4-discovery.md`, `PRD-tasktinder-refinements.md`, `80-telemetry-layer.md` (Higgins), `25-target-state.md` (B2, B3), `ADR-0001` (API boundary)</doc:depends_on>
  <doc:supersedes>rev 1 (2026-10-05), which kept Postgres as the system of record for KR definitions</doc:supersedes>
</doc:meta>

## TL;DR

Notion becomes the operator's surface for **planning** and **writing**:

1. **Master OKR document.** Objectives, Key Results, and Initiatives are defined in Notion, in three linked databases. The CoS reads them. Each KR names a metric the CoS computes from Postgres.
2. **Initiatives on Task Tinder.** Every Task Tinder card and task can carry one initiative, set with a new `/initiative` slash command. A new `/todo` command posts a manual to-do as a Task Tinder card, so the operator still decides whether it's a priority now.
3. **To-do list.** Accepted tasks are pushed to a Notion To-Do database. Ticking **Done** in Notion completes the task in the CoS.
4. **Dashboard.** A Notion page shows Objectives → Key Results → Initiatives, with KR values computed from Postgres. Higgins posts a short Discord summary that links to it.
5. **Tartt review.** The top 10 articles per day go to a Content Review database. The operator marks each Keep, Dismiss, or Research this.
6. **Writing.** Long-form newsletter pieces live in an Articles database, one page per article, with every draft under its own toggle heading. The operator drafts with Claude in claude.ai or the desktop app through the Notion connector. The CoS builds no code for the drafting loop itself.

**Build order (operator, 2026-10-16):** N1 foundation → N2 master OKR document → N3 Task Tinder updates → N4 dashboard → **N6 writing → N5 Tartt review**. Time-to-value decides the tail: writing is mostly setup and is usable in days.

## Problem

- **The OKRs have no place to live or evolve.** They exist only as a markdown draft (`15-okrs-q4-2026.md`). Changing a target means editing a file.
- **Tasks are not tied to goals.** 16 accepted tasks and 115 pending candidates (2026-10-16) carry no link to any objective. Nothing shows whether daily work moves a KR.
- **There's no manual to-do path.** `task_candidates.source_type` holds only `discovery`, `outreach_stale_signal`, and `inbound_lead`. The operator can't add their own to-do through Discord.
- **Tartt has no feedback loop.** 246 `content_items`, 0 with `engaged_at` (2026-10-05).
- **Writing has no home.** The newsletter (The Adaptive) is outside the content pipeline (`60-content-pipeline.md` non-goals). Drafts live wherever they were last typed.

## S1. Outcome

When N1–N6 are done:

1. **Master OKRs:** a KR or initiative added or edited in Notion appears in the CoS (`/initiative` type-ahead and the dashboard) within 15 minutes. A KR whose Metric the CoS can't compute is flagged in Discord #system, not silently shown as zero.
2. **Initiatives:** every Task Tinder card and every #tasks checklist line shows its initiative, or "Unassigned". `/initiative` sets or changes it, with type-ahead over active initiatives.
3. **To-dos:** within 5 minutes of accepting a card (including a `/todo` card), it is a row in the Notion To-Do database. Within 15 minutes of ticking Done in Notion, `tasks.status = 'done'`, and the #tasks checklist no longer lists it. All 16 tasks accepted before N3 are in Notion as Unassigned.
4. **Dashboard:** every Monday by 07:15, the dashboard shows each KR's value, target, progress, and status, matching `kr_metrics` at that time. Under each KR, it lists that KR's initiatives with their open and done to-do counts. Higgins has posted the Discord summary with the page link.
5. **Tartt:** each day by 06:30, the day's top 10 articles by `interest_score` are in Content Review. A Decision set in Notion reaches `content_items` within 20 minutes.
6. **Writing:** each article has exactly one page. The page's header block names the latest draft and its status. Each draft sits under a toggle H2 that carries its revision number. Claude in claude.ai can read an article page and add a new draft that follows this convention, using only the instructions page.
7. **Containment:** no outreach contact data, prospect data, email body, or meeting transcript text reaches Notion. To-dos from outreach and inbound sources push with neutral titles (N-D9).

## Architecture

### Systems of record

Rev 1 kept every system of record in Postgres. Rev 2 deliberately moves **three things the operator authors** to Notion. Everything the agents compute stays in Postgres.

| Data | System of record | The other side |
|---|---|---|
| Objective, KR, and initiative definitions and KR targets | **Notion** master OKR databases | Postgres cache (`okr_*` tables), read-only, refreshed by polling |
| KR current values and history | Postgres (`_lib/kr_metrics.py`, `okr_snapshots`) | Notion dashboard, read-only mirror |
| Task existence, title, source, and initiative | Postgres `tasks` | Notion To-Do, mirror |
| **Task completion** | **Notion To-Do `Done`** | Postgres `tasks.status`, written by the poller |
| Article decision (Keep, Dismiss, Research this) | Notion Content Review `Decision` (input) | Postgres `content_items.engagement_type` (record) |
| Article metadata, score, summary | Postgres `content_items` | Notion Content Review, mirror |
| **Article drafts** | **Notion Articles pages** | None. The CoS doesn't copy drafts. |

**Why this still fits ADR-0001.** ADR-0001 rejects bidirectional sync of the *same* data between two stores. Here each field has exactly one owner. Notion owns definitions, completion, and drafts. Postgres owns everything computed. Each sync direction moves only fields its source owns, so the two systems never disagree about who is right.

### Components

| Component | Responsibility |
|---|---|
| `agents/_lib/notion_client.py` | Thin stdlib `urllib` REST client, matching `granola_client.py` (see N1 build notes). Token from keychain `notion-api-token`. Retries on 429 and 529 honoring `Retry-After`. Budget of about 3 requests/second. |
| `notion_sync` table | Maps `(entity_table, entity_id)` to `notion_page_id`. Stores `content_hash` (to skip unchanged pushes), `last_pushed_at`, and `last_pulled_at`. |
| `agents/notion/okr_pull.py` | Polls the master OKR databases into `okr_objectives`, `okr_key_results`, and `okr_initiatives`. |
| `agents/notion/publish.py` | Pushes the To-Do, dashboard, and Content Review projections. Idempotent via `notion_sync`. |
| `agents/notion/pull.py` | Polls To-Do `Done` and Content Review `Decision` changes. |
| `loops/notion-<job>.md` | Scheduler entries, one manifest per job because the loader allows one command per manifest (N2 build notes). N2: `notion-okr-pull`. OKR pull and Done/Decision pull every 15 minutes. To-do push every 5 minutes, plus immediately on accept (best effort). Dashboard push Monday 07:00, attention refresh daily 07:00, Tartt push daily 06:15. |
| `_lib/kr_metrics.py` | The single implementation of every metric, keyed by metric id. Higgins and the dashboard both read it. |

Every loop run writes an `agent_runs` row (`agent_name='notion-sync'`, `usd_cost=0`), so KR4.2 counts it and Ted watches it.

---

## N1 — Foundation

**Outcome:** `uv run python -m cli.notion_check` authenticates as barry-agent and lists the pages shared with the integration. `notion_sync` exists and is owned by `barry_agent`.

- Create a Notion **internal integration** with Read, Update, and Insert content capabilities. Share only the parent page "AFC Richmond Chief-of-Staff".
- Keychain item `notion-api-token`, stored as a human step in barry-agent's GUI session. Add it to `scripts/keychain_setup.sh` and `scripts/keychain_verify.sh`.
- Migration: `notion_sync`.
- `notion_client.py` with pure unit tests: request shaping, 429 backoff, hash stability.

**Verification:** `cli.notion_check` succeeds. Unit tests pass. `verify_schema.sql` lists `notion_sync`.

**Build notes (2026-10-16):**
- **Deviation — `urllib`, not `httpx`.** Every API client in the repo uses stdlib `urllib` (`granola_client.py`, `apollo.py`, `briefing/run.py`), and `httpx` is not a runtime dependency. The spec followed the repo, not the reverse.
- **Version pinned: `Notion-Version: 2026-03-11`**, confirmed latest against developers.notion.com on 2026-10-16. It removes `archived` in favor of `in_trash` (affects the N5 cull) and replaces block `after` with a `position` object that supports `start` (affects N6).
- **Limits confirmed:** 180 requests/minute (about 3/s) per connection on non-Business plans. 429 *and* 529 carry `Retry-After`. 100 elements per array. 2000 characters per rich-text item.
- **Search** filters on `page` or `data_source` (the 2025-09-03 split). `cli.notion_check` lists both.
- **Done:** `notion_client.py` (18 unit tests), `cli/notion_check.py`, migration 0032 (scratch-tested, applied to `aiadaptive_cos`; `verify_schema.sql` clean for it), `notion-api-token` added to both keychain scripts.
- **Pending:** run `uv run python -m cli.notion_check` as barry-agent once the token is stored.

## N2 — Master OKR document

**Outcome:** S1.1.

**Notion side.** A page "OKRs — Master" with three databases. The operator owns all fields.

| Database | Properties |
|---|---|
| **Objectives** | Name (title), Quarter (select, e.g. `2026-Q4`), Order (number), Status (Active / Retired) |
| **Key Results** | Name (title), Objective (relation, limit 1), **Metric** (select from the fixed list below), Target (number), Unit (select: count, $, %, hours), Direction (Higher is better / Lower is better), Manual value (number, used only when Metric = `manual`), Status (Active / Retired) |
| **Initiatives** | Name (title), Key Result (relation, **limit 1** — operator, 2026-10-16), Status (Active / Paused / Done), Owner (person, optional) |

**Metric list.** The Metric select offers only the ids `kr_metrics.py` implements, so picking from the list can't produce a typo. Notion still lets an editor type a new option into a select, so `okr_pull` must reject unknown ids (below). The first list comes from `15-okrs-q4-2026.md` §1:

`engagements_signed`, `median_engagement_value`, `project_to_maintenance_rate`, `cold_sequences_live`, `touches_on_schedule_rate`, `sequences_completed`, `conversations_opened`, `calls_booked`, `proposals_sent`, `followups_on_time_rate`, `agent_run_success_rate`, `llm_spend_month`, and `manual`.

Metrics with no data source yet (selling hours, reply time, recurring pains) are **not** in the list until their gaps close. Those KRs use `manual`.

**CoS side.**
- Migration: `okr_objectives`, `okr_key_results`, and `okr_initiatives`. Each is keyed by `notion_page_id`, holds the Notion fields, and has `archived_at` (set when the page disappears or is retired). Owned by `barry_agent`.
- `okr_pull.py` upserts all three each cycle. A KR with no Objective, an initiative with no KR, or a Metric id unknown to the running code posts one #system message per offending page per day.
- `15-okrs-q4-2026.md` becomes the history of how the Q4 OKRs were chosen, plus the gap analysis. Its targets are entered into Notion once at N2 setup.

**Verification:**
1. Pure tests: Notion property → row mapping; validation flags for each malformed case; archive on disappearance.
2. Runtime: add an initiative in Notion, and within one cycle it appears in `okr_initiatives`. Retire it, and `archived_at` is set.

**Build notes — setup command (2026-10-16):**
- `cli.notion_setup` (logic in `agents/notion/setup.py`, schema in `agents/notion/okr_schema.py`) creates the page "OKRs — Master" under the shared root, then Objectives, Key Results, and Initiatives in link order. Relations are two-way. The reverse sides are renamed to "Key Results" (on Objectives) and "Initiatives" (on Key Results).
- **Idempotent and non-destructive:** it creates only what's missing. An existing database is never edited: a missing property is reported as DRIFT and the command exits 1. `--dry-run` only reads.
- **Ids** are recorded in `notion_sync` with `entity_table = 'notion_schema'` (keys `okr_master_page`, `okr_objectives`, `okr_key_results`, `okr_initiatives`), not in a repo file, so the runtime checkout stays clean.
- **`--seed-q4`** enters the 4 objectives and 16 KRs from `15-okrs-q4-2026.md` §1, only into an empty Objectives database. Two targets are left blank on purpose: Cold sequences live (the cap changed to 150) and LLM spend per month (no budget yet). Selling hours, reply time, recurring pains, and the product spec use `manual`.
- **Confirmed against the API reference (2026-10-16):** databases are created with `initial_data_source.properties`, rows use parent `data_source_id`, and relations reference a `data_source_id`. **The API can't set a relation's "limit 1 page"**, so the command prints it as a manual step. The OKR pull must still reject a page with more than one link.
- **Tests:** 21 in `tests/test_notion_setup.py` against a fake workspace (fresh, re-run, dry run, drift, root missing or ambiguous, seed, and schema), plus 4 client endpoint tests.
- **Verified live 2026-10-28** (barry-agent, `PHASE-NOTION-N1.md` A4): page, three databases, both reverse-relation renames, 4 `notion_sync` ids, and the Q4 seed; a second run changed nothing. The two relation limits are still a manual step (H5).
- **Known gaps (review, 2026-10-28), closed 2026-10-28:**
  - A seed that failed partway couldn't be resumed. `--resume-seed` now creates only the seed objectives and KRs whose names are missing, linked to the existing objectives. Plain `--seed-q4` still refuses a non-empty database, because names the operator has edited can't be matched safely, and its SKIP line points to `--resume-seed`.
  - The drift check compared property names only. It now also reports a select option the spec requires and the database lacks (for example, a Metric id added to `METRICS` later). Options the operator added are not drift.

**Build notes — OKR pull (2026-10-28):**
- **Migration 0033** (applied to `aiadaptive_cos`; `verify_schema.sql` updated, 39 tables): `okr_objectives`, `okr_key_results`, `okr_initiatives`, each keyed by `notion_page_id` with a BIGSERIAL `id` for N3 and N4 to reference. Owned by `barry_agent`.
- **`agents/notion/okr_pull.py`** reads all three databases each cycle and upserts them in one transaction, parents first. KR → objective and initiative → KR links resolve to row ids.
- **Deviation — malformed pages are cached, not dropped.** The spec says `okr_pull` "rejects" a page with more than one link. A rejected page is stored with the reason in `problem`, and consumers read only `problem IS NULL AND archived_at IS NULL`. Dropping it instead would make a broken page look deleted. Problems: no link, more than one link, a link outside the linked database, a blank or unknown Metric, and a blank or malformed Quarter (`YYYY-Qn`, any year, per the rev 2.1 rollover rule).
- **Notices:** due pages are batched into one #system message per cycle, at most once per page per day (`problem_notified_on`, local date), and only marked notified when Discord accepts the post. Fixing a page clears both columns.
- **Archiving:** `archived_at` is set when a page leaves its database and, for Objectives and KRs, when Status is Retired. It clears when the page returns. Initiatives keep Paused and Done as statuses, because N3 shows them on linked tasks.
- **Deviation — run ledger.** Each run writes its `agent_runs` row through `agents/notion/ledger.py`, not `runs.agent_run`. That wrapper runs the LLM spend breaker, which would stop a free loop whenever system spend crossed $25/day.
- **Deviation — one manifest per job:** `loops/notion-okr-pull.md`, every 15 minutes, **shipped `enabled: false`** per `loops/README.md`. Activation is free: Notion reads, cache writes, and #system notices.
- **Tests:** 15 in `tests/test_okr_pull.py` (parsing, every validation case, notices, the Notion read) and 5 more in `tests/test_notion_setup.py` (option drift, resume). The upsert, archive, and notice SQL was run against a scratch database built from 0033 through five cycles: retire and un-retire, delete and return, a daily re-notice, a fix clearing the flag, and an empty database.
- **Pending (barry-agent):** a dry run and a manual run against live Notion, then the runtime verification below.

## N3 — Task Tinder updates

**Outcome:** S1.2 and S1.3.

**Schema** (migration):
- `okr_initiative_id BIGINT REFERENCES okr_initiatives(id)` on both `task_candidates` and `tasks`. Nullable, where NULL means Unassigned. On accept, the candidate's initiative copies to the task.
- `task_candidates.source_type` gains `operator` for `/todo` items.

**Discord:**
- **`/initiative item:<type-ahead> initiative:<type-ahead>`**. `item` suggests pending cards and open tasks, newest first, labelled with their title. `initiative` suggests active initiatives as "Initiative · KR". Running it again changes the initiative. Choosing "Unassigned" clears it. Operator-only. Type-ahead offers only initiatives that are Active in Notion. If an initiative is retired or paused in Notion, tasks keep their link and show the initiative with "(retired)" or "(paused)" until reassigned.
- **`/todo text:<…> initiative:<type-ahead, optional> due:<optional>`** writes a pending `task_candidates` row (`source_type='operator'`, confidence 1.0). It posts to #task-tinder like any card, because a to-do written today may not be a priority yet (N-D10). Accept, decline, and defer work unchanged.
- **Cards and checklist:** the Task Tinder embed gains an "Initiative" field. Each #tasks checklist line ends with `· <initiative>` or `· Unassigned`. Both redraw after `/initiative`.

**Notion To-Do database.** One row per task. Properties: Task (title), Initiative (relation), Key Result (rollup through Initiative), **Done** (checkbox, operator-owned), Due (date), Source (select: discovery / outreach / inbound / operator), Created (date), CoS id (number). Title, **Initiative**, Source, and Due are overwritten from Postgres. Done is never overwritten. Initiative is display-only here: assignment happens in Discord (N-D11). The database description says so. The Initiative relation is one-way (`single_property`), so the master Initiatives database gains no reverse property (see N4).

**Neutral titles (N-D9).** A task whose source names a prospect never sends its real title. `outreach_stale_signal` tasks push as "Outreach follow-up · open in NocoDB", and `inbound_lead` tasks as "Inbound lead follow-up · open in Discord". Each title is suffixed with the CoS id so rows stay distinguishable. The real text stays in Postgres and Discord. A pure test asserts that no title for those sources contains the source text.

**Flows:**
- **Accept** (any source, including `/todo` cards): the push creates the To-Do row. It runs immediately and is retried by the 5-minute loop if Notion is down.
- **Done ticked in Notion:** the pull sets `tasks.status='done'` and `completed_at`. If the task has a linked follow-up (`tasks.follow_up_id`), the pull also closes it. The #tasks checklist then refreshes.
- **Done unticked:** the task reopens.
- **Backfill:** a one-time `cli.notion_backfill_tasks` pushes the 16 tasks accepted before N3 as Unassigned.

**Verification:**
1. Pure tests: type-ahead choice builders, including the 25-choice cap and 100-character truncation. Candidate → task initiative copy. Done/undone transitions. Checklist line rendering.
2. Runtime smoke test: `/todo` with an initiative creates a To-Do row showing that initiative. `/initiative` on a pending card updates the card embed. Accepting it creates the To-Do row. Ticking Done closes `tasks` and the follow-up, and the checklist drops the line. The backfill shows 16 Unassigned rows, and running it again adds none.

## N4 — Dashboard

**Outcome:** S1.4. Layout: the draft `~/dashboard-preview/cos-dashboard-preview.html` (2026-10-05), with one structural change. The scorecard nests **Objective → Key Result → Initiatives**.

**Verify before build.** Check whether the API can create linked database views and chart views (see Risks). N2 didn't need views, so this wasn't checked there. If the API can't, the N4 setup prints a one-time manual checklist, as `cli.notion_setup` does for relation limits. Record the result in the N4 build notes.

**Notion side.**
- **Page "<quarter> Dashboard"**, for example "Q4 2026 Dashboard" (separate from the master document). It has header properties, 4 stat callouts, a "Needs your attention" callout, and linked database views. It shows only KRs whose Objective has the current Quarter and Status Active.
- **KR Scorecard database** (CoS-owned mirror): one row per active KR. Key Result (relation), Objective (rollup through Key Result), Initiatives (rollup through Key Result), Current, Target, Progress (bar), Status, Note, Updated.
- **Initiative Status database** (CoS-owned mirror): one row per active initiative. Initiative (relation), Key Result (rollup), Open to-dos, Done to-dos, Idle (checkbox: no open to-dos). The CoS can't write counts into the master Initiatives database, because the operator owns it (N-S1).
- **One-way relations only.** Every relation from a CoS-owned database into a master database is created as `single_property`, so Notion adds no reverse property to the operator's databases. The same rule applies to the N3 To-Do Initiative relation. The Key Result rollup on To-Do still works through it.
- **Weekly Snapshots database** (CoS-owned mirror): one row per week, one number property per charted KR. It feeds the trend charts. It mirrors `okr_snapshots`.
- **"Needs your attention": counts only** (operator, 2026-10-16). For example, "3 outreach touches unsent — open NocoDB". No company names. The link opens only on the tailnet.
- **Higgins Discord summary** (operator, 2026-10-16): 5 lines to #dashboard, covering north-star KRs, the worst KR, attention counts, and system health, plus the Notion link. The worst KR is the Off track KR with the lowest score (see Scoring), or the lowest At risk KR if none is Off track. This replaces the full text report in `80-telemetry-layer.md`. That doc gets updated in the N4 change.

**Metric registry.** `_lib/kr_metrics.py` declares a *kind* and a *window* for every metric id, so scoring never depends on a property the operator could set wrong. A **cumulative** metric counts events inside its window and grows over time. A **point** metric is a level or rate measured at push time.

| Metric id | Kind | Window | Source (`15-okrs-q4-2026.md` §1) |
|---|---|---|---|
| `engagements_signed` | cumulative | quarter to date | KR1 |
| `median_engagement_value` | point | engagements signed this quarter | KR2 |
| `project_to_maintenance_rate` | point | rolling 90 days | KR3 |
| `cold_sequences_live` | point | at push time | KR2.1 |
| `touches_on_schedule_rate` | point | touches whose window closed this quarter | KR2.2 |
| `sequences_completed` | cumulative | quarter to date | KR2.3 |
| `conversations_opened` | cumulative | quarter to date, from status transitions (gap M3) | KR2.4 |
| `calls_booked` | cumulative | quarter to date, from status transitions (gap M2, M3) | KR2.5 |
| `proposals_sent` | cumulative | quarter to date | KR2.6 |
| `followups_on_time_rate` | point | follow-ups whose deadline fell this quarter | KR4.1 |
| `agent_run_success_rate` | point | last 7 days | KR4.2 |
| `llm_spend_month` | cumulative | calendar month to date | KR4.3 |
| `manual` | point | the KR's Manual value | any |

A metric returns `no_data` when its window holds no rows to measure (for example, a median with no signed engagements, or a rate with an empty denominator).

**Scoring** (thresholds settled, N-S15). Statuses match the dashboard preview.

| Status | Rule |
|---|---|
| ⚪ No data | The metric returned `no_data`, or the KR is `manual` with a blank Manual value. |
| 🔧 Set target | Target is blank. Current still shows. |
| ⏳ Too early | A cumulative, Higher-is-better KR before day 15 of its window that would otherwise score At risk or Off track. Without this rule, every cumulative KR scores Off track in week 1. |
| 🟢 On track | Score ≥ 1.0. |
| 🟡 At risk | 0.9 ≤ score < 1.0 for a point KR. 0.7 ≤ score < 1.0 for a cumulative KR. |
| 🔴 Off track | Below At risk. |

The **score** compares Current to where it must be now:
- **Cumulative, Higher is better:** Current ÷ (Target × elapsed share of the window). The window is the quarter, or the month for `llm_spend_month`.
- **Cumulative, Lower is better:** (Target × elapsed share of the window) ÷ Current. On track when spend is at or under the prorated budget.
- **Point, Higher is better:** Current ÷ Target.
- **Point, Lower is better:** Target ÷ Current.
- **Lower is better with Current at 0:** On track, with no division.

**Progress** (the bar) is Current ÷ Target, capped at 100%, for Higher-is-better KRs. For Lower-is-better KRs, Progress stays blank, because a fuller bar would read as worse. Status carries the judgment.

**CoS side.**
- **Migration `okr_snapshots`:** `week_start DATE` (a Monday), `okr_key_result_id BIGINT NOT NULL REFERENCES okr_key_results(id)`, `metric TEXT`, `value NUMERIC` (NULL for `no_data`), `target NUMERIC`, `status TEXT`, `computed_at TIMESTAMPTZ`, and `UNIQUE (week_start, okr_key_result_id)`. Owned by `barry_agent`. It also stores `metric` because the operator can rebind a KR to another metric mid-quarter.
- **Monday 07:00 push:** compute every active KR through `kr_metrics`, upsert one `okr_snapshots` row per KR for the week that just ended, then push the scorecard, initiative status, and snapshot rows to Notion. A re-run in the same week overwrites that week's rows and changes nothing else.
- **Backfill:** a one-time `cli.notion_backfill_snapshots` computes `agent_run_success_rate` and `llm_spend_month` for each week from 2026-07-27. It attaches each row to the KR currently bound to that metric, with `target` and `status` NULL, because no target existed then. It needs the N2 OKR pull to have run.
- **Quarter rollover:** on the first push of a new quarter, the N4 setup creates that quarter's dashboard page. The previous page stays and stops updating. `okr_pull` accepts any Quarter value of the form `YYYY-Qn`, not only the options `cli.notion_setup` created.

**Verification:**
1. Pure tests: each metric against fixture rows, including the `no_data` path. Each status rule, including Too early on day 14 and day 15, Set target, both Lower-is-better formulas, and a blank Progress for Lower is better.
2. Parity: after a push, every Current matches `kr_metrics` output, and every row in `okr_snapshots` for that week matches the scorecard.
3. A second push with no data change makes 0 page updates.
4. Schema: after N4 setup, the master Key Results and Initiatives databases have no new properties.

## N5 — Tartt review (top 10 per day)

**Outcome:** S1.5.

- **Content Review database:** the day's top 10 `content_items` by `interest_score` (operator, 2026-10-16). Properties: Title, URL, Source, Summary, Interest score, Collected, **Decision** (Keep / Dismiss / Research this), Decided, and **Used in** (relation to Articles, N6).
- **Pull:** a Decision writes `content_items.engaged_at` and `engagement_type`. "Research this" also writes one `task_candidates` row (`source_type='discovery'`), which reaches Task Tinder through the existing path.
- **Cull:** undecided pages older than 14 days are archived in Notion. Postgres is untouched.
- **No learning in v1:** decisions are recorded, but interest weights don't change until about 100 decisions exist.

**Verification:** pure tests for the decision mapping, top-10 selection, and cull dates. Runtime: a Keep reaches `engaged_at` within one cycle. Research this creates exactly one candidate, even across repeat polls.

## N6 — Writing workflow

**Outcome:** S1.6. This increment is **Notion setup plus written instructions**. The drafting loop runs in claude.ai or the desktop app through the Notion connector (operator, 2026-10-16), so no CoS code reads or writes drafts.

### Content structure (recommended)

A long-form newsletter piece is the **parent**. Short-form posts and video scripts cut from it are **derivatives**: separate pages linked back to their parent. One page per thing, and each page carries its own draft history.

| Database | One page per | Properties |
|---|---|---|
| **Articles** | Newsletter piece | Title, Status (Drafting / Ready / Published), Latest draft (number), Publish date, Published URL, Sources (relation → Content Review), Initiative (relation, optional — ties writing to an OKR initiative) |
| **Derivatives** | Short-form post, thread, or video script | Title, **Parent article** (relation, limit 1), Format (LinkedIn post / X thread / Short video script / Long video script), Status (Drafting / Ready / Published), Latest draft (number), Published URL |

**Why separate pages, not sections of the article page:** a derivative has its own status, draft history, and publish date. As a section it would share the article's single status, and you couldn't tell which LinkedIn post shipped. The relation keeps the family together. The article page shows its derivatives as a linked view.

### Page convention (both databases)

```
[Callout] Latest: Revision 3 · Status: Drafting · Updated 2026-10-16

▸ ## Revision 3 — 2026-10-16      ← toggle H2, newest first
    (draft body)
▸ ## Revision 2 — 2026-10-12
    (draft body)
▸ ## Revision 1 — 2026-10-09
    (draft body)

## Notes                          ← optional, plain H2 at the bottom
```

- The callout at the top is the human-readable "which draft is latest". The `Latest draft` and `Status` properties hold the same facts for database views.
- A new draft is a new toggle H2 inserted **above** the previous one, numbered previous + 1. Earlier revisions are never edited.

### Claude instructions

A Notion page "Writing with Claude — instructions", shared with the connector. It tells Claude to:

1. Read the article page, its Sources, and its derivatives before drafting.
2. To save a draft: insert `## Revision N+1 — <date>` as a toggle H2 above Revision N, put the full draft inside it, update the callout, and set `Latest draft` to N+1. Never edit an earlier revision.
3. Change Status only when the operator says so.
4. For a derivative: create it in Derivatives with Parent article set, using the same convention.

The operator adds the same text to a claude.ai Project's instructions, so every writing chat starts with it.

**Verification:** in claude.ai, ask Claude to draft a revision of a test article. The result has exactly one new toggle H2, numbered one higher. The callout and `Latest draft` match it. Earlier revisions are byte-identical before and after (compare with the page history).

---

## S2. Non-goals

- **No outreach or prospect data in Notion.** No contact names, emails, evidence, touches, or dossiers. The attention block shows counts only, and outreach and inbound to-dos use neutral titles.
- **Notion decides nothing gated.** No approvals, sends, or publishes from Notion. `#approvals` (B2) stays the only gate. "Published" on an article is a status the operator sets, not an action.
- **The CoS doesn't copy, index, or ingest drafts.** Drafts stay in Notion. Ingesting them into the brain is a later option.
- **No Notion → Postgres sync beyond the owned fields:** OKR definitions, Done, and Decision. Edits to mirrored fields in Notion are overwritten.
- **No Notion MCP in the CoS runtime.** Scheduled jobs use the REST client so they stay in the ledger. The operator's claude.ai connector is separate.
- **No webhooks in v1.** Polling meets every latency target without a new public endpoint.
- **Notion content as context for Keeley is deferred.** The operator wants Keeley to use Notion content as context, but has not chosen a method. Ingesting it into the knowledge base (cognee) is *not* assumed. Nothing in N1–N6 reads Notion page content for an agent. (Operator, 2026-10-16.)
- **No interest-score learning in v1** (N5).

## S4. Decisions

### Settled (operator, 2026-10-16 unless noted)

| # | Decision |
|---|---|
| N-S1 | **Field-level ownership:** Notion owns OKR definitions, task completion, article decisions, and drafts. Postgres owns everything computed. Each sync moves only owned fields (replaces rev 1's "Postgres owns everything"). |
| N-S2 | Dashboard and Higgins share `_lib/kr_metrics.py`. KRs bind to it through the Metric select. |
| N-S3 | Master OKR document = three linked Notion databases, separate from the dashboard. |
| N-S4 | Each initiative belongs to exactly one KR. Each task has at most one initiative. |
| N-S5 | Initiatives are assigned with `/initiative` (type-ahead), on cards from any source and on open tasks. |
| N-S14 | **This spec stands alone.** No content from `PRD-notion-channel.md` (unmerged branch `phase-10-icp-intelligence`, 2026-09-15) is used: not its ingest design, its drafting model, its decisions, or its probes. The keychain item name `notion-api-token` is kept because the operator chose it, not inherited. (Operator, 2026-10-16.) |
| N-S13 | **Initiative ownership is split by layer (N-D11).** The *list* of initiatives is defined and maintained only in the Notion master Initiatives database. A task's *assignment* is made only in Discord (`/initiative`, or `initiative:` on `/todo`), picking from that list. Discord can't create, rename, or retire an initiative. The Notion To-Do Initiative property is a read-only mirror. |
| N-S6 | Accepted tasks push to a Notion To-Do database. Done is ticked in Notion. The 16 existing accepted tasks are backfilled. |
| N-S7 | Drafting happens in claude.ai or the desktop app via the Notion connector. One page per article, a toggle H2 per revision with its number, and a header naming the latest revision and its status (Drafting / Ready / Published). |
| N-S8 | Attention block: counts only. Higgins: short Discord summary plus the Notion link. Tartt: top 10 per day. |
| N-S9 | Build order: N1 → N2 → N3 → N4 → N6 → N5 (N-D8: time-to-value). |
| N-S10 | Writing structure: Articles (parents) + Derivatives (linked short-form and video pages), each with the page convention in N6. Articles carry an optional Initiative relation (N-D12). |
| N-S11 | To-dos that name a prospect push to Notion with neutral titles (N-D9). Extended from outreach to inbound-lead to-dos, which name a prospect too. |
| N-S15 | **KR status thresholds (N-D13, operator, 2026-10-28).** On track at score ≥ 1.0. At risk from 0.9 for point KRs and from 0.7 for cumulative KRs, which swing more week to week. Too early covers any cumulative KR that would score At risk or Off track before day 15, not only a KR at 0. Lower-is-better KRs show no Progress bar. |
| N-S12 | `/todo` posts a Task Tinder card. It doesn't skip acceptance, because a new to-do may not be a priority yet (N-D10). |

### Open

None (2026-10-28).

## Risks and verify-before-build

| Risk | Mitigation |
|---|---|
| The Notion API might not create database views or chart views. | Verify before N4 (N2 creates no views, so the check moved there). If unsupported, the N4 setup prints a one-time manual checklist. Properties and data stay API-managed. |
| Recent Notion API versions changed the database model ("data sources"). | Pin `Notion-Version` and record the version in this spec at N1. |
| The claude.ai Notion connector might not create **toggleable** headings, or insert above an existing block. The REST API can (`position: start`, confirmed 2026-10-16), but the connector is a separate tool. | Test at N6 before writing the instructions. Fallback: append revisions at the bottom, oldest first, with the header callout still naming the latest. |
| A relation "limit 1" setting might not be settable through the API. | Set it by hand in the master databases. `okr_pull` also rejects pages with more than one KR. |
| Rate limits (about 3 requests/second). | Hash-skip unchanged pages. Weekly volume is small: about 40 dashboard writes, 70 Tartt pages, and a handful of to-dos. |
| The operator edits a mirrored field in Notion. | The next push overwrites it (S2). Each database's description says which fields are yours. |
