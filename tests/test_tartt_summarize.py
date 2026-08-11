"""Unit tests for Tartt summarization (Phase 4, Task 2).

`build_prompt` is pure (title/body assembly + truncation). `summarize`'s wiring
is checked with `agent_run` mocked — it must run under the `tartt` /
`news_aggregation` label and call Gemini Flash — so no ceiling, DB, or network.
"""

from __future__ import annotations

from agents.tartt import summarize


def test_build_prompt_includes_title_and_truncates_body():
    p = summarize.build_prompt("My Title", "x" * 20_000, max_input_chars=100)
    assert "My Title" in p
    assert p.count("x") == 100  # body truncated to the bound


def test_build_prompt_untitled_fallback():
    assert "(untitled)" in summarize.build_prompt("   ", "some body")


def test_summarize_runs_under_tartt_label_and_calls_flash(mocker):
    fake_run = mocker.MagicMock()
    fake_run.call_gemini.return_value = "a concise summary"
    cm = mocker.MagicMock()
    cm.__enter__.return_value = fake_run
    cm.__exit__.return_value = False
    ar = mocker.patch.object(summarize, "agent_run", return_value=cm)

    out = summarize.summarize("Title", "body text", source_url="https://ex.com/1")

    assert out == "a concise summary"
    # Labeled tartt / news_aggregation for telemetry + the daily ceiling.
    assert ar.call_args.args[:2] == ("tartt", "news_aggregation")
    assert ar.call_args.kwargs["correlation_id"] == "https://ex.com/1"
    # Gemini Flash, bounded output.
    kw = fake_run.call_gemini.call_args.kwargs
    assert kw["model"] == "gemini-2.5-flash"
    assert kw["max_output_tokens"] == summarize.SUMMARY_MAX_TOKENS
