# Handoff — Discord bot connection pool wedges when Postgres restarts

<doc:meta>
  <doc:from>barry-agent (runtime), 2026-09-09</doc:from>
  <doc:to>barry-admin (build)</doc:to>
  <doc:kind>Bug + build spec — durable fix is code, so it is yours, not a runtime step</doc:kind>
  <doc:status>IMPLEMENTED 2026-09-10 (`agents/_lib/db.py` + `tests/test_db.py`). The unit/config fix and its verification (S3 #1) are built and green; the live bounce test (S3 #2) still needs Postgres-owner (barry-admin) access to stop/start the server under the running bot.</doc:status>
  <doc:severity>High — a Postgres bounce takes the **entire** Discord bot offline (all cogs), silently, until a manual restart. Postgres bounces on every machine reboot here, so this recurs.</doc:severity>
  <doc:touches>`agents/_lib/db.py` (both pools); every cog that polls the DB.</doc:touches>
</doc:meta>

## TL;DR

The operator asked why the Gate-0 review surface looked stalled. It was two separate
things, and only the second is a bug:

1. **Not a bug.** Nothing new has *surfaced* for review since 2026-08-25 because the
   only firms that clear the ≥2-verification bar (`MIN_VERIFICATION_KINDS = 2`,
   `outreach_discovery._eligible`) — 9 of them — were **already surfaced by 8/25 and are
   awaiting operator decisions**. The poll is idempotent and correctly posts nothing new.
   The upstream verification/evidence throughput is a **design** matter the operator is
   handling separately; it is out of scope here.

2. **A bug, uncovered while investigating.** After today's Postgres restart, the running
   Discord bot cannot get a database connection: **135 × `PoolTimeout: couldn't get a
   connection after 30.00 sec`** in `logs/discord-bot.log`, across *every* polling cog
   (approvals, outreach_intake, task_tinder, outreach_rescore, outreach_discovery). The
   whole bot is locked out of the DB — not just Gate 0.

## Evidence

- **Fresh processes connect fine; the long-lived bot does not.** Every diagnostic query in
  this session ran through the *same* `agents/_lib/db.py` pool and the *same* keychain
  `db-url` (role `barry_agent`) that the bot uses, and succeeded. So Postgres is healthy and
  the credentials/endpoint are correct — the problem is specific to the bot's already-open
  pool.
- **Timeline** (`logs/discord-bot.log`, times are log-local ≈ wall +~2h):
  - `2026-09-03 18:39` — `gate 0: re-attached 3 persistent view(s) covering 9 row(s)`; Gate-0
    accept/reject decisions logged. Bot healthy.
  - `2026-09-09 17:45` — `gate 0: failed to list surfaced pages for re-attach` and
    `gate 0: could not read the review window`, both bottoming out in
    `psycopg_pool ... PoolTimeout: couldn't get a connection after 30.00 sec`
    (`agents/_lib/db.py:68`, `_get_pool().connection()`), and the same PoolTimeout repeating
    for `approvals._poll`, `outreach_intake._poll`, etc.
- **Bot process** PID 38695, started `Wed Sep 9 13:45:04 2026`, still PoolTimeout-ing ~2h
  later — it does **not** self-heal.

## Root cause

`agents/_lib/db.py` builds both pools with no resilience to a server that goes away and
comes back:

```python
ConnectionPool(
    creds.keychain_get("db-url"),
    min_size=0,
    max_size=4,
    open=True,
    kwargs={"autocommit": True},
)   # _get_pool() and _get_ro_pool() are identical in shape
```

There is **no `check`, no `max_lifetime`, and no socket-level timeout** on the connections.
When Postgres restarts under the running bot, borrowed connections are left holding a dead
TCP socket with nothing to time them out; the operations on them never return, so all four
slots stay occupied. Every subsequent `getconn` then waits the full 30 s and raises
`PoolTimeout`. Nothing recycles the stranded slots, so the bot stays wedged until the
process is restarted.

This recurs on **every reboot**, because Postgres always dies on reboot here until
barry-admin logs into the GUI session (the standing known issue). So "reboot → operator
starts Postgres → the bot is silently dead anyway" is the default outcome today.

## Fix (proposed — outcome, so it can be checked)

**Outcome (S1):** after Postgres restarts underneath the running bot, the bot recovers DB
access on its own within one poll cycle (≤ ~2 min) — no manual bot restart — and
`logs/discord-bot.log` shows the cogs resuming their normal reads/writes with no sustained
`PoolTimeout` run.

Make both pools in `agents/_lib/db.py` survive a bounced server:

- **`check=ConnectionPool.check_connection`** — validate a connection on checkout so a dead
  one is discarded and replaced instead of handed out or left to hang.
- **`max_lifetime`** (e.g. 30–60 min) — cap connection age so stale sockets are retired even
  absent a crash.
- **A connection-level socket timeout** so an operation on a dead socket fails fast instead
  of pinning its slot forever — set on the DSN/`kwargs` (e.g. `connect_timeout`, plus
  libpq TCP keepalives `keepalives=1`, `keepalives_idle`, and/or a session
  `statement_timeout` via the connection's `options`). Pick the mechanism and values
  during the build; the requirement is only that a dead connection cannot hold a slot
  indefinitely.

Apply to **both** `_get_pool()` and `_get_ro_pool()` — the read-only pool has the identical
defect.

**Verification (S3):**
1. Unit/integration: a pool built this way, pointed at a server that is stopped and
   restarted mid-use, hands out a working connection on the next borrow rather than raising
   `PoolTimeout`.
2. Live: with the bot running, `brew services stop postgresql@17 && brew services start
   postgresql@17` (as barry-admin), then confirm within a couple of minutes that the Gate-0
   and intake polls resume in the log with no ongoing PoolTimeout, and a new Gate-0 sheet /
   intake card can still be posted and clicked.

## Non-goals

- **Not** the verification-bar / evidence-throughput design work (why only 9 firms are
  eligible) — separate, operator-owned.
- **Not** the accept-vs-promote / trigger-gating question (27 accepted, 7 promoted) —
  separate.
- **Not** a change to pool size, roles, or the keychain credential model.
- **Not** an attempt to keep Postgres alive across reboots — that is the known
  login-keychain/GUI issue and stays out of scope; this fix only makes the bot *recover*
  once Postgres is back.

## Open decisions (for the build, surfaced not decided)

- Exact `max_lifetime` and timeout values, and whether to use `check` on every borrow
  (small latency cost) vs. rely on `max_lifetime` + socket timeout alone.
- Whether to add a lightweight startup gate so the bot, if launched while Postgres is still
  down, waits/retries rather than coming up degraded (a reboot commonly starts the bot
  before barry-admin has started Postgres).

## Immediate runtime mitigation (barry-agent — does NOT need this build)

Restarting the bot rebuilds the pool against the now-healthy Postgres and restores all cogs,
including the Gate-0 surface (the 9 pending review sheets re-attach on startup):

```bash
launchctl kickstart -k gui/$(id -u)/com.aiadaptive.cos.discord-bot
```

barry-agent can run this now on the operator's go-ahead; it is independent of the code fix
above. The code fix is what stops it recurring on the next reboot.
