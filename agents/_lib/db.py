"""Shared Postgres access: one process-wide connection pool.

Replaces the per-operation `psycopg.connect()` pattern — a 3-fact capture
used to open 7+ connections. The pool starts empty (min_size=0) so one-shot
CLIs pay for at most one connection, while the long-running bot reuses
warm connections across captures.

Connections are handed out in autocommit mode (matching the previous
behavior); multi-statement atomic writes use `conn.transaction()`.

Also home to the embedding-dimension constant and the pgvector literal
formatter, which were previously duplicated across brain.py and recall.py.
"""

from __future__ import annotations

import atexit
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool

from agents._lib import creds

# System-wide embedding dimensionality; matches every vector(768) column.
EMBEDDING_DIM = 768

_pool: ConnectionPool | None = None
_ro_pool: ConnectionPool | None = None
# Guards lazy pool creation. Without it, threads borrowing at the same moment
# (the bot's cogs re-attaching views at startup) each build a pool; all but one
# are orphaned and garbage-collected on their own worker thread, which logs
# "cannot join current thread" (seen 2026-10-05).
_pool_lock = threading.Lock()

# --- Resilience to a bounced Postgres ---------------------------------------
# A Postgres restart under the long-lived Discord bot used to wedge the *entire*
# process: borrowed connections held dead sockets with no timeout, so every pool
# slot stayed pinned and every subsequent `getconn` hit PoolTimeout until a manual
# bot restart — and Postgres bounces on every reboot here. See
# `architecture/HANDOFF-2026-09-09-discord-pool-wedge.md`.
#
# The three defences, applied identically to BOTH pools (the read-only pool had
# the same defect):
#   check=       validate a connection on checkout — one left dead by a server
#                bounce is discarded and replaced rather than handed out, so the
#                pool self-heals on the next borrow once Postgres is back. The
#                per-borrow ping cost is negligible at the bot's poll rate, and it
#                is the strongest guarantee (open decision #1, resolved: use check).
#   max_lifetime retire connections by age, so a stranded socket cannot hold a
#                slot indefinitely even without a checkout.
#   keepalives / connect_timeout  bound blocking at the socket level so an
#                operation on a dead peer fails fast instead of hanging, and a
#                reconnect while Postgres is still down does not hang either.
_MAX_LIFETIME = 30 * 60  # seconds — within the spec's 30–60 min band
# Bound at import so a test that patches ConnectionPool still sees the real check.
_CHECK_CONNECTION = ConnectionPool.check_connection
_CONNECT_KWARGS: dict[str, object] = {
    "autocommit": True,
    "connect_timeout": 10,
    "keepalives": 1,
    "keepalives_idle": 10,
    "keepalives_interval": 5,
    "keepalives_count": 3,
}


def _build_pool(dsn: str) -> ConnectionPool:
    """Build a connection pool that survives a Postgres bounce (see note above).

    `min_size=0` keeps one-shot CLIs to at most one connection; `open=True` opens
    no connections eagerly at `min_size=0`, so construction does not fail if
    Postgres is down at launch — the first borrow reconnects once it is back.
    """
    return ConnectionPool(
        dsn,
        min_size=0,
        max_size=4,
        open=True,
        max_lifetime=_MAX_LIFETIME,
        check=_CHECK_CONNECTION,
        kwargs=dict(_CONNECT_KWARGS),
    )


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = _build_pool(creds.keychain_get("db-url"))
    return _pool


def _get_ro_pool() -> ConnectionPool:
    """Pool authenticated as the read-only `brain_reader` role (Track I).

    Separate DSN (`brain-reader-db-url`) so the MCP tool layer's reads run over a
    role that can only SELECT the `v_*` views — defense in depth (migration 0008).
    Distinct from the read-write pool above; both share the bounce-resilient
    `_build_pool` config so they cannot drift apart again.
    """
    global _ro_pool
    if _ro_pool is None:
        with _pool_lock:
            if _ro_pool is None:
                _ro_pool = _build_pool(creds.keychain_get("brain-reader-db-url"))
    return _ro_pool


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Borrow a pooled connection (autocommit). Returned to the pool on exit."""
    with _get_pool().connection() as conn:
        yield conn


@contextmanager
def ro_connection() -> Iterator[psycopg.Connection]:
    """Borrow a read-only (`brain_reader`) pooled connection (Track I reads)."""
    with _get_ro_pool().connection() as conn:
        yield conn


def close_pool() -> None:
    """Close the pools (clean shutdown of long-running processes)."""
    global _pool, _ro_pool
    with _pool_lock:
        if _pool is not None:
            _pool.close()
            _pool = None
        if _ro_pool is not None:
            _ro_pool.close()
            _ro_pool = None


# One-shot CLIs never reach an explicit close_pool(); without this, the
# pool's __del__ fires during interpreter finalization and Python 3.14
# raises PythonFinalizationError ("cannot join thread") on every run.
# Idempotent, so the bot's explicit close_pool() on SIGTERM is unaffected.
atexit.register(close_pool)


def vector_literal(embedding: list[float]) -> str:
    """Format a float list as a pgvector string literal: '[a,b,c]'.

    Inserted with an explicit `::vector` cast, which avoids needing the
    pgvector psycopg adapter (and its numpy dependency).
    """
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"
