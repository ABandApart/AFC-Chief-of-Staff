# Liveness alerting — arm the dead-man's switch and close its coverage gaps

<doc:meta>
  <doc:type>PRD & build spec</doc:type>
  <doc:status>BUILT builder-side 2026-10-02 (branch `liveness-alerting`). Not armed: §1 operator setup and V1–V5 runtime checks are pending. Open: O1, O2, O4; O3 partly settled (see As-built).</doc:status>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:depends_on>`80-telemetry-layer.md` §dead_mans_switch (PERF-4, as-built 2026-08-08); `agents/_lib/heartbeat.py`</doc:depends_on>
  <doc:related>`PRD-session-independent-services.md` (the companion spec: keep the services running; this one says when they are not)</doc:related>
  <doc:size>Code: three call sites and one startup check, plus tests. Operator setup: about 20 minutes on healthchecks.io and one keychain item.</doc:size>
</doc:meta>

## Why this exists

On 2026-09-24 at 09:35:26, the Discord bot, gateway, and scheduler all received
SIGTERM and exited cleanly. launchd did not relaunch them, because
`KeepAlive.SuccessfulExit=false` relaunches only after a crash. The nightly backup
stopped too. The system stayed down for **8 days**, and the operator found out by
noticing it, not by an alert. The Cloudflare tunnel had separately been failing
since about 2026-09-10 (missing token file), also without an alert.

The dead-man's switch designed for exactly this (PERF-4) is built and wired into
five loops, but it did not fire. The likely reason: `heartbeat.py` **no-ops until
`healthchecks-ping-key` is provisioned**, and that operator step was never done.
That is an inference: barry-admin cannot read barry-agent's keychain. Either way,
"un-armed" and "broken" looked identical, which is the first gap. The second is
coverage: the bot, the gateway, the tunnel, and Postgres have no check at all.

## Outcome (S1)

**When any component in the table below stops working, the operator receives an
email or phone push naming that component, within that component's period plus
grace, and with no dependency on Discord or on the Mac mini being up.**

A second, smaller outcome: **an un-armed switch is never silent.** If the ping key
is missing, the system says so where the operator will see it.

## Design

### 1. Arm it (operator step, no code)

The setup listed in `80-` §"As-built" was never completed. Do it:

1. Create the healthchecks.io project and its checks (table below).
2. Point the project's notifications at an **off-Discord** channel (open decision O1).
3. Add `healthchecks-ping-key` to barry-agent's keychain.

If `PRD-session-independent-services.md` changes where credentials live, the ping
key moves with the rest; `heartbeat.py` reads through `creds.keychain_get`, so
it follows automatically.

### 2. Close the coverage gaps

Existing checks are unchanged. Four new ones:

| Check | Pinged by | Ping when | Ping every | Period | Grace | New? |
|---|---|---|---|---|---|---|
| `cos-scheduler` | scheduler daemon, `_maybe_beat` | each cycle | 5 min | 1h | 15m | existing |
| `cos-briefing` | morning-briefing loop | after posting | daily | 24h | 1h | existing |
| `cos-backup` | `scripts/pg_backup.sh` | exit 0 | daily | 24h | 2h | existing |
| `cos-outreach-evidence` | evidence poller | success | 12h | 12h | 2h | existing |
| `cos-outreach-bcc` | BCC poller | live pass only | 15 min | 15m | 10m | existing (create once bcc@ IMAP exists) |
| **`cos-bot`** | Discord bot, a `tasks.loop` in an existing cog | `bot.is_ready()` and the gateway connection is open | 5 min | 15m | 15m | **new** |
| **`cos-gateway`** | gateway, a background task started in the app lifespan | `GET http://127.0.0.1:8788/health` returns 200 | 5 min | 15m | 15m | **new** |
| **`cos-tunnel`** | gateway, same background task | `GET https://<public-hostname>/health` returns 200 through the tunnel | 5 min | 15m | 15m | **new** |
| **`cos-brain`** | scheduler daemon, beside `_maybe_beat` | `SELECT 1` succeeds on both `aiadaptive_cos` and `aiadaptive_cognee` | 5 min | 15m | 15m | **new** |

Why each new check is shaped this way:

- **`cos-bot`** pings only when the bot is connected. A process that is alive but
  disconnected from Discord is down from the operator's point of view.
- **`cos-gateway`** calls its own `/health` over HTTP rather than pinging from inside
  the handler, so the check proves the server is accepting connections, not only
  that the event loop runs.
- **`cos-tunnel`** goes out through Cloudflare and back. It is the only check that
  would have caught the 2026-09-10 tunnel failure. It runs in the gateway, so if the
  gateway is down, both `cos-gateway` and `cos-tunnel` alert; the pair reads
  correctly ("gateway down" vs "tunnel down, gateway fine").
- **`cos-brain`** is separate from `cos-scheduler` because the rule in `80-` is one
  check per failure, so the alert names the failure. A scheduler that cycles while
  Postgres is down is a different problem from a scheduler that has stopped.

All new pings follow the existing rules in `heartbeat.py`: ping only on the success
path, never in `finally:`, and never raise.

### 3. Make the un-armed state loud

At startup, the scheduler checks whether `healthchecks-ping-key` resolves. If it
does not, it posts once to `#system`: "Dead-man's switch is UN-ARMED: no
`healthchecks-ping-key`. Outages will not alert." It also logs the same line at
WARNING. The scheduler is chosen because it starts on every boot and already owns
`cos-scheduler`.

This does not block startup. Monitoring must not be able to stop the work it
monitors (the `heartbeat.py` rule). Discord is acceptable here, unlike for outage
alerts, because this message is sent while the system is up.

## Non-goals (S2)

- **No automatic restart.** Keeping services running is
  `PRD-session-independent-services.md`. This spec only reports.
- **No alerts through Discord.** `80-` already settles this: the failing system is
  where the alert would not appear.
- **No on-box watchdog process.** `80-` rejects it: the box being off is not
  observable from the box.
- **No per-loop checks for loops the scheduler runs** (Granola, Tartt, outreach
  classify and others). `cos-scheduler` covers "the schedule stopped". Per-loop
  failures stay with Ted (Phase 11).
- **The briefing's staleness line** (second layer in `80-`) stays with Phase 4.
- **No escalation or on-call rotation.** There is one operator.

## Verification (S3)

| # | Check | Pass when |
|---|---|---|
| V1 | Key provisioned | As barry-agent, `security find-generic-password -s healthchecks-ping-key -w` exits 0. |
| V2 | All checks green | Within 30 minutes of the services starting, every check in the table (except `cos-outreach-bcc` if IMAP is not set up) shows "up" on the healthchecks.io dashboard. |
| V3 | Bot alert fires | `launchctl kill SIGTERM gui/$(id -u)/com.aiadaptive.cos.discord-bot`. An alert naming `cos-bot` arrives within 30 minutes. `kickstart` the bot; a recovery notice follows. |
| V4 | Gateway and tunnel alerts fire | Stop the gateway: both `cos-gateway` and `cos-tunnel` alert within 30 minutes. Restart it. Then stop only cloudflared (`sudo launchctl kill SIGTERM system/com.cloudflare.cloudflared`): only `cos-tunnel` alerts. |
| V5 | Scheduler and brain alerts fire | Stop the scheduler: `cos-scheduler` and `cos-brain` alert within 75 minutes. Postgres is not stopped in testing; the `cos-brain` query failure path is covered by V7. |
| V6 | Un-armed warning | In a test, `creds.keychain_get` raises for the ping key; the scheduler startup posts the `#system` warning once. |
| V7 | Unit tests | Each new pinger: pings on success; does not ping when its condition fails (bot not ready, `/health` non-200, tunnel request error, `SELECT 1` raises); never raises. Suite passes without the optional dependency groups. |

V3 to V5 are barry-agent runtime steps, handed over through
`/Users/Shared/afc-richmond/`.

## Settled (S4)

- healthchecks.io stays the provider (`80-`, 2026-08-08).
- Push-based pings from the work itself, not polling (`80-`).
- One check per failure, success-path only, never raises (`heartbeat.py`).

## Open decisions (S4)

| # | Decision | Options | What it changes |
|---|---|---|---|
| O1 | **Where alerts go** | Email to an address you read on your phone; healthchecks.io's mobile push (Pushover or ntfy integration); SMS (paid tier) | Whether an alert reaches you within minutes or at your next inbox check. |
| O2 | **Alert latency for always-on services** | 15m period + 15m grace (proposed, about 30 minutes to alert); tighter (5m + 5m) at the cost of alerts during a slow restart | Noise vs speed. A planned restart longer than the grace will alert. |
| O3 | **Is `/health` reachable through the tunnel?** | Already routed (verify); add a route; or make `cos-tunnel` call a HMAC-authenticated endpoint instead | Whether `cos-tunnel` needs a tunnel-config change, and whether an unauthenticated `/health` is acceptable on the public hostname. It returns no data, but it does confirm the host exists. |
| O4 | **Planned maintenance** | Pause checks by hand on healthchecks.io before planned work; or accept the alerts | Operator habit only; no code. |

## Verify before building

1. **Was the key ever provisioned?** barry-agent runs V1. If it was provisioned and
   no alert fired on 2026-09-24, the cause is different (checks never created, or
   notifications not configured) and this spec's "why" section must be corrected.
2. **The public hostname and whether `/health` is routed** (O3), from
   `PRD-b3-tunnel.md` and the Cloudflare dashboard.
3. **Free-tier limit.** Nine checks against the roughly 20 that `80-` cites. Confirm
   the current limit before creating them.

## As-built (2026-10-02)

- `agents/_lib/heartbeat.py`: `is_armed()`.
- `agents/scheduler/run.py`: `cos-brain` via `brain_ok()` and `Scheduler._maybe_check_brain` (same 5-minute cadence as the beat, separate method so `cos-scheduler` is unchanged); `warn_if_unarmed()` at daemon start, posting through `post_system_notice()` (Discord REST, best-effort).
- `agents/discord_bot/cogs/system.py`: `cos-bot` via `SystemCog.liveness`, a 5-minute `tasks.loop`.
- `agents/gateway/liveness.py`: `cos-gateway` and `cos-tunnel` from one probe thread, started in `app.main()`.
- Tests: `tests/test_gateway_liveness.py`, `tests/test_system_cog.py`, and additions to `tests/test_heartbeat.py` and `tests/test_scheduler.py` (V6, V7).

**Deviation from the spec, recorded rather than patched over:** the spec did not say where `cos-tunnel` gets the public URL. It reads a new optional keychain item, `gateway-public-url` (for example `https://<hostname>`), kept out of git like the PRD-b3 hostname. While the item is absent, the tunnel probe is skipped and logs "un-armed" once. Whether `/health` is routed through the tunnel (O3) is still unverified.

**Operator setup, in addition to §1:** add `gateway-public-url` to barry-agent's keychain, and create the four new checks: `cos-bot`, `cos-gateway`, `cos-tunnel`, `cos-brain` (each 15m period, 15m grace).

## Risks

- **The ping key is a credential.** If it is leaked, someone can send fake "up" pings
  and mask an outage. It is low value but must stay in the same credential store as
  every other secret.
- **Alert fatigue on reboots.** A reboot longer than the grace window alerts on four
  or more checks at once. Accept it, or pause checks first (O4).
- **External dependency.** If healthchecks.io itself fails, nothing alerts. The
  Phase 4 briefing staleness line is the backstop for that.
