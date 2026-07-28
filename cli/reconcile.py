"""Reconcile the agent_runs ledger against provider bills — the W1.3 backstop.

Dropping the hard pre-flight gate (W1.2) means a code path that bypasses the
ledger would spend money and log nothing. This periodic check catches that: it
computes authoritative ledger spend by provider for a window, and compares it to
the provider-reported figures.

The ledger side is automated and authoritative. The provider side is
**operator-supplied** — read the totals off the Anthropic + Google dashboards
and pass them in. Automating that pull needs org-admin billing credentials this
setup doesn't assume; the same manual compare closed the H1 spike check.

Usage:
    uv run python -m cli.reconcile                       # this month, ledger only
    uv run python -m cli.reconcile --since 30d
    uv run python -m cli.reconcile --anthropic 4.20 --gemini 0.05
    uv run python -m cli.reconcile --anthropic 4.20 --tolerance 0.10

Exit code: 1 if any supplied provider actual diverges beyond tolerance (so it
can back a monthly routine/alert); 0 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from agents._lib import db

ANTHROPIC_DASHBOARD = "https://console.anthropic.com/settings/usage"
GEMINI_DASHBOARD = "https://aistudio.google.com/usage"

# Below this absolute dollar delta a divergence is treated as noise regardless of
# percentage (keeps tiny figures — e.g. the ~$0.007 embedding side — from
# tripping the tolerance on rounding).
ABS_FLOOR = 0.01


@dataclass
class ReconcileLine:
    provider: str
    ledger_usd: float
    actual_usd: float | None
    runs: int

    @property
    def delta(self) -> float | None:
        return None if self.actual_usd is None else self.actual_usd - self.ledger_usd

    def within(self, tolerance: float) -> bool | None:
        """True/False if an actual was supplied and is within/over tolerance;
        None if no actual to compare."""
        if self.actual_usd is None or self.delta is None:
            return None
        return abs(self.delta) <= max(tolerance * self.actual_usd, ABS_FLOOR)


def reconcile(
    ledger: dict[str, dict],
    actuals: dict[str, float | None],
    tolerance: float,
) -> list[ReconcileLine]:
    """Build one line per provider seen in the ledger OR named in actuals."""
    providers = sorted(set(ledger) | {p for p, v in actuals.items() if v is not None})
    lines = []
    for p in providers:
        led = ledger.get(p, {})
        lines.append(ReconcileLine(
            provider=p,
            ledger_usd=float(led.get("usd", 0.0)),
            actual_usd=actuals.get(p),
            runs=int(led.get("runs", 0)),
        ))
    return lines


def overall_ok(lines: list[ReconcileLine], tolerance: float) -> bool:
    """True unless some line has a supplied actual that is out of tolerance."""
    return all(lp.within(tolerance) is not False for lp in lines)


def format_report(window_desc: str, lines: list[ReconcileLine], tolerance: float) -> str:
    out = [f"=== ledger vs provider reconcile — {window_desc} ==="]
    out.append(
        f"  {'provider':<12} {'runs':>6} {'ledger $':>12} "
        f"{'actual $':>12} {'delta $':>12}  status"
    )
    out.append("  " + "-" * 70)
    any_actual = False
    led_total = 0.0
    for lp in lines:
        led_total += lp.ledger_usd
        if lp.actual_usd is None:
            actual_s, delta_s, status = "—", "—", "(ledger only)"
        else:
            any_actual = True
            actual_s = f"{lp.actual_usd:.6f}"
            delta_s = f"{lp.delta:+.6f}"
            status = "ok" if lp.within(tolerance) else "⚠️ OVER TOLERANCE"
        out.append(
            f"  {lp.provider:<12} {lp.runs:>6} {lp.ledger_usd:>12.6f} "
            f"{actual_s:>12} {delta_s:>12}  {status}"
        )
    out.append("  " + "-" * 70)
    out.append(f"  {'TOTAL':<12} {'':>6} {led_total:>12.6f}")
    if not any_actual:
        out.append("")
        out.append("  No provider actuals supplied — this is the ledger side only.")
        out.append("  Read the dashboards for this window, re-run with --anthropic/--gemini:")
        out.append(f"    Anthropic: {ANTHROPIC_DASHBOARD}")
        out.append(f"    Gemini:    {GEMINI_DASHBOARD}")
    else:
        out.append(f"  tolerance: {tolerance:.0%} (or ${ABS_FLOOR:.2f} absolute)")
    return "\n".join(out)


def parse_since(s: str) -> timedelta:
    m = re.fullmatch(r"(\d+)([hd])", s)
    if not m:
        raise argparse.ArgumentTypeError(f"Invalid --since '{s}'. Use e.g. 24h, 30d.")
    n, unit = int(m.group(1)), m.group(2)
    return timedelta(hours=n) if unit == "h" else timedelta(days=n)


def _month_start_local() -> datetime:
    now_local = datetime.now().astimezone()
    return now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def query_ledger(since: datetime) -> dict[str, dict]:
    with db.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COALESCE(llm_provider, '(none)') AS provider,
                       COUNT(*) AS runs,
                       COALESCE(SUM(usd_cost), 0)::float AS usd
                FROM agent_runs
                WHERE started_at >= %s
                GROUP BY llm_provider
                """,
                (since,),
            )
            return {r[0]: {"runs": r[1], "usd": r[2]} for r in cur.fetchall()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile the agent_runs ledger against provider bills.",
    )
    parser.add_argument("--since", type=parse_since, default=None,
                        help="lookback window (e.g. 30d). Default: start of this month.")
    parser.add_argument("--anthropic", type=float, default=None,
                        help="Anthropic dashboard total for the window (USD)")
    parser.add_argument("--gemini", type=float, default=None,
                        help="Google/Gemini dashboard total for the window (USD)")
    parser.add_argument("--tolerance", type=float, default=0.15,
                        help="fractional divergence allowed before flagging (default 0.15)")
    args = parser.parse_args()

    if args.since is not None:
        since = datetime.now(UTC) - args.since
        window_desc = f"last {args.since}"
    else:
        since = _month_start_local()
        window_desc = f"since {since:%Y-%m-%d} (month to date)"

    ledger = query_ledger(since)
    actuals: dict[str, float | None] = {"anthropic": args.anthropic, "gemini": args.gemini}
    lines = reconcile(ledger, actuals, args.tolerance)

    print(format_report(window_desc, lines, args.tolerance))
    return 0 if overall_ok(lines, args.tolerance) else 1


if __name__ == "__main__":
    sys.exit(main())
