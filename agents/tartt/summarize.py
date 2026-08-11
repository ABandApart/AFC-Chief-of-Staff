"""Tartt — article summarization via Gemini Flash (Phase 4, Task 2).

Gemini `gemini-2.5-flash` is Tartt's **one** paid touchpoint — chosen because it
weights search + recency, which is what news discovery wants (embeddings stay
local; extraction/cognify is Anthropic). It runs under the `tartt` telemetry
label + daily ceiling via `agent_run`, so the spend lands in `agent_runs` and the
soft breaker applies. Kept on the free tier during the quality trial — volume is
bounded upstream (small source set, slow cadence, interest-gating downstream).
"""

from __future__ import annotations

from agents._lib.runs import agent_run

SUMMARY_MODEL = "gemini-2.5-flash"
SUMMARY_MAX_TOKENS = 400
# Bound the prompt so a long article can't blow latency or the free-tier budget;
# the lede + first section carry the gist for a reading-digest summary.
MAX_INPUT_CHARS = 12000

_PROMPT = """You are summarizing a piece of content for a busy solo consultant's \
daily reading digest. In 3-5 sentences, capture what it is about, the key insight \
or claim, and why it might matter. Be concrete and neutral — no hype, no preamble.

Title: {title}

Content:
{body}
"""


def build_prompt(title: str, text: str, *, max_input_chars: int = MAX_INPUT_CHARS) -> str:
    """Assemble the summarization prompt (pure). Truncates the body to bound cost."""
    body = (text or "").strip()[:max_input_chars]
    return _PROMPT.format(title=(title or "").strip() or "(untitled)", body=body)


def summarize(title: str, text: str, *, source_url: str) -> str:
    """Summarize one article via Gemini Flash. Returns the summary text.

    Runs inside `agent_run('tartt', 'news_aggregation')`: the soft daily breaker
    is checked on entry (raises `DailyCeilingExceeded` if over), and exactly one
    `agent_runs` row records the spend on exit.
    """
    with agent_run(
        "tartt",
        "news_aggregation",
        trigger_kind="scheduled",
        correlation_id=source_url,
        correlation_kind="content",
    ) as run:
        return run.call_gemini(
            prompt=build_prompt(title, text),
            model=SUMMARY_MODEL,
            max_output_tokens=SUMMARY_MAX_TOKENS,
        )
