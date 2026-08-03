"""Cognee configuration for the AFC Richmond brain (Phase 3.7 / W2).

Configures cognee to run its graph + vector + relational stores on the local
Postgres — a **dedicated `aiadaptive_cognee` database**, isolated from the
operational tables (`agent_runs`, `prospects`, …) which stay in `aiadaptive_cos`.

Provider plan (2026-08-03): **Gemini is reserved for news ingestion only** (Tartt,
Phase 4). The knowledge pipeline runs entirely off Gemini —
  - **LLM = Anthropic** (`claude-haiku-4-5`) for extraction/graph-building, routed
    through litellm (**M1**, mandatory): `LLM_PROVIDER=custom` + an `anthropic/…`
    model prefix → GenericAPIAdapter → `litellm.acompletion`, so our labeling
    callback fires. Cognee's *native* AnthropicAdapter calls the raw SDK and
    bypasses the callback — that routing would silently lose ~all LLM spend.
  - **Embeddings = local FastEmbed** (`BAAI/bge-base-en-v1.5` @ 768), in-process
    via ONNX Runtime — **no API key, no rate limits, no data leaves the box**.
    Replaced `gemini-embedding-001` (2026-08-03): the first Granola poll hit the
    Gemini free-tier embed cap (429), and embeddings are the only thing that had
    kept Gemini in this pipeline. bge-base-en-v1.5 is 768-dim, so the dimension
    commitment is unchanged. Local embeddings don't go through litellm, so they
    make **no ledger row** (they're free) — the ledger shows only Anthropic
    extraction spend per cognify. **Fallback:** if the local path proves flaky,
    switch to **Voyage** (Anthropic's recommended embeddings partner) — see the
    commented block in `build_cognee_env`.
  - **access control OFF** (single-user) so the pgvector + postgres-graph
    adapters take one set of per-store creds instead of tenant scoping.

cognee is an optional dependency (`uv sync --group cognee`, which now includes the
`fastembed` extra); this module imports without it (`configure_cognee` only
touches cognee transitively via the litellm callback). Call `configure_cognee()`
once at process start, before any cognee call. The first run downloads + caches
the bge model from HuggingFace (~a few hundred MB) — needs network once.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from agents._lib import creds
from agents._lib.telemetry_context import install_litellm_callback

COGNEE_DB_NAME = "aiadaptive_cognee"
LLM_MODEL = "anthropic/claude-haiku-4-5"   # litellm prefix → GenericAPIAdapter (M1)
EMBEDDING_PROVIDER = "fastembed"           # local ONNX — no key, no rate limits
EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"  # 768-dim; keeps the dimension commitment
EMBEDDING_DIMENSIONS = "768"


def cognee_dsn(db_url: str) -> str:
    """The operational db-url with the dbname swapped to the cognee database."""
    return urlparse(db_url)._replace(path=f"/{COGNEE_DB_NAME}").geturl()


def build_cognee_env(db_url: str, *, anthropic_key: str) -> dict[str, str]:
    """Build the cognee env-var config (pure — unit-tested).

    All three stores (relational `DB_*`, vector `VECTOR_DB_*`, graph
    `GRAPH_DATABASE_*`) point at the same dedicated Postgres database; with
    access control off they need their own copies of the creds (they don't
    inherit `DB_*`). Embeddings are local (FastEmbed) → no embedding API key.
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
        "EMBEDDING_PROVIDER": EMBEDDING_PROVIDER,   # local ONNX — no EMBEDDING_API_KEY
        "EMBEDDING_MODEL": EMBEDDING_MODEL,
        "EMBEDDING_DIMENSIONS": EMBEDDING_DIMENSIONS,
        "VECTOR_DB_PROVIDER": "pgvector",
        "GRAPH_DATABASE_PROVIDER": "postgres",
        "DB_PROVIDER": "postgres",
        "ENABLE_BACKEND_ACCESS_CONTROL": "false",
    }
    # Fallback embedder (Voyage — Anthropic's recommended partner) if the local
    # FastEmbed path proves flaky at runtime. To switch: `uv sync` with a
    # `cognee[postgres]` that includes voyage support, provision `voyage-api-key`,
    # and replace the three EMBEDDING_* lines above with:
    #     "EMBEDDING_PROVIDER": "litellm",
    #     "EMBEDDING_MODEL": "voyage/voyage-3.5",   # 1024-dim → re-embed the graph
    #     "EMBEDDING_DIMENSIONS": "1024",
    #     "EMBEDDING_API_KEY": voyage_key,
    for prefix in ("DB", "VECTOR_DB", "GRAPH_DATABASE"):
        for key, val in store.items():
            env[f"{prefix}_{key}"] = val
    return env


def _clear_cognee_config_caches() -> None:
    """Clear cognee's `@lru_cache`d `get_*_config()` getters.

    cognee caches each config the first time it's read. `configure_cognee` sets
    the env with `setdefault`, which **cannot** override a config that was already
    read (and cached with cognee's defaults, e.g. `provider=openai`, no key)
    before configure ran — e.g. an entrypoint that imports `cognee.low_level` (via
    `ontology`) at module load. Symptom: `LLMAPIKeyNotSetError (422)` on the first
    LLM call even though the env is correct (diagnosed at the W3 meeting-hybrid
    probe, 2026-08-03). Clearing the caches after we set the env makes config
    order-independent. Best-effort per getter so a cognee version that renames or
    moves one doesn't break configure.
    """
    import contextlib
    import importlib

    getters = [
        ("cognee.infrastructure.llm.config", "get_llm_config"),
        ("cognee.infrastructure.databases.relational", "get_relational_config"),
        ("cognee.infrastructure.databases.vector", "get_vectordb_config"),
        ("cognee.infrastructure.databases.graph.config", "get_graph_config"),
    ]
    for module_path, name in getters:
        with contextlib.suppress(Exception):
            getattr(importlib.import_module(module_path), name).cache_clear()


def configure_cognee() -> None:
    """Apply cognee config to the environment and install the M1 callback.

    Reads creds from keychain (`db-url`, `anthropic-api-key`). No Gemini key —
    embeddings are local (FastEmbed). Uses `setdefault`, so anything already
    exported wins (lets an operator override a single value without editing code),
    then clears cognee's cached config getters so our env wins regardless of
    whether a config was read before this ran (see `_clear_cognee_config_caches`).
    """
    env = build_cognee_env(
        creds.keychain_get("db-url"),
        anthropic_key=creds.keychain_get("anthropic-api-key"),
    )
    for key, val in env.items():
        os.environ.setdefault(key, val)
    _clear_cognee_config_caches()
    install_litellm_callback()
