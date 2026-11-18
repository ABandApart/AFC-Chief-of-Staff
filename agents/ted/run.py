"""Ted's entry point (`loops/ted.md`, `loops/ted-judgment.md`).

    uv run python -m agents.ted.run                # the hourly cycle
    uv run python -m agents.ted.run --judgment     # the weekly headless review (D8)
    uv run python -m agents.ted.run --probe        # does the subscription answer headless?

**The cycle** (no LLM, O9): read barry-admin's acks, run the detectors, record
the findings, append the ones to raise to `INBOX-admin.md`, and send URGENT and
HIGH ones to the operator as urgent Briefing-page notes (D1(d): a Notion
@-mention). Then ping `cos-ted` (O8). If the operator alert can't be delivered,
it pings `/fail` instead, so healthchecks.io carries the alert off-box.

`--dry-run` prints what would be raised and writes nothing.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from agents.ted import detectors, judgment, state
from agents.ted.detectors import ALERTED, Finding

logger = logging.getLogger("ted")
HEARTBEAT_SLUG = "cos-ted"


def alert_text(f: Finding) -> str:
    return f"Ted {f.severity}: {f.text}"


def cycle(connection: Any, now: datetime, *, alert: Callable[[str], None] | None,
          dry_run: bool = False, inbox: Callable[..., None] = state.append_inbox,
          acks: Callable[[], dict[str, str]] = state.read_acks) -> dict[str, Any]:
    """One detection cycle. Returns counts and whether every alert was delivered."""
    with connection() as conn:
        found = detectors.detect(conn, now)
        if dry_run:
            return {"found": found, "raised": found, "alerted": 0, "alert_failed": False}
        acked = state.apply_acks(conn, acks(), now)
        raised = state.record(conn, found, now)
    inbox(raised, now, "Ted cycle")
    alerted, failed = 0, False
    for f in raised:
        if f.severity in ALERTED and alert is not None:
            try:
                alert(alert_text(f))
                alerted += 1
            except Exception:  # noqa: BLE001 - an alert failure must not lose the rest
                logger.exception("could not alert %s", f.key)
                failed = True
    return {"found": found, "raised": raised, "alerted": alerted, "acked": acked,
            "alert_failed": failed}


def run_judgment(connection: Any, now: datetime, *, dry_run: bool, trigger_kind: str,
                 session: Callable[[str], judgment.SessionResult] = judgment.run_session,
                 inbox: Callable[..., None] = state.append_inbox) -> dict[str, Any]:
    with connection() as conn:
        ev = judgment.evidence(conn, now)
    started = datetime.now().astimezone()
    res = session(judgment.prompt(ev))
    ended = datetime.now().astimezone()
    kept, dropped = judgment.findings(res.output) if res.ok else ([], [])
    out = {"result": res, "kept": kept, "dropped": dropped, "raised": []}
    if dry_run:
        return out
    with connection() as conn:
        judgment.record_run(conn, res, started=started, ended=ended, trigger_kind=trigger_kind,
                            correlation_id=f"ted-judgment-{now:%Y%m%dT%H%M}")
        out["raised"] = state.record(conn, kept, now) if res.ok else []
    inbox(out["raised"], now, "Ted weekly review (headless Claude Code)")
    return out


def _print_findings(label: str, items: list[Finding]) -> None:
    print(f"{label}: {len(items)}")
    for f in items:
        print(f"  {f.severity}|{f.key}|{f.text}")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Ted, the ops monitor.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--judgment", action="store_true", help="run the weekly headless review")
    mode.add_argument("--probe", action="store_true",
                      help="check that a headless session runs on the subscription; writes nothing")
    parser.add_argument("--dry-run", action="store_true", help="print; write nothing")
    parser.add_argument("--manual", action="store_true", help="record runs as manual")
    args = parser.parse_args(argv)
    now = datetime.now().astimezone()

    if args.probe:
        res = judgment.probe()
        print(f"apiKeySource: {res.api_key_source} · model: {res.model} · "
              f"tokens in/out: {res.input_tokens}/{res.output_tokens} · "
              f"notional cost: ${res.notional_usd:.4f}")
        print(f"result: {'OK ' + str(res.output) if res.ok else 'FAIL ' + str(res.error)}")
        return 0 if res.ok else 1

    from agents._lib import db, heartbeat

    try:
        if args.judgment:
            out = run_judgment(db.connection, now, dry_run=args.dry_run,
                               trigger_kind="manual" if args.manual else "scheduled")
            res = out["result"]
            print(f"session: {'ok' if res.ok else 'FAILED: ' + str(res.error)} · "
                  f"apiKeySource {res.api_key_source} · {res.input_tokens}/"
                  f"{res.output_tokens} tokens · notional ${res.notional_usd:.2f}")
            if res.ok:
                print(f"summary: {(res.output or {}).get('summary', '')}")
            _print_findings("findings", out["kept"])
            for why in out["dropped"]:
                print(f"  dropped {why}")
            return 0 if res.ok else 1

        alert = None
        if not args.dry_run:
            alert = _notion_alert(now.date())
        out = cycle(db.connection, now, alert=alert, dry_run=args.dry_run)
        _print_findings("found", out["found"])
        _print_findings("raised", out["raised"])
        if not args.dry_run:
            if out["alert_failed"] or (alert is None and any(
                    f.severity in ALERTED for f in out["raised"])):
                heartbeat.ping_fail(HEARTBEAT_SLUG)   # healthchecks carries it off-box
            else:
                heartbeat.ping(HEARTBEAT_SLUG)
        return 0
    except Exception:
        logger.exception("ted failed")
        if not args.dry_run and not args.judgment:
            heartbeat.ping_fail(HEARTBEAT_SLUG)
        raise
    finally:
        db.close_pool()


def _notion_alert(today: date) -> Callable[[str], None] | None:
    """The operator alert (D1(d)): an urgent Briefing-page note. None if Notion can't
    be reached, so the cycle still runs and pings `/fail`."""
    try:
        from agents._lib import db
        from agents._lib.notion_client import NotionClient
        from agents.notion import briefing_page

        client = NotionClient.from_keychain()
    except Exception:  # noqa: BLE001
        logger.exception("Notion unavailable; alerts go through cos-ted /fail")
        return None

    def alert(text: str) -> None:
        briefing_page.system_note(client, db.connection, today, text, urgent=True)
    return alert


if __name__ == "__main__":
    sys.exit(main())
