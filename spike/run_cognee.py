"""Cognee viability spike harness — architecture/SPIKE-cognee-eval-2026-07.md.

THROWAWAY. Runs against the scratch DB `cognee_spike` only; writes label-coverage
rows to a scratch `spike_runs` table in that DB (never the production `agent_runs`
ledger). One pass cognifies the 5 sample docs and collects all five gating
signals, each probe independently guarded so a single failure doesn't abort the
run — collecting every signal in one pass is the whole point.

Run as barry-agent (keys + db-url live there). See spike/README.md.

Config is via environment variables (cognee's most stable surface, pydantic-
settings). The harness fills them from `db-url` + keychain, but any can be
overridden by exporting it first. The GRAPH provider value is the crux of Q1 —
if cognify errors on it, that error IS the Q1 finding; try the alternates noted
inline.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import platform
import resource
import subprocess
import sys
import time
from contextlib import contextmanager
from urllib.parse import urlparse

# --------------------------------------------------------------------------
# Credentials + connection (from barry-agent's keychain, like the real code)
# --------------------------------------------------------------------------


def keychain_get(item: str) -> str:
    r = subprocess.run(
        ["security", "find-generic-password", "-w", "-s", item],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(f"keychain item '{item}' not found")
    return r.stdout.strip()


def spike_dsn() -> str:
    """Production db-url with the dbname swapped to cognee_spike."""
    base = keychain_get("db-url")
    p = urlparse(base)
    return p._replace(path="/cognee_spike").geturl()


# Small price map (USD/token) — decoupled from agents/_lib/runs.py on purpose so
# the spike is self-contained. Keyed by substring of the litellm model string.
PRICE = {
    "haiku":            {"in": 1.0 / 1e6, "out": 5.0 / 1e6},
    "sonnet":           {"in": 3.0 / 1e6, "out": 15.0 / 1e6},
    "gemini-embedding": {"in": 0.15 / 1e6, "out": 0.0},
    "gemini-2.5-flash": {"in": 0.075 / 1e6, "out": 0.30 / 1e6},
}


def price_for(model: str) -> dict[str, float]:
    for key, p in PRICE.items():
        if key in (model or ""):
            return p
    return {"in": 0.0, "out": 0.0}  # unknown → 0, flagged in output


# --------------------------------------------------------------------------
# Q3 — telemetry label propagation: contextvar + litellm callback
# --------------------------------------------------------------------------

current_label: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "current_label", default=None
)

# Collected in-memory too, so the run summarizes even if DB writes fail.
CALL_LOG: list[dict] = []


@contextmanager
def labeled(agent_name: str, function_label: str, *, correlation_id: str | None = None):
    token = current_label.set({
        "agent_name": agent_name,
        "function_label": function_label,
        "correlation_id": correlation_id,
    })
    try:
        yield
    finally:
        current_label.reset(token)


def _ensure_spike_runs(dsn: str) -> None:
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS spike_runs (
                id            BIGSERIAL PRIMARY KEY,
                ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
                agent_name    TEXT,
                function_label TEXT,
                correlation_id TEXT,
                model         TEXT,
                input_tokens  INTEGER,
                output_tokens INTEGER,
                usd_cost      NUMERIC(14,8),
                labeled       BOOLEAN NOT NULL
            )
            """
        )


def _record_call(dsn: str, model: str, in_tok: int, out_tok: int) -> None:
    """Called from the litellm success hook. Reads the contextvar (Q3) and
    writes one scratch row. Cost from the local price map (Q2)."""
    label = current_label.get()
    p = price_for(model)
    usd = in_tok * p["in"] + out_tok * p["out"]
    row = {
        "agent_name": (label or {}).get("agent_name"),
        "function_label": (label or {}).get("function_label"),
        "correlation_id": (label or {}).get("correlation_id"),
        "model": model,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "usd_cost": usd,
        "labeled": label is not None,
    }
    CALL_LOG.append(row)
    try:
        import psycopg
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO spike_runs (agent_name, function_label, correlation_id, "
                "model, input_tokens, output_tokens, usd_cost, labeled) "
                "VALUES (%(agent_name)s, %(function_label)s, %(correlation_id)s, "
                "%(model)s, %(input_tokens)s, %(output_tokens)s, %(usd_cost)s, %(labeled)s)",
                row,
            )
    except Exception as e:  # never let telemetry break the run
        print(f"  [warn] spike_runs write failed: {e}", file=sys.stderr)


def install_litellm_callback(dsn: str) -> None:
    """Register a litellm success hook that captures usage + the label.

    cognee calls the same `litellm` module we register on, so this should catch
    its internal calls. If Q3 comes back all-NULL, cognee is using a client that
    bypasses the shared module — record that as the red finding.
    """
    from litellm.integrations.custom_logger import CustomLogger

    def _usage(kwargs, response_obj):
        # litellm surfaces usage a few ways across versions; try them in order.
        model = kwargs.get("model") or ""
        usage = None
        if response_obj is not None and hasattr(response_obj, "usage"):
            usage = response_obj.usage
        if usage is None:
            usage = kwargs.get("usage")
        in_tok = int(getattr(usage, "prompt_tokens", None) or
                     (usage or {}).get("prompt_tokens", 0) or 0)
        out_tok = int(getattr(usage, "completion_tokens", None) or
                      (usage or {}).get("completion_tokens", 0) or 0)
        return model, in_tok, out_tok

    class LabelCapture(CustomLogger):
        def log_success_event(self, kwargs, response_obj, start_time, end_time):
            model, i, o = _usage(kwargs, response_obj)
            _record_call(dsn, model, i, o)

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            model, i, o = _usage(kwargs, response_obj)
            _record_call(dsn, model, i, o)

    import litellm
    litellm.callbacks = [LabelCapture()]


# --------------------------------------------------------------------------
# Cognee configuration (env-var surface). EDIT HERE if 1.4.0 differs.
# --------------------------------------------------------------------------


def configure_cognee(dsn: str) -> dict:
    p = urlparse(dsn)
    cfg = {
        # LLM for extraction/summaries. Anthropic Haiku via litellm.
        "LLM_PROVIDER": os.environ.get("LLM_PROVIDER", "anthropic"),
        "LLM_MODEL": os.environ.get("LLM_MODEL", "claude-haiku-4-5"),
        "LLM_API_KEY": keychain_get("anthropic-key-ted"),
        # Embeddings — Q4: keep gemini-embedding-001 @ 768.
        "EMBEDDING_PROVIDER": os.environ.get("EMBEDDING_PROVIDER", "gemini"),
        "EMBEDDING_MODEL": os.environ.get("EMBEDDING_MODEL", "gemini/gemini-embedding-001"),
        "EMBEDDING_DIMENSIONS": os.environ.get("EMBEDDING_DIMENSIONS", "768"),
        "EMBEDDING_API_KEY": keychain_get("gemini-api-key"),
        # Vector store — pgvector on the shared instance.
        "VECTOR_DB_PROVIDER": os.environ.get("VECTOR_DB_PROVIDER", "pgvector"),
        # GRAPH store — THE Q1 CRUX. If cognify errors on this value, that's the
        # finding. 1.4.0 alternates to try (in order): "postgres", "pgsql",
        # "networkx" (in-Postgres via relational? or file), "kuzu" (falls off
        # the single-Postgres goal → Q1 yellow).
        "GRAPH_DATABASE_PROVIDER": os.environ.get("GRAPH_DATABASE_PROVIDER", "postgres"),
        # Relational + pgvector share this connection.
        "DB_PROVIDER": "postgres",
        "DB_HOST": p.hostname or "localhost",
        "DB_PORT": str(p.port or 5432),
        "DB_NAME": (p.path or "/cognee_spike").lstrip("/"),
        "DB_USERNAME": p.username or "",
        "DB_PASSWORD": p.password or "",
    }
    for k, v in cfg.items():
        os.environ.setdefault(k, v)
    redacted = {k: ("***" if "KEY" in k or "PASSWORD" in k else v) for k, v in cfg.items()}
    print("cognee config:\n" + json.dumps(redacted, indent=2))
    return cfg


# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "sample_docs")


async def cognify_docs(dsn: str) -> dict:
    """Q2 latency + $: cognify each doc under its own label; time each."""
    import cognee

    results = {"per_doc": [], "errors": []}
    files = sorted(f for f in os.listdir(SAMPLE_DIR) if f.endswith(".txt"))
    for fname in files:
        path = os.path.join(SAMPLE_DIR, fname)
        text = open(path, encoding="utf-8").read()
        corr = fname.split("_")[0]
        t0 = time.perf_counter()
        try:
            with labeled("spike", "customer_discovery", correlation_id=corr):
                await cognee.add(text, dataset_name="spike")
                await cognee.cognify(datasets=["spike"])
            dt = time.perf_counter() - t0
            results["per_doc"].append({"doc": fname, "chars": len(text), "seconds": round(dt, 1)})
            print(f"  cognified {fname} ({len(text)} chars) in {dt:.1f}s")
        except Exception as e:
            results["errors"].append({"doc": fname, "error": f"{type(e).__name__}: {e}"})
            print(f"  [ERROR] cognify {fname}: {type(e).__name__}: {e}", file=sys.stderr)
    return results


async def graph_query() -> dict:
    """Q1: does a graph-traversal query return a real answer?"""
    out = {"ok": False, "answer": None, "error": None}
    try:
        import cognee
        from cognee import SearchType
        res = await cognee.search(
            query_type=SearchType.GRAPH_COMPLETION,
            query_text="What single workflow do these firms want removed, and who asked?",
        )
        out["ok"] = True
        out["answer"] = str(res)[:600]
        print(f"  GRAPH_COMPLETION returned: {str(res)[:200]}")
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        print(f"  [ERROR] graph query: {type(e).__name__}: {e}", file=sys.stderr)
    return out


def inspect_stores(dsn: str) -> dict:
    """Q1 (graph tables present?) + Q4 (embedding dim/norm)."""
    import math

    import psycopg
    out = {"tables": [], "graph_tables": [], "embedding": None, "error": None}
    try:
        with psycopg.connect(dsn) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' ORDER BY table_name"
            ).fetchall()]
            out["tables"] = tables
            out["graph_tables"] = [
                t for t in tables
                if any(k in t.lower() for k in ("edge", "graph", "relationship", "node"))
            ]
            # Q4: find a pgvector column, sample one embedding, check dim + norm.
            vec_cols = conn.execute(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE udt_name='vector' AND table_schema='public' LIMIT 1"
            ).fetchone()
            if vec_cols:
                tbl, col = vec_cols
                row = conn.execute(
                    f"SELECT {col}::text FROM {tbl} WHERE {col} IS NOT NULL LIMIT 1"
                ).fetchone()
                if row and row[0]:
                    vals = [float(x) for x in row[0].strip("[]").split(",")]
                    norm = math.sqrt(sum(v * v for v in vals))
                    out["embedding"] = {
                        "table": tbl, "column": col,
                        "dim": len(vals), "l2_norm": round(norm, 4),
                        "unit_norm": abs(norm - 1.0) < 0.01,
                    }
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def rss_mb() -> float:
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes; Linux reports KiB.
    return ru / (1024 * 1024) if platform.system() == "Darwin" else ru / 1024


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


async def main() -> int:
    dsn = spike_dsn()
    print(f"spike DSN → {urlparse(dsn)._replace(netloc='***').geturl()}\n")
    configure_cognee(dsn)
    _ensure_spike_runs(dsn)
    install_litellm_callback(dsn)

    print("\n=== cognify (Q2 latency/$) ===")
    cog = await cognify_docs(dsn)
    print("\n=== graph query (Q1) ===")
    gq = await graph_query()
    print("\n=== store inspection (Q1 tables / Q4 embedding) ===")
    stores = inspect_stores(dsn)

    # ---- Q3 label coverage ----
    total = len(CALL_LOG)
    labeled_n = sum(1 for c in CALL_LOG if c["labeled"])
    corr_n = sum(1 for c in CALL_LOG if c["correlation_id"])
    # ---- Q2 cost ----
    total_usd = sum(c["usd_cost"] for c in CALL_LOG)
    short_docs = [d for d in cog["per_doc"] if d["chars"] < 300]
    # crude per-doc cost: total / docs cognified (refine against dashboard)
    n_ok = len(cog["per_doc"]) or 1
    per_doc_usd = total_usd / n_ok
    peak = rss_mb()

    print("\n" + "=" * 60)
    print("SPIKE FINDINGS (copy into SPIKE-cognee-eval-2026-07.md)")
    print("=" * 60)
    print(f"Q1 graph tables in cognee_spike : {stores['graph_tables'] or 'NONE'}")
    print(f"Q1 GRAPH_COMPLETION answered    : {gq['ok']}"
          + (f"  (err: {gq['error']})" if gq["error"] else ""))
    print(f"Q2 provider calls captured      : {total}")
    print(f"Q2 total spend (local price map): ${total_usd:.6f}")
    print(f"Q2 ~cost per doc (all sizes)    : ${per_doc_usd:.6f}"
          f"   [short docs: {len(short_docs)} — cross-check dashboard]")
    print("Q2 latency per doc (s)          : "
          + ", ".join(f"{d['doc'].split('_')[0]}={d['seconds']}" for d in cog["per_doc"]))
    print(f"Q3 label coverage               : {labeled_n}/{total} labeled, "
          f"{corr_n}/{total} carry correlation_id")
    print(f"Q4 embedding                    : {stores['embedding'] or 'NOT FOUND — see error'}")
    print(f"Q5 peak RSS (MB)                : {peak:.0f}   "
          f"(also run: /usr/bin/time -l uv run python -m spike.run_cognee)")
    if cog["errors"]:
        print(f"\ncognify errors: {json.dumps(cog['errors'], indent=2)}")
    if stores["error"]:
        print(f"store-inspect error: {stores['error']}")
    print("\nReminder: this wrote ONLY to cognee_spike. Teardown: dropdb cognee_spike.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
