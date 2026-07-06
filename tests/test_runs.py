"""Unit tests for the cost-emission helper.

Tests the four paths required by Phase 2 acceptance criteria:
  AC1: successful call writes a valid agent_runs row
  AC2: G1 token cap exceeded path writes `token_cap_exceeded` row
  AC3: G2 daily ceiling exceeded path raises BEFORE call, writes no row
  AC4: provider error path writes `failed` row with error_text

All tests use mocks — no real API calls, no real DB writes. Each test
captures what would have been written to agent_runs by intercepting the
psycopg cursor.execute call.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest

from agents._lib.runs import (
    DailyCeilingExceeded,
    MissingAgentKeyError,
    TokenCapExceeded,
    _l2_normalize,
    agent_run,
)

# =============================================================================
# Test infrastructure: capturing what gets written to agent_runs
# =============================================================================


class FakeCursor:
    """Records INSERT statements for inspection."""

    def __init__(self, today_spend: float = 0.0, global_spend: float | None = None):
        self.today_spend = today_spend
        # G2 reads (agent_spend, global_spend) in one query.
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
    """Patches the shared pool to a FakeConnection and keychain to test values.

    Also resets the cached SDK clients so each test's provider mocks are used.
    """
    _patch_pool(monkeypatch, fake_cursor)
    monkeypatch.setattr(
        "agents._lib.runs._keychain_get",
        lambda name: {
            "db-url": "postgresql://test:test@localhost/test",
            "anthropic-key-ted": "sk-ant-test-key",
            "gemini-api-key": "AIza-test-key",
        }.get(name, "test-placeholder"),
    )
    monkeypatch.setattr("agents._lib.runs._ANTHROPIC_CLIENTS", {})
    monkeypatch.setattr("agents._lib.runs._GENAI_CLIENT", None)
    return fake_cursor


# =============================================================================
# AC1: Successful call writes a valid agent_runs row
# =============================================================================


def test_successful_anthropic_call_writes_valid_row(patched_db, monkeypatch):
    """A successful Anthropic call should produce a row with status=success
    and accurate input_tokens, output_tokens, and usd_cost."""

    # Mock the Anthropic client
    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text="Hello world")]
    mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)

    mock_count = MagicMock(input_tokens=100)

    mock_client = MagicMock()
    mock_client.messages.count_tokens.return_value = mock_count
    mock_client.messages.create.return_value = mock_response

    monkeypatch.setattr(
        "agents._lib.runs.anthropic.Anthropic",
        lambda **kwargs: mock_client,
    )

    with agent_run("phase-2-smoke", "infrastructure") as run:
        result = run.call_anthropic(
            messages=[{"role": "user", "content": "Hi"}],
            model="claude-haiku-4-5",
            max_input_tokens=1000,
            max_output_tokens=500,
        )

    assert result == "Hello world"
    assert len(patched_db.inserts) == 1
    _, params = patched_db.inserts[0]
    # Params order: agent_name, function_label, trigger_kind, started_at, ended_at,
    #               status, llm_provider, llm_model, input_tokens, output_tokens,
    #               usd_cost, correlation_id, correlation_kind, error_text
    assert params[0] == "phase-2-smoke"
    assert params[1] == "infrastructure"
    assert params[5] == "success"
    assert params[6] == "anthropic"
    assert params[7] == "claude-haiku-4-5"
    assert params[8] == 100  # input_tokens
    assert params[9] == 50  # output_tokens
    # Haiku 4.5: $1/1M input + $5/1M output
    # = 100 * 1e-6 + 50 * 5e-6 = 1e-4 + 2.5e-4 = 3.5e-4 USD
    # Recorded at 8dp since migration 0002 — no boundary rounding.
    assert params[10] == pytest.approx(0.00035)
    assert params[13] is None  # error_text
    # Input was tiny: G1's local estimate must skip the count_tokens round trip.
    mock_client.messages.count_tokens.assert_not_called()


def test_successful_gemini_call_writes_valid_row(patched_db, monkeypatch):
    """A successful Gemini call should produce a row with status=success."""

    mock_response = MagicMock()
    mock_response.text = "Gemini result"
    mock_response.usage_metadata = MagicMock(
        prompt_token_count=80, candidates_token_count=40
    )

    mock_count = MagicMock(total_tokens=80)

    mock_client = MagicMock()
    mock_client.models.count_tokens.return_value = mock_count
    mock_client.models.generate_content.return_value = mock_response

    monkeypatch.setattr(
        "agents._lib.runs.genai.Client",
        lambda **kwargs: mock_client,
    )

    with agent_run("phase-2-smoke", "news_aggregation") as run:
        result = run.call_gemini(
            prompt="Summarize this",
            model="gemini-2.5-flash",
            max_input_tokens=1000,
            max_output_tokens=500,
        )

    assert result == "Gemini result"
    assert len(patched_db.inserts) == 1
    _, params = patched_db.inserts[0]
    assert params[5] == "success"
    assert params[6] == "gemini"
    assert params[7] == "gemini-2.5-flash"
    assert params[8] == 80
    assert params[9] == 40


# =============================================================================
# AC2: G1 token cap exceeded -> token_cap_exceeded row, raises TokenCapExceeded
# =============================================================================


def test_token_cap_exceeded_writes_failure_row(patched_db, monkeypatch):
    """When input tokens exceed max_input_tokens, raise TokenCapExceeded and
    write a row with status=token_cap_exceeded."""

    mock_count = MagicMock(input_tokens=50_000)  # way over cap
    mock_client = MagicMock()
    mock_client.messages.count_tokens.return_value = mock_count

    monkeypatch.setattr(
        "agents._lib.runs.anthropic.Anthropic",
        lambda **kwargs: mock_client,
    )

    with pytest.raises(TokenCapExceeded) as exc_info:
        with agent_run("phase-2-smoke", "infrastructure") as run:
            run.call_anthropic(
                messages=[{"role": "user", "content": "x" * 100_000}],
                model="claude-haiku-4-5",
                max_input_tokens=1000,
                max_output_tokens=500,
            )

    assert "50000" in str(exc_info.value)
    # The actual API call should NOT have been made
    mock_client.messages.create.assert_not_called()

    # A row WAS written, with status=token_cap_exceeded
    assert len(patched_db.inserts) == 1
    _, params = patched_db.inserts[0]
    assert params[5] == "token_cap_exceeded"
    assert "exceeds cap 1000" in params[13]  # error_text


# =============================================================================
# AC3: G2 daily ceiling exceeded -> raises BEFORE call, writes no row
# =============================================================================


def test_daily_ceiling_exceeded_writes_no_row(monkeypatch):
    """When today's spend is at or over the daily ceiling, raise immediately
    on context entry and write no agent_runs row."""

    # Simulate barely-over the $0.50 ceiling for phase-2-smoke
    fake_cursor = FakeCursor(today_spend=0.50)
    _patch_pool(monkeypatch, fake_cursor)

    with pytest.raises(DailyCeilingExceeded) as exc_info:
        with agent_run("phase-2-smoke", "infrastructure") as run:
            # This should never execute — raise happens on context entry
            run.call_anthropic(
                messages=[{"role": "user", "content": "Hi"}],
                model="claude-haiku-4-5",
                max_input_tokens=1000,
                max_output_tokens=500,
            )

    assert "spent $0.5000" in str(exc_info.value)
    assert "$0.50" in str(exc_info.value)
    # No row written for refused-before-start run
    assert len(fake_cursor.inserts) == 0


# =============================================================================
# AC4: Provider error -> writes `failed` row with error_text
# =============================================================================


def test_provider_error_writes_failed_row(patched_db, monkeypatch):
    """When the provider raises (e.g. rate limit, network error), write a
    row with status=failed and the error captured in error_text."""

    mock_count = MagicMock(input_tokens=100)
    mock_client = MagicMock()
    mock_client.messages.count_tokens.return_value = mock_count
    mock_client.messages.create.side_effect = RuntimeError(
        "Simulated rate limit hit"
    )

    monkeypatch.setattr(
        "agents._lib.runs.anthropic.Anthropic",
        lambda **kwargs: mock_client,
    )

    with pytest.raises(RuntimeError):
        with agent_run("phase-2-smoke", "infrastructure") as run:
            run.call_anthropic(
                messages=[{"role": "user", "content": "Hi"}],
                model="claude-haiku-4-5",
                max_input_tokens=1000,
                max_output_tokens=500,
            )

    assert len(patched_db.inserts) == 1
    _, params = patched_db.inserts[0]
    assert params[5] == "failed"
    assert "RuntimeError" in params[13]
    assert "Simulated rate limit hit" in params[13]


# =============================================================================
# Edge cases worth covering
# =============================================================================


def test_missing_agent_key_raises_before_call(patched_db, monkeypatch):
    """An agent not in KEY_BY_AGENT should raise MissingAgentKeyError when
    calling Anthropic (Gemini is shared so doesn't hit this)."""

    # Note: 'sam' is in DAILY_CEILINGS but NOT yet in KEY_BY_AGENT
    with pytest.raises(MissingAgentKeyError) as exc_info:
        with agent_run("sam", "infrastructure") as run:
            run.call_anthropic(
                messages=[{"role": "user", "content": "Hi"}],
                model="claude-haiku-4-5",
                max_input_tokens=1000,
                max_output_tokens=500,
            )

    assert "sam" in str(exc_info.value)
    assert "KEY_BY_AGENT" in str(exc_info.value)


def test_unknown_agent_raises_value_error(monkeypatch):
    """An agent with no DAILY_CEILINGS entry should raise ValueError."""
    monkeypatch.setattr(
        "agents._lib.runs._keychain_get",
        lambda name: "postgresql://test:test@localhost/test",
    )

    with pytest.raises(ValueError) as exc_info:
        with agent_run("nonexistent-agent", "infrastructure"):
            pass

    assert "DAILY_CEILINGS" in str(exc_info.value)


def test_correlation_fields_persisted(patched_db, monkeypatch):
    """correlation_id and correlation_kind should make it to the row."""

    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text="Result")]
    mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)

    mock_count = MagicMock(input_tokens=10)
    mock_client = MagicMock()
    mock_client.messages.count_tokens.return_value = mock_count
    mock_client.messages.create.return_value = mock_response

    monkeypatch.setattr(
        "agents._lib.runs.anthropic.Anthropic",
        lambda **kwargs: mock_client,
    )

    with agent_run(
        "phase-2-smoke",
        "infrastructure",
        correlation_id="42",
        correlation_kind="content_item",
    ) as run:
        run.call_anthropic(
            messages=[{"role": "user", "content": "Hi"}],
            model="claude-haiku-4-5",
            max_input_tokens=1000,
            max_output_tokens=500,
        )

    _, params = patched_db.inserts[0]
    assert params[11] == "42"  # correlation_id
    assert params[12] == "content_item"  # correlation_kind


# =============================================================================
# Embeddings (Phase 3.2 — gemini-embedding-001 @ 768 dims, L2-normalized)
# =============================================================================


def test_l2_normalize_produces_unit_vector():
    out = _l2_normalize([3.0, 4.0])
    assert out == pytest.approx([0.6, 0.8])
    assert sum(x * x for x in out) ** 0.5 == pytest.approx(1.0)


def test_l2_normalize_zero_vector_unchanged():
    assert _l2_normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]


def test_call_embedding_requests_768_and_normalizes(patched_db, monkeypatch):
    """call_embedding must request output_dimensionality=768 (gemini-embedding-001
    defaults to 3072) and return L2-normalized vectors, with a gemini agent_runs
    row."""
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
            # Return un-normalized [3,4,0,0] vectors (stand-in for 768-dim).
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
    # [3,4,0,0] L2-normalized → [0.6, 0.8, 0, 0]
    assert out[0] == pytest.approx([0.6, 0.8, 0.0, 0.0])
    # One agent_runs row, attributed to gemini.
    assert len(patched_db.inserts) == 1
    _, params = patched_db.inserts[0]
    assert params[6] == "gemini"
    assert params[7] == "gemini-embedding-001"


# =============================================================================
# 2026-07 refactor additions: global ceiling, pre-call pricing, structured call
# =============================================================================


def test_global_ceiling_exceeded_writes_no_row(monkeypatch):
    """System-wide spend at/over GLOBAL_DAILY_CEILING refuses on entry even
    when the individual agent is under its own ceiling."""
    fake_cursor = FakeCursor(today_spend=0.10, global_spend=25.0)
    _patch_pool(monkeypatch, fake_cursor)

    with pytest.raises(DailyCeilingExceeded) as exc_info:
        with agent_run("phase-2-smoke", "infrastructure"):
            pass

    assert "System-wide" in str(exc_info.value)
    assert len(fake_cursor.inserts) == 0


def test_unpriced_model_refuses_before_call(patched_db, monkeypatch):
    """A model missing from PRICE_TABLE must raise BEFORE the paid API call
    is made (the run itself is recorded as failed)."""
    mock_client = MagicMock()
    monkeypatch.setattr(
        "agents._lib.runs.anthropic.Anthropic",
        lambda **kwargs: mock_client,
    )

    with pytest.raises(ValueError) as exc_info:
        with agent_run("phase-2-smoke", "infrastructure") as run:
            run.call_anthropic(
                messages=[{"role": "user", "content": "Hi"}],
                model="claude-not-a-model",
                max_input_tokens=1000,
                max_output_tokens=500,
            )

    assert "PRICE_TABLE" in str(exc_info.value)
    mock_client.messages.create.assert_not_called()
    # The run wrote a failed row (money was never spent).
    assert len(patched_db.inserts) == 1
    assert patched_db.inserts[0][1][5] == "failed"


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

    monkeypatch.setattr(
        "agents._lib.runs.anthropic.Anthropic",
        lambda **kwargs: mock_client,
    )

    with agent_run("phase-2-smoke", "infrastructure") as run:
        out = run.call_anthropic_structured(
            messages=[{"role": "user", "content": "note text"}],
            model="claude-haiku-4-5",
            max_input_tokens=4000,
            max_output_tokens=600,
            tool_name="record_facts",
            tool_description="Record extracted facts.",
            input_schema={"type": "object"},
        )

    assert out == {"facts": [{"content": "A fact.", "confidence": 1.0}]}
    create_kwargs = mock_client.messages.create.call_args.kwargs
    assert create_kwargs["tool_choice"] == {"type": "tool", "name": "record_facts"}
    assert create_kwargs["tools"][0]["name"] == "record_facts"
    # Cost recorded as usual.
    _, params = patched_db.inserts[0]
    assert params[5] == "success"
    assert params[8] == 120
    assert params[9] == 60
