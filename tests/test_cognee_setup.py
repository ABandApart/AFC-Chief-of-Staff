"""Unit tests for the cognee config builder (W2). Pure — no cognee, no DB."""

from __future__ import annotations

from agents._lib.cognee_setup import COGNEE_DB_NAME, build_cognee_env, cognee_dsn

DB_URL = "postgresql://barry_agent:secret@localhost:5432/aiadaptive_cos"


def test_cognee_dsn_swaps_dbname_only():
    assert cognee_dsn(DB_URL) == (
        "postgresql://barry_agent:secret@localhost:5432/aiadaptive_cognee"
    )


def test_build_env_models_and_flags():
    env = build_cognee_env(DB_URL, anthropic_key="sk-ant")
    # M1 routing: custom provider + anthropic/ model prefix (→ litellm)
    assert env["LLM_PROVIDER"] == "custom"
    assert env["LLM_MODEL"].startswith("anthropic/")
    assert env["LLM_API_KEY"] == "sk-ant"
    # embeddings: local FastEmbed @ 768, no API key (Gemini reserved for news)
    assert env["EMBEDDING_PROVIDER"] == "fastembed"
    assert env["EMBEDDING_MODEL"] == "BAAI/bge-base-en-v1.5"
    assert env["EMBEDDING_DIMENSIONS"] == "768"
    assert "EMBEDDING_API_KEY" not in env
    # single-user
    assert env["ENABLE_BACKEND_ACCESS_CONTROL"] == "false"


def test_build_env_all_three_stores_point_at_cognee_db():
    env = build_cognee_env(DB_URL, anthropic_key="k")
    assert env["DB_PROVIDER"] == "postgres"
    assert env["VECTOR_DB_PROVIDER"] == "pgvector"
    assert env["GRAPH_DATABASE_PROVIDER"] == "postgres"
    for prefix in ("DB", "VECTOR_DB", "GRAPH_DATABASE"):
        assert env[f"{prefix}_NAME"] == COGNEE_DB_NAME  # isolated from aiadaptive_cos
        assert env[f"{prefix}_HOST"] == "localhost"
        assert env[f"{prefix}_PORT"] == "5432"
        assert env[f"{prefix}_USERNAME"] == "barry_agent"
        assert env[f"{prefix}_PASSWORD"] == "secret"


def test_build_env_defaults_for_sparse_url():
    env = build_cognee_env("postgresql:///aiadaptive_cos", anthropic_key="k")
    assert env["DB_HOST"] == "localhost"
    assert env["DB_PORT"] == "5432"
    assert env["DB_NAME"] == COGNEE_DB_NAME
