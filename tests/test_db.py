"""The shared pools must be built resilient to a Postgres bounce.

A Postgres restart under the long-lived bot used to wedge the whole process
(`architecture/HANDOFF-2026-09-09-discord-pool-wedge.md`). These tests pin the
fix — `check` on checkout, a bounded `max_lifetime`, and socket-level keepalives
/ connect timeout — on BOTH pools, so a future edit cannot silently drop it or
let the two pools drift apart again.

They mock `ConnectionPool` and `creds.keychain_get`, so they need neither a live
Postgres nor runtime credentials (they pass on the build box). The end-to-end
behaviour — bounce Postgres under the running bot, confirm recovery within a poll
cycle with no sustained PoolTimeout — is the live step in the handoff (S3 #2),
which needs Postgres-owner (barry-admin) access to stop/start the server.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from psycopg_pool import ConnectionPool

from agents._lib import db


def _capture_pool_kwargs():
    """Build both pools with ConnectionPool + keychain mocked; return the kwargs
    each pool was constructed with, keyed by the DSN passed in."""
    db.close_pool()  # reset the module-level singletons
    dsns = {"db-url": "postgresql://rw@h/aiadaptive_cos",
            "brain-reader-db-url": "postgresql://ro@h/aiadaptive_cos"}
    with patch.object(db, "ConnectionPool", MagicMock()) as mock_pool, \
         patch.object(db.creds, "keychain_get", side_effect=lambda k: dsns[k]):
        db._get_pool()
        db._get_ro_pool()
    calls = {}
    for call in mock_pool.call_args_list:
        args, kwargs = call
        calls[args[0]] = kwargs
    db.close_pool()
    return calls


def test_both_pools_are_built_bounce_resilient():
    calls = _capture_pool_kwargs()
    # Both the read-write and the read-only pool were built.
    assert set(calls) == {"postgresql://rw@h/aiadaptive_cos",
                          "postgresql://ro@h/aiadaptive_cos"}
    for dsn, kwargs in calls.items():
        # check= must be the REAL check_connection (self-heal on next borrow).
        assert kwargs["check"] is ConnectionPool.check_connection, dsn
        # A bounded lifetime so a stranded socket cannot hold a slot forever.
        assert 0 < kwargs["max_lifetime"] <= 60 * 60, dsn
        # Autocommit preserved, plus socket-level fail-fast on a dead peer.
        conn_kwargs = kwargs["kwargs"]
        assert conn_kwargs["autocommit"] is True, dsn
        assert conn_kwargs["keepalives"] == 1, dsn
        assert conn_kwargs["keepalives_idle"] > 0, dsn
        assert conn_kwargs["connect_timeout"] > 0, dsn


def test_both_pools_share_identical_resilience_config():
    """The two pools must not drift apart — the read-only pool having missed the
    fix is exactly the defect the handoff called out."""
    calls = _capture_pool_kwargs()
    rw = calls["postgresql://rw@h/aiadaptive_cos"]
    ro = calls["postgresql://ro@h/aiadaptive_cos"]
    for key in ("check", "max_lifetime", "min_size", "max_size"):
        assert rw[key] == ro[key], key
    assert rw["kwargs"] == ro["kwargs"]


def test_concurrent_first_borrowers_build_exactly_one_pool(monkeypatch):
    """Cogs re-attaching views at startup borrow at the same moment; they must
    share one pool, not each build (and orphan) their own (2026-10-05)."""
    import threading
    import time

    from agents._lib import db

    builds: list[str] = []

    class FakePool:
        def close(self) -> None:
            pass

    def slow_build(dsn: str) -> FakePool:
        builds.append(dsn)
        time.sleep(0.05)  # widen the window the lock must close
        return FakePool()

    monkeypatch.setattr(db, "_build_pool", slow_build)
    monkeypatch.setattr(db.creds, "keychain_get", lambda item: item)
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_ro_pool", None)

    results: list[object] = []
    threads = [threading.Thread(target=lambda: results.append(db._get_pool()))
               for _ in range(8)]
    threads += [threading.Thread(target=db._get_ro_pool) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert builds.count("db-url") == 1
    assert builds.count("brain-reader-db-url") == 1
    assert len({id(r) for r in results}) == 1
    db.close_pool()
