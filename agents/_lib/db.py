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
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool

from agents._lib import creds

# System-wide embedding dimensionality; matches every vector(768) column.
EMBEDDING_DIM = 768

_pool: ConnectionPool | None = None
_ro_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            creds.keychain_get("db-url"),
            min_size=0,
            max_size=4,
            open=True,
            kwargs={"autocommit": True},
        )
    return _pool


def _get_ro_pool() -> ConnectionPool:
    """Pool authenticated as the read-only `brain_reader` role (Track I).

    Separate DSN (`brain-reader-db-url`) so the MCP tool layer's reads run over a
    role that can only SELECT the `v_*` views — defense in depth (migration 0008).
    Distinct from the read-write pool above.
    """
    global _ro_pool
    if _ro_pool is None:
        _ro_pool = ConnectionPool(
            creds.keychain_get("brain-reader-db-url"),
            min_size=0,
            max_size=4,
            open=True,
            kwargs={"autocommit": True},
        )
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
