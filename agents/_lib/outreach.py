"""Outreach CRM shared core (Track O) — target upsert + evidence poll semantics.

The write paths every outreach surface shares. Kept out of the poller (and out of
any cog) because three different callers need the same rules: the CSV/manual
import (`cli/outreach_import.py`, D1), Roy Kent's high-fit inbound hand-off
(`35-` §5 D2), and the evidence poller (`agents/outreach/evidence.py`, §6).

Two rule sets live here, both from `architecture/35-outreach-crm.md`:

**D1 — import never overwrites judgement (§5).** An import UPSERTs on
`company_domain` and may refresh firmographics, but `s2`–`s5`, `function_state`,
`status`, and `stalled_reason` are *never* touched: those are the operator's
two-tab diagnostic and pipeline decisions, and a spreadsheet re-import must not
silently undo them. `trigger_kind`/`trigger_date` move only *forward* — a stale
row in a CSV cannot rewind the arc that everything else anchors on.

**Poll semantics — the proprietary datum (§6).** Each poll upserts on
`(target_id, fact_kind, dedup_key)`: a new key sets `first_seen_at = today`, a
seen key advances `last_seen_at`, and a key that was open but is absent from this
poll gets `closed_at = today`. After two weeks of this you hold posting-age data
that cannot be bought retroactively — which is why the loop should start before
anything else in Track O is finished.

Everything that mutates state here is also covered by the DB audit trigger
(migration 0013), so a correction is traceable without app-level logging.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from agents._lib import screening

logger = logging.getLogger(__name__)

# H1 (§11): evidence carries typed, SHORT fields — never a page dump. The column
# has a 500-char CHECK; we truncate here so a long posting title is stored rather
# than rejected mid-poll.
MAX_EXCERPT_CHARS = 500

# Columns an import may refresh. `s2`–`s5`, `function_state`, `status`, and
# `stalled_reason` are deliberately ABSENT — see the D1 rule above. Adding a
# column here is a decision about whose knowledge wins, not a formatting choice.
_IMPORT_REFRESHABLE = (
    "company_name",
    "company_url",
    "careers_url",
    "sector",
    "stage",
    "contact_name",
    "contact_first_name",
    "contact_role",
    "contact_email",
    "contact_linkedin_url",
    # `function` is refreshable, unlike `function_state`. The CSV is allowed to
    # name which function is being pitched into; only its *diagnosis* is the
    # operator's two-tab judgement and therefore protected (D1).
    "function",
    "cognee_node_id",
)


def clean_field(value: object, *, max_chars: int | None = None) -> str | None:
    """H2-harden a scraped/imported string (or pass through None).

    Applied to everything crossing into the outreach tables: an invisible-char or
    bidi payload in a job title ends up in an email a human sends, which is the
    threat H2 exists for now that nothing is generated (`35-` §11).
    """
    if value is None:
        return None
    text, removed = screening.harden(str(value))
    text = text.strip()
    if removed:
        logger.warning("outreach: stripped %d invisible char(s) from a field", removed)
    if max_chars is not None:
        text = text[:max_chars]
    return text or None


# Free/consumer mail hosts. An address at one of these says nothing about the
# company, so it must never become a `company_domain` — two unrelated leads on
# gmail.com would collide onto ONE target row via the domain dedup key.
FREE_EMAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "msn.com", "yahoo.com", "ymail.com", "aol.com", "icloud.com", "me.com",
    "mac.com", "proton.me", "protonmail.com", "pm.me", "gmx.com", "mail.com",
    "zoho.com", "fastmail.com", "hey.com", "yandex.com", "qq.com",
})


def company_domain_from_email(email: str | None) -> str | None:
    """The company domain implied by a work email, or None (pure).

    Returns None for a free/consumer host or anything unparseable. None is a
    perfectly good answer: it means "this lead has no company identity we can
    key on", and the caller skips creating an outreach target rather than
    inventing one.
    """
    if not email or "@" not in email:
        return None
    domain = normalize_domain(email.rsplit("@", 1)[1])
    if not domain or "." not in domain or domain in FREE_EMAIL_DOMAINS:
        return None
    return domain


# --- [function] derivation (migration 0015) ----------------------------------
# `[function]` is a bare noun for substitution — "I run revenue fractionally".
# It is derived from an open **leadership** req's title, with the level stripped.
#
# The leadership restriction is the load-bearing part, not fussiness. T10's whole
# mechanic is that an *executive* search runs 90–120 days, and `35-` §2 already
# separates `open_role` (feeds S4, the leadership gap) from `ic_hire` (feeds S5,
# team-build-below). Deriving a function from "Account Executive" would claim the
# revenue function is unled when the company is simply hiring a rep — a specific,
# confident, checkable falsehood of exactly the kind R19 is about.

# Titles at or above the seat a fractional engagement holds. "Manager" and "Lead"
# are deliberately absent: they are first-line or IC-plus, and a manager req does
# not mean the function is unowned.
_LEADERSHIP_MARKERS = (
    "chief", "vice president", "vp", "svp", "evp", "head of", "director",
    "president", "partner",
)

# Words carrying rank rather than function — stripped once a marker is found.
_RANK_WORDS = frozenset({
    "senior", "sr", "junior", "jr", "global", "regional", "group", "deputy",
    "associate", "assistant", "interim", "fractional", "officer", "of", "and",
    "the", "for", "executive",
})

# Acronyms that carry their own function. `CPO` is omitted on purpose — it means
# Chief Product Officer or Chief People Officer depending on the company, and
# guessing wrong puts the wrong noun in a sentence about what is unled.
_ACRONYM_FUNCTIONS = {
    "cro": "revenue", "cmo": "marketing", "cfo": "finance",
    "coo": "operations", "cto": "technology", "chro": "people",
    "ciso": "security", "cio": "technology",
}


def derive_function(role_title: str | None) -> str | None:
    """The bare function noun implied by a leadership job title (pure).

    Returns None — meaning "operator, you tell me" — for IC titles, bare
    acronyms we cannot disambiguate, and anything that reduces to nothing. A
    NULL `function` blocks the placeholder; a wrong one ships a confident claim
    about the wrong department.

        "VP Revenue"                 -> "revenue"
        "Chief Revenue Officer"      -> "revenue"
        "Senior Director, Finance"   -> "finance"
        "Account Executive"          -> None      (an IC req, not a leadership gap)
    """
    if not role_title:
        return None
    text = re.sub(r"[^a-z0-9\s]+", " ", role_title.lower())
    words = text.split()
    if not words:
        return None

    if len(words) == 1 and words[0] in _ACRONYM_FUNCTIONS:
        return _ACRONYM_FUNCTIONS[words[0]]

    # Find the leadership marker. Longest markers first so "vice president"
    # matches before the bare "vp", and "head of" consumes its own connector.
    remainder: list[str] | None = None
    for marker in sorted(_LEADERSHIP_MARKERS, key=len, reverse=True):
        marker_words = marker.split()
        for i in range(len(words) - len(marker_words) + 1):
            if words[i:i + len(marker_words)] == marker_words:
                remainder = words[:i] + words[i + len(marker_words):]
                break
        if remainder is not None:
            break
    if remainder is None:
        return None            # no leadership marker — an IC req

    kept = [w for w in remainder if w not in _RANK_WORDS]
    return " ".join(kept) or None


def suggest_first_name(contact_name: str | None) -> str | None:
    """A *suggested* greeting name from a full name (pure) — never auto-applied.

    Display only: the gaps report offers it so filling `contact_first_name` is
    quick. It is deliberately not written anywhere, because the cases it gets
    wrong (honorifics, initials) are exactly the ones that would produce
    "Hi Dr.," in the first line a prospect reads.
    """
    if not contact_name:
        return None
    honorifics = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "rev"}
    for token in contact_name.split():
        cleaned = token.strip(".,").strip()
        if not cleaned or cleaned.lower() in honorifics:
            continue
        if len(cleaned) <= 1:      # a bare initial tells us nothing
            return None
        return cleaned
    return None


def normalize_domain(raw: str) -> str:
    """Normalize a company domain to the import dedup key (pure).

    Lowercased, scheme/path/`www.` stripped. This is THE dedup key (§5 D1, R8) —
    `https://Acme.com/`, `www.acme.com`, and `acme.com` are one company, and a
    duplicate row would inflate the live count against the capacity cap.
    """
    text = (raw or "").strip().lower()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.split("/", 1)[0].split("?", 1)[0]
    if text.startswith("www."):
        text = text[4:]
    return text.strip().strip(".")


# --- targets ------------------------------------------------------------------


def upsert_target(conn: object, target: dict[str, Any]) -> dict[str, Any]:
    """UPSERT one target on `company_domain`, applying the D1 import rules.

    Never a blind insert (§5). Returns the resulting row. `company_domain`,
    `company_name`, `stage`, `trigger_kind`, and `trigger_date` are required;
    everything else is optional and only overwrites when non-NULL.
    """
    row = dict(target)
    row["company_domain"] = normalize_domain(row["company_domain"])
    for key in ("company_name", "company_url", "careers_url", "sector", "stage",
                "contact_name", "contact_first_name", "contact_role",
                "contact_email", "contact_linkedin_url", "trigger_kind",
                "trigger_source_url", "function", "cognee_node_id"):
        if key in row:
            row[key] = clean_field(row[key])
    for key in ("company_url", "careers_url", "sector", "contact_name",
                "contact_first_name", "contact_role", "contact_email",
                "contact_linkedin_url", "trigger_source_url", "function",
                "cognee_node_id", "prospect_id"):
        row.setdefault(key, None)

    # Refreshable columns COALESCE so a sparse import doesn't blank existing data.
    # trigger_* move forward only. Judgement columns are absent by construction.
    refresh = ",\n                ".join(
        f"{c} = COALESCE(EXCLUDED.{c}, outreach_targets.{c})"
        for c in _IMPORT_REFRESHABLE
    )
    sql = f"""
        INSERT INTO outreach_targets (
            company_name, company_domain, company_url, careers_url, sector, stage,
            contact_name, contact_first_name, contact_role, contact_email,
            contact_linkedin_url, trigger_kind, trigger_date, trigger_source_url,
            function, cognee_node_id, prospect_id
        ) VALUES (
            %(company_name)s, %(company_domain)s, %(company_url)s, %(careers_url)s,
            %(sector)s, %(stage)s, %(contact_name)s, %(contact_first_name)s,
            %(contact_role)s, %(contact_email)s, %(contact_linkedin_url)s,
            %(trigger_kind)s, %(trigger_date)s, %(trigger_source_url)s,
            %(function)s, %(cognee_node_id)s, %(prospect_id)s
        )
        ON CONFLICT (company_domain) DO UPDATE SET
                {refresh},
                -- A more recent trigger replaces the anchor and its provenance
                -- together; an older one changes nothing. The arc measures from
                -- trigger_date, so rewinding it would shift every touch window.
                trigger_kind = CASE
                    WHEN EXCLUDED.trigger_date > outreach_targets.trigger_date
                    THEN EXCLUDED.trigger_kind
                    ELSE outreach_targets.trigger_kind END,
                trigger_source_url = CASE
                    WHEN EXCLUDED.trigger_date > outreach_targets.trigger_date
                    THEN EXCLUDED.trigger_source_url
                    ELSE outreach_targets.trigger_source_url END,
                trigger_date = GREATEST(outreach_targets.trigger_date, EXCLUDED.trigger_date),
                prospect_id = COALESCE(outreach_targets.prospect_id, EXCLUDED.prospect_id)
        RETURNING *, (xmax = 0) AS was_inserted
    """
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(sql, row)
        return cur.fetchone()


# --- evidence -----------------------------------------------------------------


def evidence_row(fact: dict[str, Any], *, target_id: int, today: date) -> dict[str, Any]:
    """Map an adapter's observed fact → the `outreach_evidence` row (pure).

    Applies H1 (typed, bounded fields) and H2 (unicode hardening) at the write
    boundary, so no adapter can widen what reaches storage.
    """
    payload = {
        k: clean_field(v, max_chars=200)
        for k, v in (fact.get("payload") or {}).items()
    }
    return {
        "target_id": target_id,
        "fact_kind": fact["fact_kind"],
        "payload": {k: v for k, v in payload.items() if v is not None},
        "source_kind": fact["source_kind"],
        "source_url": clean_field(fact.get("source_url")),
        "source_excerpt": clean_field(fact.get("source_excerpt"), max_chars=MAX_EXCERPT_CHARS),
        "first_seen_at": today,
        "last_seen_at": today,
        "dedup_key": fact["dedup_key"],
    }


def upsert_evidence(conn: object, row: dict[str, Any]) -> bool:
    """Insert or confirm one evidence fact. Returns True if newly seen.

    New key → `first_seen_at = today` (the datum no provider sells). Existing key
    → advance `last_seen_at` only; **`first_seen_at` is never rewritten**, which
    is what keeps posting age honest across polls.

    A fact that had closed and is now back is re-opened (`closed_at = NULL`)
    rather than re-dated. The reopen itself is not lost: the audit trigger records
    the `closed_at` transition, and a reopened req is a meaningful signal in its
    own right (§10 — a company that hired instead of engaging).
    """
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            INSERT INTO outreach_evidence (
                target_id, fact_kind, payload, source_kind, source_url,
                source_excerpt, first_seen_at, last_seen_at, dedup_key
            ) VALUES (
                %(target_id)s, %(fact_kind)s, %(payload)s, %(source_kind)s,
                %(source_url)s, %(source_excerpt)s, %(first_seen_at)s,
                %(last_seen_at)s, %(dedup_key)s
            )
            ON CONFLICT (target_id, fact_kind, dedup_key) DO UPDATE SET
                last_seen_at   = EXCLUDED.last_seen_at,
                payload        = EXCLUDED.payload,
                source_url     = COALESCE(EXCLUDED.source_url, outreach_evidence.source_url),
                source_excerpt = COALESCE(EXCLUDED.source_excerpt,
                                          outreach_evidence.source_excerpt),
                closed_at      = NULL
            RETURNING (xmax = 0) AS was_inserted
            """,
            {**row, "payload": Jsonb(row["payload"])},
        )
        return bool(cur.fetchone()[0])


def close_absent_evidence(
    conn: object, *, target_id: int, fact_kind: str, seen_keys: list[str], today: date
) -> int:
    """Close facts of one kind that this poll did not see. Returns rows closed.

    **Only ever called after a poll the adapter confirmed it parsed.** A failed
    fetch that fell through to "zero facts" would close every open req at once,
    and the packet arithmetic would then quietly describe reqs as closed that are
    open — the mirror image of R19, and just as wrong in a founder's inbox.
    """
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            UPDATE outreach_evidence
            SET closed_at = %(today)s
            WHERE target_id = %(target_id)s
              AND fact_kind = %(fact_kind)s
              AND closed_at IS NULL
              AND NOT (dedup_key = ANY(%(seen_keys)s))
            """,
            {"target_id": target_id, "fact_kind": fact_kind,
             "seen_keys": seen_keys, "today": today},
        )
        return cur.rowcount


def backfill_function(conn: object, target_id: int, role_titles: list[str]) -> str | None:
    """Fill a NULL `function` from the leadership reqs just observed.

    Returns the value written, or None if nothing was written. **Only ever fills
    a NULL** — `WHERE function IS NULL` in the statement, not a check in Python,
    so a value the operator corrected survives every subsequent poll regardless
    of what the board says next.

    Where several leadership reqs are open, the first derivable one wins. That is
    arbitrary between equals and deliberately so: the column exists to be
    overridden, and guessing harder at which of two open exec seats to name would
    not make the guess more right.
    """
    for title in role_titles:
        derived = derive_function(title)
        if derived is None:
            continue
        with conn.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(
                "UPDATE outreach_targets SET function = %s "
                "WHERE id = %s AND function IS NULL",
                (derived, target_id),
            )
            if cur.rowcount:
                logger.info(
                    "outreach: derived function=%r for target %s from %r",
                    derived, target_id, title,
                )
                return derived
        return None            # a function was already set — leave it alone
    return None


def pollable_targets(conn: object) -> list[dict[str, Any]]:
    """Targets with a careers page still worth polling.

    Excludes terminal states — an archived, dropped, or engaged company is not
    generating signal worth paying for (§10: polling stops on expiry).
    """
    with conn.cursor(row_factory=dict_row) as cur:  # type: ignore[attr-defined]
        cur.execute(
            """
            SELECT id, company_name, company_domain, careers_url
            FROM outreach_targets
            WHERE careers_url IS NOT NULL
              AND status NOT IN ('archived', 'dropped', 'engaged')
            ORDER BY id
            """
        )
        return cur.fetchall()
