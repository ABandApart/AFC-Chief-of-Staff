"""Cognee configuration for the AFC Richmond brain (Phase 3.7 / W2).

Configures cognee to run its graph + vector + relational stores on the local
Postgres — a **dedicated `aiadaptive_cognee` database**, isolated from the
operational tables (`agent_runs`, `prospects`, …) which stay in `aiadaptive_cos`.

Bakes in what the spike learned:
  - **M1 telemetry routing (mandatory):** the LLM goes through litellm
    (`LLM_PROVIDER=custom` + an `anthropic/…` model prefix → GenericAPIAdapter →
    `litellm.acompletion`), so our labeling callback fires. Cognee's *native*
    AnthropicAdapter calls the raw SDK and bypasses the callback — that routing
    would silently lose ~all LLM spend from the ledger.
  - **our embedder kept:** `gemini-embedding-001` @ 768 (M2 — cognee does not
    L2-normalize the truncated output; handled on the recall/write path in W5).
  - **access control OFF** (single-user) so the pgvector + postgres-graph
    adapters take one set of per-store creds instead of tenant scoping.

cognee is an optional dependency (`uv sync --group cognee`); this module imports
without it (`configure_cognee` only touches cognee transitively via the litellm
callback). Call `configure_cognee()` once at process start, before any cognee
call.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from agents._lib import creds
from agents._lib.telemetry_context import install_litellm_callback

COGNEE_DB_NAME = "aiadaptive_cognee"
LLM_MODEL = "anthropic/claude-haiku-4-5"   # litellm prefix → GenericAPIAdapter (M1)
EMBEDDING_MODEL = "gemini/gemini-embedding-001"
EMBEDDING_DIMENSIONS = "768"


def cognee_dsn(db_url: str) -> str:
    """The operational db-url with the dbname swapped to the cognee database."""
    return urlparse(db_url)._replace(path=f"/{COGNEE_DB_NAME}").geturl()


def build_cognee_env(db_url: str, *, anthropic_key: str, gemini_key: str) -> dict[str, str]:
    """Build the cognee env-var config (pure — unit-tested).

    All three stores (relational `DB_*`, vector `VECTOR_DB_*`, graph
    `GRAPH_DATABASE_*`) point at the same dedicated Postgres database; with
    access control off they need their own copies of the creds (they don't
    inherit `DB_*`).
    """
    p = urlparse(db_url)
    store = {
        "HOST": p.hostname or "localhost",
        "PORT": str(p.port or 5432),
        "NAME": COGNEE_DB_NAME,
        "USERNAME": p.username or "",
        "PASSWORD": p.password or "",
    }
    env: dict[str, str] = {
        "LLM_PROVIDER": "custom",
        "LLM_MODEL": LLM_MODEL,
        "LLM_API_KEY": anthropic_key,
        "EMBEDDING_PROVIDER": "gemini",
        "EMBEDDING_MODEL": EMBEDDING_MODEL,
        "EMBEDDING_DIMENSIONS": EMBEDDING_DIMENSIONS,
        "EMBEDDING_API_KEY": gemini_key,
        "VECTOR_DB_PROVIDER": "pgvector",
        "GRAPH_DATABASE_PROVIDER": "postgres",
        "DB_PROVIDER": "postgres",
        "ENABLE_BACKEND_ACCESS_CONTROL": "false",
    }
    for prefix in ("DB", "VECTOR_DB", "GRAPH_DATABASE"):
        for key, val in store.items():
            env[f"{prefix}_{key}"] = val
    return env


def configure_cognee() -> None:
    """Apply cognee config to the environment and install the M1 callback.

    Reads creds from keychain (`db-url`, `anthropic-api-key`, `gemini-api-key`).
    Uses `setdefault`, so anything already exported wins (lets an operator
    override a single value without editing code).
    """
    env = build_cognee_env(
        creds.keychain_get("db-url"),
        anthropic_key=creds.keychain_get("anthropic-api-key"),
        gemini_key=creds.keychain_get("gemini-api-key"),
    )
    for key, val in env.items():
        os.environ.setdefault(key, val)
    install_litellm_callback()
