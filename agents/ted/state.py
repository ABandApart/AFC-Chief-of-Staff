"""Finding state, acks, and the barry-admin inbox (PRD-ted-ops-monitor §5.2, §5.4, D4, D5).

- `ted_findings` (migration 0040) holds one row per key: dedupe, re-raise, acks.
- `INBOX-admin.md` is written only by Ted (barry-agent). `ACK-admin.md` is written
  only by barry-admin. One writer per file, so neither account edits the other's
  file (D4).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from agents.ted.detectors import SHARED, Finding

INBOX = SHARED / "INBOX-admin.md"
ACKS = SHARED / "ACK-admin.md"

# A finding not seen for this long is over; seeing it again is a new incident,
# raised even if the old one was acknowledged. Two missed hourly cycles plus slack.
CLEAR_AFTER = timedelta(hours=3)
RERAISE_AFTER = timedelta(hours=24)   # O7

_ACK_LINE = re.compile(r"^\s*[-*]?\s*ack:\s*(\S+)\s*(.*)$", re.IGNORECASE)


def should_raise(row: dict[str, Any] | None, now: datetime) -> tuple[bool, bool]:
    """(raise it, it is a new incident) for a finding seen now (pure).

    `row` is the stored finding or None."""
    if row is None:
        return True, True
    if now - row["last_seen"] > CLEAR_AFTER:
        return True, True
    if row["acked_at"] is not None:
        return False, False
    last = row["last_raised"]
    return (last is None or now - last >= RERAISE_AFTER), False


def parse_acks(text: str) -> dict[str, str]:
    """`ack: <key> <note>` lines from ACK-admin.md → key: note (pure)."""
    out = {}
    for line in text.splitlines():
        m = _ACK_LINE.match(line)
        if m and m.group(1) not in ("<key>",):
            out[m.group(1)] = m.group(2).strip()
    return out


def apply_acks(conn: Any, acks: dict[str, str], now: datetime) -> int:
    """Mark acknowledged findings. Returns how many changed."""
    changed = 0
    for key, note in acks.items():
        cur = conn.execute("UPDATE ted_findings SET acked_at = %s, ack_note = %s "
                           "WHERE key = %s AND acked_at IS NULL", (now, note[:500], key))
        changed += cur.rowcount or 0
    return changed


def read_acks(path: Path = ACKS) -> dict[str, str]:
    try:
        return parse_acks(path.read_text())
    except OSError:
        return {}


def record(conn: Any, findings: Iterable[Finding], now: datetime) -> list[Finding]:
    """Upsert this cycle's findings; return the ones to raise now."""
    to_raise = []
    for f in findings:
        row = conn.execute(
            "SELECT last_seen, last_raised, acked_at FROM ted_findings WHERE key = %s",
            (f.key,)).fetchone()
        stored = (dict(zip(("last_seen", "last_raised", "acked_at"), row, strict=True))
                  if row else None)
        raise_it, new_incident = should_raise(stored, now)
        conn.execute(
            """
            INSERT INTO ted_findings (key, severity, source, text, first_seen, last_seen,
                                      last_raised, raise_count)
            VALUES (%(key)s, %(sev)s, %(src)s, %(text)s, %(now)s, %(now)s,
                    %(raised)s, %(count)s)
            ON CONFLICT (key) DO UPDATE SET
                severity = EXCLUDED.severity, text = EXCLUDED.text,
                last_seen = EXCLUDED.last_seen,
                first_seen = CASE WHEN %(new)s THEN EXCLUDED.first_seen
                                  ELSE ted_findings.first_seen END,
                acked_at = CASE WHEN %(new)s THEN NULL ELSE ted_findings.acked_at END,
                ack_note = CASE WHEN %(new)s THEN NULL ELSE ted_findings.ack_note END,
                last_raised = COALESCE(EXCLUDED.last_raised, ted_findings.last_raised),
                raise_count = ted_findings.raise_count + EXCLUDED.raise_count
            """,
            {"key": f.key, "sev": f.severity, "src": f.source, "text": f.text, "now": now,
             "raised": now if raise_it else None, "count": int(raise_it), "new": new_incident})
        if raise_it:
            to_raise.append(f)
    return to_raise


def inbox_entry(findings: list[Finding], now: datetime, title: str) -> str:
    """The Markdown block appended to INBOX-admin.md (pure)."""
    lines = [f"\n## {now:%Y-%m-%d %H:%M %Z} — {title}"]
    lines += [f"- {f.severity}|{f.key}|{f.text}" for f in findings]
    lines.append("- To acknowledge, add `ack: <key> <date> <accepted|rejected|specced in X>` "
                 "to ACK-admin.md.")
    return "\n".join(lines) + "\n"


def append_inbox(findings: list[Finding], now: datetime, title: str,
                 path: Path = INBOX) -> None:
    if not findings:
        return
    with path.open("a", encoding="utf-8") as fh:
        fh.write(inbox_entry(findings, now, title))
