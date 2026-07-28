"""Unit tests for the W1 telemetry-context shim (M1).

No litellm required — the contextvar + row-writer logic is tested directly by
patching the shared db pool, mirroring test_runs.py's fake-cursor pattern.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from agents._lib import telemetry_context as tc


class FakeCursor:
    def __init__(self):
        self.inserts: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        if "INSERT INTO agent_runs" in query:
            self.inserts.append((query, params or ()))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class FakeConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def _patch_pool(monkeypatch, cur):
    @contextmanager
    def fake_connection():
        yield FakeConn(cur)
    monkeypatch.setattr("agents._lib.db.connection", fake_connection)


T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)


# --- labeled() contextvar -------------------------------------------------


def test_labeled_sets_and_resets():
    assert tc._current_label.get() is None
    with tc.labeled("fact-extraction", "customer_discovery", correlation_id="msg-1") as run_id:
        lbl = tc._current_label.get()
        assert lbl is not None
        assert lbl.agent_name == "fact-extraction"
        assert lbl.correlation_id == "msg-1"
        assert lbl.run_id == run_id
    assert tc._current_label.get() is None  # reset on exit


def test_labeled_defaults_correlation_to_run_id():
    with tc.labeled("cognee", "infrastructure") as run_id:
        assert tc._current_label.get().correlation_id == run_id


# --- price map ------------------------------------------------------------


def test_price_for_matches_prefixed_model():
    provider, i, o = tc._price_for("anthropic/claude-haiku-4-5")
    assert provider == "anthropic" and i == 1.0 / 1e6 and o == 5.0 / 1e6


def test_price_for_unknown_is_zero():
    provider, i, o = tc._price_for("some/mystery-model")
    assert (provider, i, o) == ("unknown", 0.0, 0.0)


# --- _record_call writes a conformant row ---------------------------------


def test_record_call_labeled_row(monkeypatch):
    cur = FakeCursor()
    _patch_pool(monkeypatch, cur)
    with tc.labeled("fact-extraction", "customer_discovery", correlation_id="msg-9"):
        tc._record_call(model="anthropic/claude-haiku-4-5", input_tokens=100,
                        output_tokens=50, started_at=T0, ended_at=T1)
    assert len(cur.inserts) == 1
    _, p = cur.inserts[0]
    # column order matches runs.py: agent, function, trigger, started, ended,
    # status, provider, model, in, out, usd, corr_id, corr_kind, error
    assert p[0] == "fact-extraction"
    assert p[1] == "customer_discovery"
    assert p[5] == "success"
    assert p[6] == "anthropic"
    assert p[8] == 100 and p[9] == 50
    assert p[10] == 0.00035  # 100*1e-6 + 50*5e-6
    assert p[11] == "msg-9"
    assert p[12] == "cognify_run"


def test_record_call_unlabeled_defaults_to_cognee(monkeypatch):
    cur = FakeCursor()
    _patch_pool(monkeypatch, cur)
    # no labeled() block
    tc._record_call(model="gemini-embedding-001", input_tokens=10, output_tokens=0,
                    started_at=T0, ended_at=T1)
    _, p = cur.inserts[0]
    assert p[0] == "cognee" and p[1] == "unlabeled"
    assert p[6] == "gemini" and p[12] == "cognify_run"


def test_usage_from_reads_object_and_dict():
    class U:
        prompt_tokens = 7
        completion_tokens = 3
    model, i, o = tc._usage_from({"model": "x"}, type("R", (), {"usage": U()})())
    assert (model, i, o) == ("x", 7, 3)
    model2, i2, o2 = tc._usage_from(
        {"model": "y", "usage": {"prompt_tokens": 4, "completion_tokens": 2}}, None
    )
    assert (model2, i2, o2) == ("y", 4, 2)
