# Shared libraries

Phase 1 creates this directory but does not populate it.

Phase 2 (Telemetry primitives) adds:
- `runs.py` — the cost-emission helper (`agent_run` context manager,
  `RunContext` with `call_gemini`, `call_anthropic`, `call_embedding`).
- Price table and the three guards (G1 per-run cap, G2 daily ceiling,
  enforced here; G3 anomaly detection runs in Ted).

All future agents import from this module. No agent imports an LLM SDK directly.
This is enforced by code review at the git-gate.
