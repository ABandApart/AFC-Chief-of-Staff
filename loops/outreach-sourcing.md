---
name: outreach-sourcing
schedule: "0 5 * * 1-5"
trigger_kind: scheduled
enabled: true
command: uv run python -m agents.outreach_sourcing.run
description: Research agent that surfaces at least 10 approvable outreach candidates each weekday (PRD-outreach-autonomous-sourcing).
---

# Autonomous outreach sourcing

Runs `agents/outreach_sourcing/run.py` at **05:00 on weekdays**. It completes the
verification of stuck pool rows first, then plans new-firm research with
`claude-opus-5-5` and runs `claude-sonnet-5-5` research workers in parallel, each
with Claude web search and fetch. Every dossier is checked by code before it is
stored; the Gate 0 cog surfaces what passed in `#outreach` from 05:30.

**Spends.** Outreach group ceiling $20.00/day (with `outreach-discover` and
`trent-crimm`); system ceiling $25.00/day. Every model call and web search is in
`agent_runs`, correlated by run id. Yesterday's spend appears in the 06:00 briefing.

**Stops** when N candidates pass (§6.4), the budget is spent, or 07:00 arrives,
and posts one line to `#outreach` if it fell short.

**Enabled 2026-11-17** on the operator's go-ahead: the V6 probe (`v6-20261112T225020`, 5 firms, $1.40) was judged accurate (N-D20, `PRD-notion-integration.md` N8 R5). It shipped disabled until:
1. migration `0029_outreach_autonomous_sourcing.sql` is applied;
2. V6, the worker quality probe: a `--dry-run` on the runtime box, reviewed by the
   operator for accuracy and cost per candidate.

## Browser research (N10 Part B)

When the operator's **CoS research** Chrome answers on CDP, each worker also gets
three read-only tools, run by `agents/_lib/browser.py`: `browser_open` (any page
except ZoomInfo and Glassdoor, at most 20,000 characters of text),
`linkedin_company` (headcount band, HQ, industry), and `linkedin_people` (names
and titles). The run checks the browser once at start (`COS_BROWSER_CDP`, default
`http://127.0.0.1:9222`) and writes a `browser:` line in the run's notes. If Chrome
doesn't answer, the run uses the server-side tools only, as before; a missing
browser never fails a run.

Start Chrome as barry-agent with a profile used only for research, then sign in
to LinkedIn in it by hand (the CoS never signs in):

```sh
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --user-data-dir="$HOME/Library/Application Support/CoS-Research-Chrome" \
  --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 \
  --no-first-run --no-default-browser-check
```

Check it listens on loopback only: `lsof -nP -iTCP:9222 -sTCP:LISTEN` shows
`127.0.0.1:9222`.

**LinkedIn limits (N-D31).** At most 25 LinkedIn page loads a day and 3 per
researched firm, 8 to 20 seconds apart, shared by every worker and process. A
sign-in wall, checkpoint, or CAPTCHA stops LinkedIn for the rest of the day; the
tools then return `linkedin_unavailable`, and the run's notes end with a
`LinkedIn today: ... STOPPED` line. Check the profile's LinkedIn session by hand.
The day's counters are in `~/.local/state/aiadaptive-cos/linkedin-limiter.json`
(override with `COS_BROWSER_STATE`); delete the file to clear a stop early.

**Rules.** LinkedIn evidence counts toward at most one of the two independent
domains a candidate needs (N-D32), so a firm found only on LinkedIn fails
`thin_evidence`. A contact found on LinkedIn without an email has method
`linkedin_profile`; it is stored as `none_found` with the profile in
`contact_linkedin_url` until a migration widens the `contact_method` CHECK.

Nothing here sends mail (B2, G3).
