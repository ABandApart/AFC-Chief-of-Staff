# Ted, increment 1: ops and handoff friction monitor — PRD & Build Spec

**Status (2026-11-18): ADOPTED with amendments; increment 1 BUILT, loops disabled.** Drafted by barry-agent at the operator's request; reviewed and adopted by barry-admin when the operator said "build this". Where the draft and the **Amendments** section below disagree, the Amendments win. The draft text is kept as written so the review stays readable.
**Asked of barry-admin:**
- Review it against the project history.
- Correct anything that contradicts what's already built or decided.
- Answer the open decisions in §6 with the operator.
- If you accept it, move it into `architecture/` and log it in `70-build-order.md`.

How to reply is in §10.

Builds on:
- `80-telemetry-layer.md` §"Ted's Telemetry Responsibilities", §"Who Watches Ted", and §"As-built (PERF-4)"
- `70-build-order.md` Phase 11 and the 2026-08-08 external-witness decision
- `loops/README.md` (ships disabled, two-account activation)
- `/Users/Shared/afc-richmond/README.md` (handoff protocol)

---

## TL;DR

**Ted is specced but has never been built.** `80-` lists `cos-ted` as "still to wire (Phase 11)".

**What it cost on 2026-11-18:** the Anthropic credit ran out overnight.
- `tartt`, `outreach-sourcing`, and `outreach-discover` failed for about 12 hours.
- The 05:00 sourcing run did nothing (`stop_reason = credit`).
- Nobody knew until the operator asked for a manual probe at about 12:05.

**The friction in the handoff loop:** every step between barry-agent and barry-admin was relayed by the operator in chat (N10, "Pre-check" through "Fix validated"). Also, `CURRENT-PHASE.txt` has pointed at N4 since 2026-10-29.

**This increment builds Ted's reactive check:**
- pure SQL, so it keeps running when the LLM credit doesn't;
- extended with fleet-wide credential classification and handoff hygiene;
- elevated off-box to the operator (healthchecks.io, as settled);
- elevated into barry-admin's next Claude Code session automatically, through an append-only inbox and a session-start hook, with no operator relay.

## 1. Evidence (barry-agent, from `agent_runs` and `outreach_sourcing_runs`, 7 days to 2026-11-18)

| Signal | Data | Detected by |
|---|---|---|
| Credit outage | 46 `credit balance is too low` failures across 3 agents, 00:00 to 06:02 | Operator-requested probe, about 6 h after the last failure |
| tartt Gemini failures | 43× 503 UNAVAILABLE, 37× 429 RESOURCE_EXHAUSTED; 7–30% of runs daily | Nobody (not in any handoff) |
| Stale phase pointer | `CURRENT-PHASE.txt` = N4; N7–N10 written since | Nobody |
| Operator relay | 5 hand-offs in N10 on 2026-11-18, each triggered "in chat" | n/a: the operator *is* the transport |
| Leftover LaunchAgents | `sourcing-oneoff` (fired 2026-10-07; GOLIVE says boot out), `outreach-spotcheck` | Nobody |

Full write-up: `REPORT-ops-friction-2026-11-18.md`. Running inbox: `INBOX-admin.md`.

## 2. Goal & non-goals

**Goal.** A friction or failure that affects the agent team reaches the operator and barry-admin without anyone having to look for it, including when the LLM provider, Discord, or the operator's attention is unavailable.

**Non-goals (S2):**
- **No auto-remediation.** Ted reports; it does not restart, retry, edit code, change permissions, or touch credentials. Remediation stays with the two accounts and the operator, as today.
- **No replacement of the dead-man's switches.** The per-loop `cos-*` heartbeats stay exactly as built. Ted is one more loop under that regime, not a second watchdog (`80-` "What this deliberately does not do").
- **No LLM on the detection path.** It may be added later only for phrasing (`80-` already allows Haiku for that); increment 1 has none.
- **No Discord as the primary channel.** This is settled in `80-`.
- **Not in this increment:**
  - G3 token anomaly detection;
  - ceiling proximity;
  - the outreach invariants (`80-` Ted item 6);
  - the `#system` pinned status;
  - the briefing's `last_ted_run_at` line.

  Each is a later Ted increment (see §6, D7).
- **Not a log project.** Log rotation and plain tracebacks (240 MB / 131 MB unrotated logs) are a separate small item.
- **Out of scope here:** the tartt Gemini policy. Ted surfaces it; the fix (quota, backoff, or a fallback model) is its own decision.

## 3. Outcomes (S1): observable, checkable by someone else

| # | Outcome |
|---|---|
| **O1** | Within **one Ted cycle** of the first credit or auth failure (`credit balance`, 401, `authentication_error`, `invalid x-api-key`) in **any** agent, the operator gets **one** off-box alert (email or push). It names the failure class and every affected agent. The same incident does not alert again within 24 h. |
| **O2** | An agent whose failures in the last 24 h are at or above the threshold (§6 D3) produces one finding naming the agent, the counts, and the most common error text. |
| **O3** | An agent that ran on 3 or more days in the prior 9 and has been silent for 48 h produces a finding. A finished one-off batch (e.g. `trent-crimm`, 1 day) does not. |
| **O4** | A non-dry `outreach_sourcing_runs` row whose `stop_reason` is anything other than `target_met`, `no_briefs`, or `deadline` produces a finding. |
| **O5** | **Handoff hygiene.** If `CURRENT-PHASE.txt` names a file other than the newest `PHASE-*.md`, a finding is produced. |
| **O6** | **Every finding** lands as a dated entry in `/Users/Shared/afc-richmond/INBOX-admin.md`. **Every new barry-admin Claude Code session** starts with the unacknowledged entries in its context, with no operator action. |
| **O7** | An URGENT finding with no `ack:` after 24 h is raised again, once per 24 h, until it is acknowledged. |
| **O8** | Ted itself is watched. `cos-ted` pings on the success path each cycle, so a dead or wedged Ted alerts through healthchecks.io like every other loop. |
| **O9** | Ted completes a cycle with **no** Anthropic, Gemini, or OpenAI credential present. This is the property that would have caught 2026-11-18. |

## 4. Settled (binding; cited, not re-decided)

- **Off-box alerting goes to email or push, never Discord**, because the failing system is where the alert would not appear. The healthchecks.io project is armed: barry-agent's keychain has `healthchecks-ping-key` (checked 2026-11-18). (`80-`, 2026-08-08 decision log)
- **Heartbeat rules:** `ping()` on the success path only, never in `finally:`; `ping_fail()` on a caught exception; a ping never raises. (`agents/_lib/heartbeat.py`)
- **Ted computes anomalies in pure SQL/Python, not Higgins.** (decision log 2026-05-14)
- **A new loop ships `enabled: false`.** barry-admin flips it, and barry-agent pulls. (`loops/README.md`, 2026-08-14)
- **New tables need `ALTER TABLE … OWNER TO barry_agent`.** (`CLAUDE.md`, migration 0011)
- **barry-agent does not change repo code.** barry-admin has no runtime credentials. (`CLAUDE.md`)
- **Spec-driven development, S1–S6.** (`70-`)

## 5. Design (proposed; barry-admin owns the build shape)

**5.1 Detector.**
- `agents/ted/` with a `loops/ted.md` manifest (`command:` variant).
- Each check is a SQL query over `agent_runs`, `outreach_sourcing_runs`, and the shared folder's file metadata.
- A working prototype of all checks plus dedupe is `ops_check-prototype.sh` (zsh + psql). It was run live on 2026-11-18 and reproduced every §1 finding except the credit class (outside its 6 h window by then).

**5.2 State.**
- Proposed: a table `ted_findings (key PK, severity, text, first_seen, last_raised, acked_at)`.
- It drives dedupe (O1), re-raise (O7), and ack tracking.
- Alternative: the existing `dashboard` table, or a state file (§6 D5).

**5.3 Elevation to the operator.**
- `cos-ted` is liveness only (O8).
- Findings go out separately (§6 D1). Recommendation: a second check, `cos-ted-alert`:
  - `ping_fail` with the finding text as the request body on a cycle with a new URGENT or HIGH finding;
  - a plain success ping on a clean cycle, so healthchecks sends a recovery notice.
- **Verify before building (V0):** does the healthchecks.io notification channel the operator uses include the ping body?

**5.4 Elevation to barry-admin.**
- Ted appends to `INBOX-admin.md`. Each entry has a date, severity, key, text, one likely-cause line, and an `ack:` line.
- On barry-admin's side, a Claude Code **SessionStart hook** in barry-admin's settings prints the entries whose `ack:` is empty. This is the piece that removes the operator relay.
- Optionally, a `launchd` `WatchPaths` job under barry-admin raises a macOS notification when the file changes.

**5.5 Permissions.**
- The folder is `drwxrwxrwx`, so both accounts create files freely.
- The catch: a file one account creates is `644` and can't be edited by the other unless the README's `chmod 666` step runs. barry-agent's `chmod` on 2026-11-18 was blocked by its session's permission layer.
- For the inbox, the proposal is **one writer per file**:
  - Ted writes `INBOX-admin.md`;
  - barry-admin writes acks to `ACK-admin.md` (key + date + disposition);
  - Ted reads both.
- That avoids cross-account edits entirely. See §6 D4.

## 6. Open decisions (S4): recommendations, not settled

| # | Decision | Options | Recommendation |
|---|---|---|---|
| D1 | How findings reach the operator | (a) `cos-ted-alert` with `/fail` + body; (b) `/fail` on `cos-ted` itself; (c) email via another path | **(a)**: keeps `cos-ted` an honest liveness signal |
| D2 | Ted's cycle | 6 h (`80-`) vs hourly | **Hourly.** Pure SQL is cheap, and 6 h would have left the 2026-11-18 outage unseen until about 06:00 |
| D3 | Failure-rate threshold | `80-`: >3 failed in 6 h; prototype: ≥4 and ≥25% in 24 h | **Both**: the 6 h count for acute, the 24 h rate for chronic (tartt) |
| D4 | Ack mechanism | (a) separate `ACK-admin.md`; (b) edit in place after `chmod`; (c) group + default ACL on the folder (operator, one-time `chmod +a`) | **(a) now; (c) if the operator wants a durable fix** |
| D5 | Where state lives | new `ted_findings` table vs `dashboard` vs file | **Table** (migration + `OWNER TO barry_agent` + `verify_schema.sql`) |
| D6 | Also post to `#system`? | yes, as a secondary, vs no | **Secondary only**, after the off-box path works |
| D7 | Scope of later increments | G3, ceilings, outreach invariants, `last_ted_run_at` in briefing | Increment 2 = outreach invariants + `last_ted_run_at`; increment 3 = G3 + ceilings |
| D8 | Judgment layer (the kind of report in §1: relay dependence, stale pointers) | headless Claude weekly vs a section in the N4 Monday dashboard push vs none | **Defer.** Revisit after a month of increment-1 data |
| D9 | Handoff-hygiene checks beyond O5 | unchecked `[BARRY-AGENT]` items older than N days; handbacks with no admin reply | Recommend adding after D4 settles the ack format |

## 7. Verification (S3), named before the build

| # | Check | Pass when |
|---|---|---|
| V0 | Send a test `/fail` with a body to a scratch check | The operator's email or push shows the body (decides D1) |
| V1 | Unit tests: each detector against fixture rows (credit, threshold, silent-vs-one-off, sourcing stop reasons, stale pointer, dedupe, re-raise) | All pass without optional dep groups |
| V2 | Live: barry-agent inserts one labelled failed row (`agent_name='ted-selftest'`, `error_text` containing `credit balance`) and runs Ted once | Within one cycle: one off-box alert, one `INBOX-admin.md` entry; a second run produces neither. Then the row is deleted |
| V3 | Live: run Ted with no LLM credential in its environment | Completes a cycle and pings `cos-ted` (O9) |
| V4 | Open a fresh barry-admin Claude Code session | The unacked V2 entry is in its first context (O6) |
| V5 | Add an ack for V2 in `ACK-admin.md`; wait 24 h | No re-raise. Without the ack, exactly one re-raise (O7) |
| V6 | Disable `loops/ted.md` for one period plus grace | `cos-ted` alerts (O8) |
| V7 | Point `CURRENT-PHASE.txt` at an older file in a scratch copy of the folder | O5 finding |

## 8. Human / credential actions

| # | Who / profile | Step |
|---|---|---|
| H1 | operator, any browser | Create the `cos-ted` check (period = D2, grace 15 m) and, if D1(a), `cos-ted-alert` on healthchecks.io |
| H2 | barry-admin (operator approves) | Install the SessionStart hook in barry-admin's Claude Code settings |
| H3 | operator, only if D4(c) | One-time folder ACL so both accounts can edit each other's files |

## 9. Interim (until Ted ships)

barry-agent is running the prototype from an interactive Claude Code session:
- three times a day (about 07:17, 13:17, and 19:17);
- appending to `INBOX-admin.md` and pushing to the operator's terminal.

Its limits:
- It is session-bound and expires on 2026-11-25.
- It does not reach barry-admin's sessions.
- It is not a substitute for O1–O9.

## 10. How to reply (barry-admin)

Write your review to **`PRD-ted-ops-monitor-REVIEW.md`** in this folder, your own file, so no `chmod` is needed. The useful parts:
1. **Contradictions with history:** anything here that a past decision, PRD, or as-built note already settled differently. Especially anything about Ted and Higgins that `80-` or the decision log says that I've missed.
2. **What's already built** that this duplicates or should reuse, e.g. existing `#system` health posts, `cli/control.py`, or `kr_metrics` health lines.
3. **D1–D9:** your recommendation where it differs, and which ones need the operator.
4. **Shape changes:** module layout, migration number, loop name, test plan.

When you adopt it, move it into `architecture/` with your changes, and note `ack: <date> adopted as architecture/PRD-…` in `ACK-admin.md`.


---

## Amendments and build notes (barry-admin, 2026-11-18)

**Adopted.** The operator said "build this" after reading the review, so the review's recommendations are the decisions below. Two remain the operator's: D1 confirmation in practice (whether a Notion mention reaches them at 06:00) and the SessionStart hook install (H2).

### Decisions as built

| # | Decision |
|---|---|
| D1 | **(d):** URGENT and HIGH findings go to the operator as urgent Briefing-page notes (`briefing_page.system_note(urgent=True)`, a Notion @-mention). `cos-ted` is liveness. If an alert can't be delivered, the cycle pings `cos-ted` `/fail`, so healthchecks.io carries it off-box. No `cos-ted-alert` check. |
| D2 | Hourly (`loops/ted.md`, `:07`). Amends `80-` (was 6 h). |
| D3 | Chronic: >= 4 failures and >= 25% in 24 h. Acute: 2 failures in a row (the N9 rule). Credit and auth: first occurrence, one finding per class across all agents. |
| D4 | (a): Ted writes `INBOX-admin.md`; barry-admin writes `ACK-admin.md` (`ack: <key> <date> <note>`). |
| D5 | Table `ted_findings`, migration `0040`. |
| D6 | No `#system`. Superseded by D1. |
| D7 | Ceiling proximity (80%) is in increment 1. Increment 2: outreach invariants and Ted's timestamp on the Briefing page. Increment 3: G3. |
| D8 | **Built**: a weekly headless Claude Code session on the Claude subscription (below). |
| D9 | Deferred. |

### D8: the headless judgment session (operator, 2026-11-18)

- The operator asked whether Ted could use a Claude Code session instead of the API. Built as `agents/ted/judgment.py`, run weekly by `loops/ted-judgment.md` (Mondays 07:30).
- Ted gathers the evidence with SQL and file metadata and passes it in the prompt. The session runs `claude -p` with **no tools** (`--tools ""`), `--restricted --strict-mcp-config`, `--json-schema`, and an empty working directory. It never reads the database or the disk and can't write.
- **No API key:** the launcher strips `ANTHROPIC_API_KEY` and the other API routes from the environment, and passes `CLAUDE_CODE_OAUTH_TOKEN` from barry-agent's keychain item `claude-oauth-token` (made once with `claude setup-token`). A session whose init message reports an API key source fails instead of billing the API.
- **`--bare` can't be used:** it reads only an API key, never the subscription sign-in (Claude Code 2.1.282, checked 2026-11-18).
- Findings are MEDIUM or INFO, validated in code, prefixed `judge:`, and go to the inbox only.
- One `agent_runs` row per session: `ted` / `telemetry`, provider `anthropic-subscription`, `usd_cost` 0. The CLI's notional cost is printed, not booked: the subscription has no per-call price, and booking it would count against the API ceilings.
- **Open (operator):** whether the subscription's terms cover unattended, scheduled use. The operator is treating it as a hypothesis to test (H4, E2 below).
- **Hypothesis result, barry-admin account (2026-11-18):** with the `claude` CLI signed in to the Max account (`subscriptionType: max`, `planTier: max_5x`) and `ANTHROPIC_API_KEY=invalid` exported, `--probe` returned `pong` with `apiKeySource: none`. A full `--judgment --dry-run` on live evidence returned valid structured output: 10 findings, none dropped, about 9,300 input and 3,400 output tokens. So the subscription answers a headless, tool-less session. Still to confirm: barry-agent with a `setup-token` token (E2), and the plan terms.
- **Evidence fixes from that run:** the first review called the weekly `trent-crimm` batch silent and misread checklist items. The evidence now carries each agent's run days and every loop's schedule, and full checklist text.

### Also in this change

- **`cos-sourcing` on a credit stop** now pings `/fail`, not alive (review §1.3).
- **`scripts/ted_inbox_hook.py`:** the SessionStart hook for barry-admin. It prints only a count and the path, never the findings. Not installed (H2 needs the operator's OK).
- **Not built from the draft:** the `launchctl` service check (the scheduler being down stops Ted too; `cos-scheduler` covers it), and the macOS notification watcher (§5.4, optional).

### Human actions

| # | Who | Step |
|---|---|---|
| H1 | operator | Create `cos-ted` on healthchecks.io: period 1 h, grace 15 min. |
| H2 | operator, then barry-admin | Approve installing `scripts/ted_inbox_hook.py` as a SessionStart hook in barry-admin's Claude Code settings. |
| H3 | operator | Approve applying `0040_ted_findings.sql`. |
| H4 | operator, signed in as barry-agent | Run `claude setup-token`, sign in with the Max account, and store the token: `security add-generic-password -U -s claude-oauth-token -a barry-agent -w` (prompts for the value). |

### Verification added

| # | Check | Pass when |
|---|---|---|
| V8 | `uv run python -m agents.ted.run --probe` as barry-agent, after H4, with `ANTHROPIC_API_KEY=invalid` exported | `result: OK`, `apiKeySource: none`. This is the hypothesis test. |
| V9 | `--judgment --dry-run` | A summary and validated findings print; nothing is written. |
| V10 | Unit tests | Off-schema findings are dropped; URGENT from the model is dropped (`tests/test_ted.py`). |

### Review

The full review is `PRD-ted-ops-monitor-REVIEW.md` in the shared folder (2026-11-18); its decisions are folded in above.
