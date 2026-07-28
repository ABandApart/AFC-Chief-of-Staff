"""Unit tests for the cost-emission helper (post-W1.2 telemetry model).

Covers:
  - successful Anthropic / Gemini / embedding calls write a valid agent_runs row
  - the soft daily breaker (agent + global ceiling) blocks on entry, no row
  - `assert_under_ceiling` callable directly (the cognee path)
  - provider error → `failed` row; unknown model refuses before the paid call
  - unknown agent → ValueError

G1 (per-run token cap) and per-agent keys were removed in W1.2 — their tests
are gone. All tests use mocks: no real API calls, no real DB writes.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest

from agents._lib.runs import (
    DailyCeilingExceeded,
    _l2_normalize,
    agent_run,
    assert_under_ceiling,
)

# =============================================================================
# Test infrastructure: capturing what gets written to agent_runs
# =============================================================================


class FakeCursor:
    """Records INSERT statements for inspection."""

    def __init__(self, today_spend: float = 0.0, global_spend: float | None = None):
        self.today_spend = today_spend
        # The breaker reads (agent_spend, global_spend) in one query.
        self.global_spend = today_spend if global_spend is None else global_spend
        self.inserts: list[tuple[str, tuple[Any, ...]]] = []
        self.select_count = 0

    def execute(self, query: str, params: tuple[Any, ...] | None = None) -> None:
        if "INSERT INTO agent_runs" in query:
            self.inserts.append((query, params or ()))
        elif "SELECT COALESCE(SUM(usd_cost)" in query:
            self.select_count += 1

    def fetchone(self) -> tuple[float, float]:
        return (self.today_spend, self.global_spend)

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


class FakeConnection:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor

    def cursor(self) -> FakeCursor:
        return self._cursor

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *args: Any) -> None:
        pass


def _patch_pool(monkeypatch, cursor: FakeCursor) -> None:
    """Route agents._lib.db.connection() to a FakeConnection."""

    @contextmanager
    def fake_connection():
        yield FakeConnection(cursor)

    monkeypatch.setattr("agents._lib.db.connection", fake_connection)


@pytest.fixture
def fake_cursor():
    """Returns a FakeCursor that captures agent_runs INSERTs."""
    return FakeCursor()


@pytest.fixture
def patched_db(fake_cursor, monkeypatch):
    """Patch the shared pool to a FakeConnection and keychain to test values;
    reset the cached SDK clients so each test's provider mocks are used."""
    _patch_pool(monkeypatch, fake_cursor)
    monkeypatch.setattr(
        "agents._lib.runs._keychain_get",
        lambda name: {
            "db-url": "postgresql://test:test@localhost/test",
            "anthropic-api-key": "sk-ant-test-key",
            "gemini-api-key": "AIza-test-key",
        }.get(name, "test-placeholder"),
    )
    monkeypatch.setattr("agents._lib.runs._ANTHROPIC_CLIENT", None)
    monkeypatch.setattr("agents._lib.runs._GENAI_CLIENT", None)
    return fake_cursor


# =============================================================================
# Successful calls write a valid agent_runs row
# =============================================================================


def test_successful_anthropic_call_writes_valid_row(patched_db, monkeypatch):
    """A successful Anthropic call produces a success row with accurate tokens
    and usd_cost — and makes no count_tokens round trip (G1 is gone)."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text="Hello world")]
    mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response
    monkeypatch.setattr("agents._lib.runs.anthropic.Anthropic", lambda **kw: mock_client)

    with agent_run("phase-2-smoke", "infrastructure") as run:
        result = run.call_anthropic(
            messages=[{"role": "user", "content": "Hi"}],
            model="claude-haiku-4-5",
            max_output_tokens=500,
        )

    assert result == "Hello world"
    assert len(patched_db.inserts) == 1
    _, params = patched_db.inserts[0]
    # order: agent, function, trigger, started, ended, status, provider, model,
    #        in, out, usd, corr_id, corr_kind, error
    assert params[0] == "phase-2-smoke"
    assert params[5] == "success"
    assert params[6] == "anthropic"
    assert params[7] == "claude-haiku-4-5"
    assert params[8] == 100 and params[9] == 50
    assert params[10] == pytest.approx(0.00035)  # 100*1e-6 + 50*5e-6
    assert params[13] is None
    mock_client.messages.count_tokens.assert_not_called()


def test_successful_gemini_call_writes_valid_row(patched_db, monkeypatch):
    mock_response = MagicMock()
    mock_response.text = "Gemini result"
    mock_response.usage_metadata = MagicMock(prompt_token_count=80, candidates_token_count=40)

    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = mock_response
    monkeypatch.setattr("agents._lib.runs.genai.Client", lambda **kw: mock_client)

    with agent_run("phase-2-smoke", "news_aggregation") as run:
        result = run.call_gemini(
            prompt="Summarize this", model="gemini-2.5-flash", max_output_tokens=500,
        )

    assert result == "Gemini result"
    _, params = patched_db.inserts[0]
    assert params[5] == "success"
    assert params[6] == "gemini"
    assert params[8] == 80 and params[9] == 40
    mock_client.models.count_tokens.assert_not_called()


# =============================================================================
# Soft daily breaker (agent + global) — blocks on entry, writes no row
# =============================================================================


def test_daily_ceiling_exceeded_writes_no_row(monkeypatch):
    """At/over the agent's ceiling → raise on entry, no row (next invocation
    blocked)."""
    fake_cursor = FakeCursor(today_spend=0.50)  # == phase-2-smoke ceiling
    _patch_pool(monkeypatch, fake_cursor)

    with pytest.raises(DailyCeilingExceeded) as exc:
        with agent_run("phase-2-smoke", "infrastructure"):
            pass

    assert "spent $0.5000" in str(exc.value)
    assert "blocked" in str(exc.value)
    assert len(fake_cursor.inserts) == 0


def test_global_ceiling_exceeded_writes_no_row(monkeypatch):
    """System-wide spend at/over GLOBAL_DAILY_CEILING blocks even when the agent
    is under its own ceiling."""
    fake_cursor = FakeCursor(today_spend=0.10, global_spend=25.0)
    _patch_pool(monkeypatch, fake_cursor)

    with pytest.raises(DailyCeilingExceeded) as exc:
        with agent_run("phase-2-smoke", "infrastructure"):
            pass

    assert "System-wide" in str(exc.value)
    assert len(fake_cursor.inserts) == 0


def test_assert_under_ceiling_direct_over_and_under(monkeypatch):
    """assert_under_ceiling is callable standalone (the cognee path)."""
    over = FakeCursor(today_spend=6.0)  # cognee ceiling is 5.0
    _patch_pool(monkeypatch, over)
    with pytest.raises(DailyCeilingExceeded):
        assert_under_ceiling("cognee")

    under = FakeCursor(today_spend=1.0)
    _patch_pool(monkeypatch, under)
    assert_under_ceiling("cognee")  # no raise


# =============================================================================
# Provider error → failed row; unknown model refuses before the paid call
# =============================================================================


def test_provider_error_writes_failed_row(patched_db, monkeypatch):
    mock_client = MagicMock()
    mock_client.messages.create.side_effect = RuntimeError("Simulated rate limit hit")
    monkeypatch.setattr("agents._lib.runs.anthropic.Anthropic", lambda **kw: mock_client)

    with pytest.raises(RuntimeError):
        with agent_run("phase-2-smoke", "infrastructure") as run:
            run.call_anthropic(
                messages=[{"role": "user", "content": "Hi"}],
                model="claude-haiku-4-5", max_output_tokens=500,
            )

    _, params = patched_db.inserts[0]
    assert params[5] == "failed"
    assert "RuntimeError" in params[13]
    assert "Simulated rate limit hit" in params[13]


def test_unpriced_model_refuses_before_call(patched_db, monkeypatch):
    """A model missing from PRICE_TABLE raises BEFORE the paid call; the run is
    recorded as failed."""
    mock_client = MagicMock()
    monkeypatch.setattr("agents._lib.runs.anthropic.Anthropic", lambda **kw: mock_client)

    with pytest.raises(ValueError) as exc:
        with agent_run("phase-2-smoke", "infrastructure") as run:
            run.call_anthropic(
                messages=[{"role": "user", "content": "Hi"}],
                model="claude-not-a-model", max_output_tokens=500,
            )

    assert "PRICE_TABLE" in str(exc.value)
    mock_client.messages.create.assert_not_called()
    assert patched_db.inserts[0][1][5] == "failed"


def test_unknown_agent_raises_value_error(monkeypatch):
    """An agent with no DAILY_CEILINGS entry raises ValueError before any DB
    access."""
    with pytest.raises(ValueError) as exc:
        with agent_run("nonexistent-agent", "infrastructure"):
            pass
    assert "DAILY_CEILINGS" in str(exc.value)


def test_correlation_fields_persisted(patched_db, monkeypatch):
    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text="Result")]
    mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response
    monkeypatch.setattr("agents._lib.runs.anthropic.Anthropic", lambda **kw: mock_client)

    with agent_run(
        "phase-2-smoke", "infrastructure",
        correlation_id="42", correlation_kind="content_item",
    ) as run:
        run.call_anthropic(
            messages=[{"role": "user", "content": "Hi"}],
            model="claude-haiku-4-5", max_output_tokens=500,
        )

    _, params = patched_db.inserts[0]
    assert params[11] == "42"
    assert params[12] == "content_item"


def test_structured_call_returns_tool_input(patched_db, monkeypatch):
    """call_anthropic_structured forces the named tool and returns its
    schema-validated input dict, with cost recorded normally."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.name = "record_facts"
    tool_block.input = {"facts": [{"content": "A fact.", "confidence": 1.0}]}

    mock_response = MagicMock()
    mock_response.content = [tool_block]
    mock_response.usage = MagicMock(input_tokens=120, output_tokens=60)
    mock_client = MagicMock()
    mock_client.messages.create.return_value = mock_response
    monkeypatch.setattr("agents._lib.runs.anthropic.Anthropic", lambda **kw: mock_client)

    with agent_run("phase-2-smoke", "infrastructure") as run:
        out = run.call_anthropic_structured(
            messages=[{"role": "user", "content": "note text"}],
            model="claude-haiku-4-5", max_output_tokens=600,
            tool_name="record_facts", tool_description="Record extracted facts.",
            input_schema={"type": "object"},
        )

    assert out == {"facts": [{"content": "A fact.", "confidence": 1.0}]}
    create_kwargs = mock_client.messages.create.call_args.kwargs
    assert create_kwargs["tool_choice"] == {"type": "tool", "name": "record_facts"}
    assert create_kwargs["tools"][0]["name"] == "record_facts"
    _, params = patched_db.inserts[0]
    assert params[5] == "success"
    assert params[8] == 120 and params[9] == 60


# =============================================================================
# Embeddings (gemini-embedding-001 @ 768 dims, L2-normalized)
# =============================================================================


def test_l2_normalize_produces_unit_vector():
    out = _l2_normalize([3.0, 4.0])
    assert out == pytest.approx([0.6, 0.8])
    assert sum(x * x for x in out) ** 0.5 == pytest.approx(1.0)


def test_l2_normalize_zero_vector_unchanged():
    assert _l2_normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]


def test_call_embedding_requests_768_and_normalizes(patched_db, monkeypatch):
    captured: dict[str, Any] = {}

    class FakeEmbedding:
        def __init__(self, values):
            self.values = values

    class FakeResult:
        def __init__(self, embeddings):
            self.embeddings = embeddings

    class FakeModels:
        def embed_content(self, *, model, contents, config):
            captured["model"] = model
            captured["dim"] = config.output_dimensionality
            captured["n"] = len(contents)
            return FakeResult([FakeEmbedding([3.0, 4.0, 0.0, 0.0]) for _ in contents])

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = FakeModels()

    monkeypatch.setattr("agents._lib.runs.genai.Client", lambda **kwargs: FakeClient())

    with agent_run("phase-2-smoke", "infrastructure") as run:
        out = run.call_embedding(["alpha", "beta"], model="gemini-embedding-001")

    assert captured["model"] == "gemini-embedding-001"
    assert captured["dim"] == 768
    assert captured["n"] == 2
    assert out[0] == pytest.approx([0.6, 0.8, 0.0, 0.0])
    _, params = patched_db.inserts[0]
    assert params[6] == "gemini"
    assert params[7] == "gemini-embedding-001"
