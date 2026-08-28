"""G3 send-guard: this system holds a send-capable Gmail token but NEVER sends.

`PRD-outreach-gmail-channel.md` G3: there is no draft-only Gmail scope —
`gmail.compose` grants send — so the OAuth token the drafting loop will use is
send-capable. The mitigation is not "remember not to call send"; it is to make
"this system never sends mail" a **property of the repo**, checked in CI, exactly
like the B1 `test_no_raw_retrieval` rule. This guard fails the build if a mail-send
call appears anywhere under `agents/` or `cli/`.

Built BEFORE any send-capable code (the guardrail before the gun): when
`agents/outreach/gmail.py` lands it will use `drafts().create/update`,
`messages().get`, and `history.list` — none of which match here — and the moment a
`drafts().send` / `messages().send` (or an SMTP `sendmail`) is introduced by a bug
or a careless change, this test goes red.

Forbidden (a send crosses B2 — `35-` §13 — and must be a human action, never
system-initiated):
  * Gmail API:  `messages().send(...)`, `drafts().send(...)`
  * SMTP:       `smtplib`, `.sendmail(...)`

Allowed and expressly NOT matched: `drafts().create`, `drafts().update`,
`messages().get`, `history.list` — the draft-and-observe surface the channel is.

Pure Python (no `rg` dependency) so it runs anywhere the suite does. The V1–V3
Gmail probes are throwaway scripts run once against the live account (§7) and are
never committed under `agents/`/`cli/`, so they do not reach this guard; and they
need no programmatic send anyway — the operator sends the probe draft by hand.
"""

from __future__ import annotations

import re
from pathlib import Path

# Gmail resource-method send, and SMTP send. `\b` on send keeps `sendmail` from
# being caught twice and avoids matching identifiers like `send_reminder`.
_PATTERN = re.compile(r"(?:messages|drafts)\(\)\.send\b|\.sendmail\(|\bsmtplib\b")

_ROOT = Path(__file__).resolve().parent.parent
_SEARCH_DIRS = ("agents", "cli")
# Diagnostic probe harnesses live here and are not agent action paths — mirrors
# the exclusion the sibling B1 guard makes.
_ALLOW_DIRS = (_ROOT / "agents" / "test",)


def _offenders() -> list[str]:
    hits: list[str] = []
    for d in _SEARCH_DIRS:
        base = _ROOT / d
        if not base.exists():
            continue
        for py in base.rglob("*.py"):
            if any(ad in py.parents for ad in _ALLOW_DIRS):
                continue
            for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                if _PATTERN.search(line):
                    hits.append(f"{py.relative_to(_ROOT)}:{i}: {line.strip()}")
    return hits


def test_no_outbound_send_call_anywhere():
    offenders = _offenders()
    assert not offenders, (
        "A mail-SEND call was found. This system drafts and observes; it must "
        "never send (G3 — a send crosses B2, `35-` §13, and is a human action). "
        "Use drafts().create/update, not send. Offenders:\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_actually_matches_send_shapes():
    # The guard is only worth anything if it would catch the real call shapes —
    # assert the pattern rather than trust it (the failure mode is a guard that
    # silently matches nothing, like the retrieval-tuple bug this project hit).
    for shape in (
        "service.users().messages().send(userId='me', body=msg)",
        "svc.users().drafts().send(userId='me', body={'id': did})",
        "server.sendmail(frm, to, msg)",
        "import smtplib",
    ):
        assert _PATTERN.search(shape), shape
    for allowed in (
        "svc.users().drafts().create(userId='me', body=draft)",
        "svc.users().drafts().update(userId='me', id=did, body=draft)",
        "svc.users().messages().get(userId='me', id=mid, format='metadata')",
        "svc.users().history().list(userId='me', startHistoryId=h)",
        "queue.send(item)",  # not a mail send — must not false-positive
    ):
        assert not _PATTERN.search(allowed), allowed
