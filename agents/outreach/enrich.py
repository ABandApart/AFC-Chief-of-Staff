"""Apollo enrichment — the storing adapter (Track O Part 3).

Where the V2 probe (`cli/outreach_enrich.py --probe`) only measured coverage, this
is the write path §3.1 specifies. Per target it:

  1. writes the firmographic spine onto `outreach_targets` (outcome 1), sparse-safe
     and stamping `headcount_asof` = the fetch date;
  2. lands each dated funding round as an `outreach_evidence` row (outcome 3) —
     `first_seen_at` = the round's own date, the market date on the evidence, the
     same rule Part 2's promotion follows (R1.4);
  3. attribute history comes for free from the audit trigger (outcome 2).

**Contacts are a scaffolded onramp, not active on Free.** Apollo's People endpoint
is paid-only (`ApolloPlanError`); `--with-contacts` wires the full fetch → map →
write path, and on Free it catches the plan gate and continues firmographics-only.
The day the account is upgraded it starts filling contact fields with no rebuild —
that is the onramp the operator asked for (2026-08-28).

R21 is GREEN for Apollo retention (§3.2a); this stores Apollo fields in the
first-party CRM within terms. `stated_use_of_funds` (T12/T21) is NOT available
from Apollo — it comes from press-release text, so funding evidence here carries
the round facts only.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from agents._lib import outreach
from agents.outreach import apollo

logger = logging.getLogger(__name__)

FUNDING_FACT_KIND = "funding_round"


def _parse_date(value: object) -> date | None:
    """Apollo dates are ISO strings ('2023-04-01'). An undateable round cannot be
    honest evidence (there is nowhere truthful to put first_seen_at), so it is
    skipped rather than stamped with today."""
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def funding_facts(org: dict) -> list[dict[str, Any]]:
    """Pure: Apollo `funding_events` → dated funding-round facts. Each carries an
    `event_date` (the round's own date) that becomes the evidence `first_seen_at`.
    Undated events are dropped."""
    facts: list[dict[str, Any]] = []
    for event in org.get("funding_events") or []:
        event_date = _parse_date(event.get("date"))
        if event_date is None:
            continue
        # Dedup on Apollo's event id where present; otherwise the date+type pair.
        ident = event.get("id") or f"{event_date.isoformat()}:{event.get('type') or ''}"
        payload = {
            "round_type": event.get("type"),
            "amount": event.get("amount"),
            "currency": event.get("currency"),
            "investors": event.get("investors"),
        }
        news_url = event.get("news_url")
        facts.append(
            {
                "fact_kind": FUNDING_FACT_KIND,
                "source_kind": "apollo",
                "dedup_key": f"apollo:{ident}",
                "payload": {k: v for k, v in payload.items() if v},
                "source_url": news_url if isinstance(news_url, str) else None,
                "source_excerpt": None,
                "event_date": event_date,
            }
        )
    return facts


def _apply_funding(conn: object, target_id: int, org: dict) -> int:
    """Write each dated funding round as evidence. Returns rows newly seen."""
    new = 0
    for fact in funding_facts(org):
        event_date = fact.pop("event_date")
        row = outreach.evidence_row(fact, target_id=target_id, today=event_date)
        if outreach.upsert_evidence(conn, row):
            new += 1
    return new


def _apply_contacts(conn: object, target: dict, api_key: str) -> str:
    """The onramp. Fetch + map + write contact fields. Returns an outcome label;
    `plan_gated` on Free (People is paid-only) — caught, not raised, so the
    firmographic write already committed stands."""
    try:
        person = apollo.match_person(target["contact_name"], target["company_domain"], api_key)
    except apollo.ApolloPlanError:
        return "plan_gated"
    if person is None:
        return "no_match"
    outreach.update_contact(conn, target["id"], apollo.map_person(person))
    return "written"


def enrich_target(
    conn: object,
    target: dict,
    api_key: str,
    *,
    with_contacts: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    """Enrich one target: firmographics + funding always; contacts only when asked
    and reachable. Returns a per-target result summary. Read/writes within the
    caller's transaction — the caller commits."""
    today = today or date.today()
    org = apollo.enrich_organization(target["company_domain"], api_key)
    if org is None:
        return {"target_id": target["id"], "org": False}

    spine = apollo.map_organization(org)
    outreach.update_firmographics(conn, target["id"], spine, today=today)
    funding_new = _apply_funding(conn, target["id"], org)

    contacts = "skipped"
    if with_contacts and target.get("contact_name"):
        contacts = _apply_contacts(conn, target, api_key)

    filled = sum(1 for f in apollo.SPINE_FIELDS if spine[f] not in (None, ""))
    logger.info(
        "enrich: target %s — %s/%s firmographic fields, %s funding round(s), contacts=%s",
        target["id"], filled, len(apollo.SPINE_FIELDS), funding_new, contacts,
    )
    return {
        "target_id": target["id"],
        "org": True,
        "firmographic_fields": filled,
        "funding_new": funding_new,
        "contacts": contacts,
    }
