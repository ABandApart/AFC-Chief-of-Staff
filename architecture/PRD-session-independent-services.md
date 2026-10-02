# Session-independent services — run the runtime as LaunchDaemons

<doc:meta>
  <doc:type>PRD & build spec</doc:type>
  <doc:status>DRAFT 2026-10-02 — for operator review. Blocked on open decision D1 (where credentials live). Only the VB2 probe is built: `scripts/vb2_keychain_daemon_probe.sh`.</doc:status>
  <doc:owner>Barry Baldwin</doc:owner>
  <doc:depends_on>`PRD-liveness-alerting.md` (build first, so this migration is observable); `launchd/README.md`; `agents/_lib/creds.py`</doc:depends_on>
  <doc:supersedes>The "user LaunchAgents in barry-agent's session" model in `launchd/README.md` (Phase 3.5)</doc:supersedes>
  <doc:size>Four plists rewritten, one credential seam in `creds.py`, an install script, and a README rewrite. No schema change. The size of D1 depends on the option chosen.</doc:size>
</doc:meta>

## Why this exists

The runtime only runs while someone is logged in. The bot, gateway, and scheduler
are **user LaunchAgents** in barry-agent's GUI session, and Postgres is a Homebrew
LaunchAgent in barry-admin's session. On 2026-09-24 at 09:35, a logout stopped all
of them. Postgres came back when barry-admin logged in again; nothing else did, for
8 days.

`launchd/README.md` records why it was built this way: "the keychain that holds
`discord-bot-token` / `db-url` is only unlocked in a logged-in user session — a
system LaunchDaemon can't read it". **So this is a credentials problem first and a
launchd problem second.** Moving the plists is mechanical; deciding where the
secrets live (D1) is the real decision.

## Outcome (S1)

**With no user logged in, the Discord bot, gateway, scheduler, and Postgres are
running, and they stay running through any user logging in or out.** After a
reboot, they are running within 5 minutes of the disk being unlocked, with no one
logging in.

Observable as: every check in `PRD-liveness-alerting.md` stays green through a
logout of both accounts, and goes green within 5 minutes of a reboot.

## Scope

### What moves

| Service | Today | After |
|---|---|---|
| Discord bot | barry-agent LaunchAgent `com.aiadaptive.cos.discord-bot` | LaunchDaemon, same label, `UserName=barry-agent` |
| Gateway | barry-agent LaunchAgent `com.aiadaptive.cos.gateway` | LaunchDaemon, same label, `UserName=barry-agent` |
| Scheduler | barry-agent LaunchAgent `com.aiadaptive.cos.scheduler` | LaunchDaemon, same label, `UserName=barry-agent` |
| Postgres 17 | barry-admin Homebrew LaunchAgent `homebrew.mxcl.postgresql@17` | LaunchDaemon, `UserName=barry-admin` (owner of the data directory) |
| cloudflared | Already a LaunchDaemon | Unchanged. Its missing token file is an operations fix, not part of this spec. |

The briefing and nightly backup **do not need plists**. They already run as
scheduler loops (`loops/morning-briefing.md`, `loops/nightly-backup.md`), so they
follow the scheduler. The old `com.aiadaptive.cos.briefing` and
`com.aiadaptive.cos.pg-backup` plists in `launchd/` are dead and get deleted in
this change.

### Plist shape

Each daemon plist lives in `/Library/LaunchDaemons/`, owned `root:wheel`, mode
644, and keeps its current label, so log paths and handoff notes don't change. The
differences from today's agents:

- **`UserName` / `GroupName`** set explicitly. launchd runs daemons as root
  otherwise.
- **`EnvironmentVariables`** sets `HOME=/Users/barry-agent` and a `PATH` that
  includes `uv`. A daemon does not get a login environment. HOME also matters
  because cognee, the FastEmbed model cache, and `uv` all resolve paths under it.
- **`KeepAlive`** becomes `true` (relaunch on any exit), not
  `SuccessfulExit=false`. That setting is what kept the services down after a clean
  SIGTERM on 2026-09-24. Stopping a service on purpose becomes `bootout`, not
  `kill`.
- **`ThrottleInterval`** 10 seconds, so a crash loop doesn't spin.
- Startup order needs no launchd dependency. `agents/_lib/db.py` already builds its
  pool so "Postgres is down at launch" is survivable: the first borrow reconnects
  once Postgres is up.

### The credential seam

All secret reads already go through `creds.keychain_get(item)`, except two shell
scripts that call `security` directly (`scripts/pg_backup.sh`, for the ping key and
`db-url`). The change is to give `creds.py` one configured backend chosen by D1, and
route the two scripts through the same backend. No caller changes.

## Open decision D1 — where credentials live

This is the decision that blocks the build. Three options are live:

| | Option | How it works | Cost |
|---|---|---|---|
| **A** | **Dedicated runtime keychain** | A separate keychain file for barry-agent (for example `~/Library/Keychains/afc-runtime.keychain-db`) with auto-lock off. Each daemon's start wrapper unlocks it with `security unlock-keychain` before running the service. | Keeps the "secrets in Keychain" convention. But the unlock password has to be stored somewhere the daemon can read, which moves the problem rather than removing it. **Unverified:** whether a keychain unlocked by a process with no GUI session is readable by that process's `security` calls (VB2). |
| **B** | **Root-owned secrets file** | `/usr/local/etc/afc-richmond/secrets.env`, owned by barry-agent, mode 0400. `creds.py` reads it. | Simple and reliable under launchd. **Breaks the written convention** ("never in `.env` files", root `README.md`), which would need a decision-log entry. At rest, it is protected only by file permissions and FileVault. |
| **C** | **Keep the login keychain; auto-login barry-agent at boot** | Turn on automatic login for barry-agent so its session, and so its keychain, always exist. | Smallest change. **Doesn't meet the outcome**: a logout still stops everything. Automatic login is also unavailable while FileVault is on. Listed only because it is the obvious first idea. |

**Recommendation: A, if VB2 passes; otherwise B.** A keeps the existing convention
and the Keychain's at-rest encryption. B is the fallback whose cost is a written
convention change, not a technical risk. Either way, the keychain-write step stops
being "a human step in barry-agent's GUI session" (root `CLAUDE.md`). That line
must be updated in the same change.

## Other open decisions

| # | Decision | Options | What it changes |
|---|---|---|---|
| D2 | **FileVault and reboots** | If FileVault is on, a reboot stops at the disk-unlock screen and no daemon runs until someone types the password. Accept that (alerting from the companion spec covers it); or use `sudo fdesetup authrestart` for planned reboots | Whether "running after a reboot with no one present" is achievable at all. The outcome above already assumes the disk is unlocked. |
| D3 | **Who can restart services** | Daemons need `sudo launchctl`. barry-agent is not an admin. Options: restarts become a barry-admin `sudo` step; or a `sudoers` rule lets barry-agent run `launchctl kickstart` and `bootout` on these four labels only | The handoff flow in `CLAUDE.md`, which assumes barry-agent can restart its own services. |
| D4 | **Postgres as a daemon** | `sudo brew services start postgresql@17` (Homebrew writes the plist); or a hand-written plist in `launchd/`, kept in git | Whether the Postgres plist is reviewed through the git-gate like the others. A hand-written plist is recommended for that reason. |

## Non-goals (S2)

- **No change to account separation.** barry-admin builds; barry-agent runs.
  Daemons run *as* barry-agent.
- **No new host, containers, or process supervisor** beyond launchd.
- **No alerting.** That is `PRD-liveness-alerting.md`.
- **Not fixing the tunnel token.** cloudflared is already a daemon; restoring its
  token is an operations fix needed now, not a design change.
- **No change to what the services do.** Same commands, same logs, same labels.

## Verify before building

| # | Question | How | Blocks |
|---|---|---|---|
| VB1 | Is FileVault on? | `fdesetup status`, run by the operator | D2 |
| VB2 | Can a LaunchDaemon running as barry-agent unlock and read a dedicated keychain with no one logged in? | `scripts/vb2_keychain_daemon_probe.sh` (built 2026-10-02): a throwaway keychain with one dummy item, a test daemon that unlocks it and logs OK/FAIL each minute, then a logout of both accounts and a reboot. Steps are in the script header | D1 (A vs B) |
| VB3 | Do `uv`, cognee, and FastEmbed work with only the daemon's `HOME` and `PATH`? | Run a test daemon that runs `uv run python -c "import cognee"` and loads the FastEmbed model from cache | Plist shape |
| VB4 | Does Postgres start cleanly as a daemon with `UserName=barry-admin`? | Load the daemon plist after `bootout` of the Homebrew agent; check `pg_isready` and the Postgres log | D4 |

## Migration and rollback

Order matters. **Never run the agent and daemon versions of a service at the same
time.** Two schedulers would fire every loop twice (double ingest, double briefing,
double cognify spend), and two bots would compete for the same Discord session.

1. Build and verify `PRD-liveness-alerting.md` first, so every step below is visible
   on the healthchecks.io dashboard.
2. Resolve D1, then move secrets to the chosen backend. Leave the login-keychain
   copies in place until step 6.
3. For each service, one at a time: `bootout` the LaunchAgent, then `bootstrap` the
   LaunchDaemon. Wait for its check to go green before moving to the next.
   Postgres goes first.
4. Run V1 to V5.
5. Delete the old LaunchAgent plists from barry-agent's `~/Library/LaunchAgents/`
   and the dead `briefing` and `pg-backup` plists from the repo.
6. After a week with no issues, delete the login-keychain copies of the secrets.

**Rollback** for any service: `sudo launchctl bootout system/<label>`, then
`launchctl bootstrap gui/<uid>` with the old agent plist. The old plists stay in git
history, and the keychain copies stay until step 6.

## Verification (S3)

| # | Check | Pass when |
|---|---|---|
| V1 | Logout test | With all four daemons loaded, log out barry-agent and barry-admin. After 30 minutes, every liveness check is still green and the four PIDs are unchanged. |
| V2 | Reboot test | Reboot. After the disk is unlocked (if FileVault is on) and with no one logged in, every liveness check is green within 5 minutes. |
| V3 | Credentials with no session | During V2, with no one logged in: the bot's log shows it connected to Discord; an HMAC-signed `POST /ingest` through the tunnel returns 202; the scheduler runs one LLM loop that writes an `agent_runs` row. |
| V4 | Backup with no session | The 02:00 backup runs with no one logged in: a new file appears in `~/agents/backups/nightly/` and `cos-backup` stays green. |
| V5 | No duplicates | `launchctl list \| grep com.aiadaptive` in barry-agent's session returns nothing, and `ps` shows exactly one process per service. |
| V6 | Unit tests | `creds.py` reads from the configured backend and raises the same `RuntimeError` on a missing item as today. The plists in `launchd/` pass `plutil -lint`. The suite passes without the optional dependency groups. |

V1 to V4 are runtime steps that need both accounts and a reboot; they are
operator steps.

## Risks

- **A wider blast radius for a credential leak** if D1 is B: a readable file is
  easier to exfiltrate than a Keychain item. File mode 0400 and FileVault limit it.
- **`KeepAlive=true` relaunches a service you meant to stop** with `kill`. Stopping
  becomes `bootout`, and `launchd/README.md` must say so.
- **A reboot with FileVault on still needs a person.** The alerting spec is what
  makes that visible.
