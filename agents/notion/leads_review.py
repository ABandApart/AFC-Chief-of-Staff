"""Gate 0 review in the Notion Leads database (`PRD-notion-integration.md` N9 I2).

Called by `leads_sync`: `pull` from the 05:30 run, `push` from the 06:20 run.

Each morning the Gate 0 daily window (`outreach_discovery.list_for_review`, up to 25
firms with its exploration buckets) is written to Leads as rows with **Channel =
Discovery**, mapped as `notion_sync ('outreach_discoveries', id)`. A row stays until
the firm is reviewed. The operator sets:

- **Review = Approve:** `outreach_discovery.approve(id)`. When the firm is promoted,
  the same row is re-keyed to the new target (`notion_sync` moves to
  `('outreach_targets', target_id)`, Channel becomes Outreach), so one firm stays
  one row. With complete proposals the sequence starts (N8 R4); otherwise the
  operator sets Function state and Start on on the same row.
- **Review = Reject** with a **Reject reason** (`other` needs Next step as the note):
  `decide(id, "reject", …)`. Status becomes Not a fit.
- **Review = Defer:** no label (OQ-H, as in Discord). Review is cleared.
- **Name, Email, Role** edits are corrected contact details, written through
  `outreach_discovery.update_contact`.

A firm reviewed in Discord during the dual-run gets the same row treatment.
Edits by anyone but the operator or the CoS are refused (N-D24).
"""

from __future__ import annotations

import json
from typing import Any

from psycopg.rows import dict_row

from agents._lib.notion_client import content_hash, rich_text
from agents.notion import leads_schema as leads
from agents.notion import operator

SYNC_TABLE = "outreach_discoveries"
CONTACT = {"name": "contact_name", "email": "contact_email", "role": "contact_title"}

_MAPPED_SQL = f"""
    SELECT d.id, d.company_name, d.company_domain, d.company_url, d.segment,
           d.hq_location, d.headcount_band, d.description, d.pain_hook, d.icp_fit_score,
           d.contact_name, d.contact_title, d.contact_email, d.contact_linkedin_url,
           d.verification_note, d.verified_on, d.proposed_scores, d.reviewed_at,
           d.review_decision, d.promoted_target_id,
           ns.notion_page_id, ns.content_hash, ns.synced_fields
    FROM notion_sync ns JOIN outreach_discoveries d ON d.id = ns.entity_id::bigint
    WHERE ns.entity_table = '{SYNC_TABLE}'
"""


def _text(props: dict[str, Any], name: str) -> str:
    spec = props.get(name) or {}
    parts = spec.get("rich_text") or spec.get("title") or []
    return "".join(p.get("plain_text") or (p.get("text") or {}).get("content", "")
                   for p in parts).strip()


def row_values(page: dict[str, Any]) -> dict[str, Any]:
    """What the operator set on a Discovery row (pure)."""
    props = page.get("properties") or {}

    def sel(name: str) -> str | None:
        return ((props.get(name) or {}).get("select") or {}).get("name")

    return {"review": sel("Review"), "reason": sel("Reject reason"),
            "name": _text(props, "Name"), "email": (props.get("Email") or {}).get("email"),
            "role": _text(props, "Role"), "next_step": _text(props, "Next step")}


def background(d: dict[str, Any]) -> str:
    parts = [p for p in (str(d.get("segment") or "").replace("_", " "),
                         f"{d['headcount_band']} staff" if d.get("headcount_band") else "",
                         d.get("hq_location") or "", d.get("description") or "",
                         f"Pain: {d['pain_hook']}" if d.get("pain_hook") else "") if p]
    return " · ".join(parts)


def proposed(d: dict[str, Any]) -> str:
    scores = d.get("proposed_scores") or {}
    if not scores:
        return ""
    keys = ("function_state", "s3_sector_match", "s4_leadership_gap", "s5_team_build_below")
    line = ", ".join(f"{k}: {scores[k]}" for k in keys if scores.get(k) is not None)
    reasons = scores.get("reasons") or ""
    return f"{line}. {reasons}".strip()


def facts(d: dict[str, Any]) -> dict[str, Any]:
    """The CoS-written columns of a Discovery row (pure). Name, Email, and Role are
    written once, at creation: after that they are the operator's corrections."""
    kinds = ", ".join(d.get("verified_on") or [])
    verification = " · ".join(p for p in (kinds, d.get("verification_note") or "") if p)
    website = d.get("company_url") or (f"https://{d['company_domain']}"
                                       if d.get("company_domain") else None)
    return {
        "Channel": {"select": {"name": leads.DISCOVERY_CHANNEL}},
        "Company": {"rich_text": rich_text(d["company_name"])},
        "Website": {"url": website},
        "LinkedIn": {"url": d.get("contact_linkedin_url") or None},
        "Background": {"rich_text": rich_text(background(d)[:2000])},
        "ICP fit": {"number": d.get("icp_fit_score")},
        "Verification": {"rich_text": rich_text(verification[:2000])},
        "Proposed": {"rich_text": rich_text(proposed(d)[:2000])},
        "CoS id": {"number": d["id"]},
    }


def new_row(d: dict[str, Any]) -> dict[str, Any]:
    return {"Name": {"title": rich_text(d.get("contact_name") or d["company_name"])},
            "Email": {"email": d.get("contact_email") or None},
            "Role": {"rich_text": rich_text(d.get("contact_title") or "")},
            "Status": {"status": {"name": "New"}}, **facts(d)}


def contact_agreed(d: dict[str, Any]) -> dict[str, Any]:
    return {"name": d.get("contact_name") or d["company_name"],
            "email": d.get("contact_email") or None, "role": d.get("contact_title") or ""}


def contact_edits(values: dict[str, Any], agreed: dict[str, Any]) -> dict[str, Any]:
    """Contact fields the operator changed since the last agreed values (pure)."""
    out = {}
    for key, column in CONTACT.items():
        if values[key] and values[key] != agreed.get(key):
            out[column] = values[key]
    return out


def review_problem(values: dict[str, Any]) -> str | None:
    """Why a Reject can't be taken yet, or None (pure)."""
    if values["review"] != "Reject":
        return None
    code = leads.REJECT_REASONS.get(values["reason"] or "")
    if code is None:
        return "Reject needs a Reject reason"
    if code == "other" and not values["next_step"]:
        return "Reject reason Other needs the reason in Next step"
    return None


def _record(conn: Any, discovery_id: int, page_id: str, digest: str,
            agreed: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO notion_sync (entity_table, entity_id, notion_page_id, content_hash,
                                 synced_fields, last_pushed_at, last_pulled_at)
        VALUES (%s, %s, %s, %s, %s, now(), now())
        ON CONFLICT (entity_table, entity_id) DO UPDATE SET
            notion_page_id = EXCLUDED.notion_page_id, content_hash = EXCLUDED.content_hash,
            synced_fields = EXCLUDED.synced_fields, last_pushed_at = now(),
            last_pulled_at = now()
        """, (SYNC_TABLE, str(discovery_id), page_id, digest, json.dumps(agreed)))


def _rekey(client: Any, connection: Any, discovery_id: int, target_id: int,
           page: dict[str, Any]) -> None:
    """Turn a Discovery row into its target's Outreach row. The agreed values are
    the row's current ones, so the next push writes Postgres's values without
    reading the old Discovery values as operator edits."""
    agreed = leads.notion_values(page)
    with connection() as conn:
        conn.execute("DELETE FROM notion_sync WHERE entity_table = 'outreach_targets' "
                     "AND entity_id = %s", (str(target_id),))
        conn.execute(
            "UPDATE notion_sync SET entity_table = 'outreach_targets', entity_id = %s, "
            "content_hash = NULL, synced_fields = %s WHERE entity_table = %s "
            "AND entity_id = %s",
            (str(target_id), json.dumps(agreed), SYNC_TABLE, str(discovery_id)))
    client.update_page(page["id"], {"Channel": {"select": {"name": leads.CHANNEL}},
                                    "Review": {"select": None},
                                    "CoS id": {"number": target_id}})


def pull(client: Any, connection: Any, pages: dict[str, dict[str, Any]],
         allowed: frozenset[str]) -> tuple[dict[str, int], list[str]]:
    """Take Gate 0 reviews and contact corrections from Discovery rows."""
    from agents._lib import outreach_discovery as gate0

    counts = dict.fromkeys(("approved", "started", "rejected", "deferred", "contacts"), 0)
    notices: list[str] = []
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_MAPPED_SQL)
        mapped = cur.fetchall()
    for d in mapped:
        page = pages.get(d["notion_page_id"])
        if page is None:
            continue
        name = d["company_name"]
        if d["promoted_target_id"]:          # approved (here or in Discord) earlier
            _rekey(client, connection, d["id"], d["promoted_target_id"], page)
            continue
        if d["reviewed_at"] is not None:     # rejected in Discord
            client.update_page(page["id"], {"Status": {"status": {"name": "Not a fit"}}})
            with connection() as conn:
                conn.execute("DELETE FROM notion_sync WHERE entity_table = %s "
                             "AND entity_id = %s", (SYNC_TABLE, str(d["id"])))
            continue
        v = row_values(page)
        agreed = d.get("synced_fields") or contact_agreed(d)
        edits = contact_edits(v, agreed)
        if (v["review"] or edits) and operator.foreign_edit(page, allowed):
            notices.append(f"{name}: Gate 0 row edited by someone other than the operator; "
                           "not taken")
            continue
        if edits:
            gate0.update_contact(d["company_domain"], edits)
            with connection() as conn:
                _record(conn, d["id"], page["id"], d["content_hash"],
                        {"name": v["name"], "email": v["email"], "role": v["role"]})
            counts["contacts"] += 1
        if (why := review_problem(v)):
            notices.append(f"{name}: {why}")
            continue
        if v["review"] == "Approve":
            result = gate0.approve(d["id"])
            if result and result.get("accepted") and result.get("target_id"):
                counts["approved"] += 1
                counts["started"] += bool(result.get("started"))
                _rekey(client, connection, d["id"], result["target_id"], page)
            elif result:
                notices.append(f"{name}: not approved: {result.get('blocked')}")
                client.update_page(page["id"], {"Review": {"select": None}})
        elif v["review"] == "Reject":
            gate0.decide(d["id"], "reject", reason=leads.REJECT_REASONS[v["reason"]],
                         note=v["next_step"] or None)
            client.update_page(page["id"], {"Status": {"status": {"name": "Not a fit"}}})
            with connection() as conn:
                conn.execute("DELETE FROM notion_sync WHERE entity_table = %s "
                             "AND entity_id = %s", (SYNC_TABLE, str(d["id"])))
            counts["rejected"] += 1
        elif v["review"] == "Defer":
            gate0.decide(d["id"], "defer")
            client.update_page(page["id"], {"Review": {"select": None}})
            counts["deferred"] += 1
    return counts, notices


def push(client: Any, connection: Any, pages: dict[str, dict[str, Any]], source: str) -> int:
    """Write today's Gate 0 window as Discovery rows; refresh rows still unreviewed.
    Returns the number of rows created."""
    from agents._lib import outreach_discovery as gate0

    with connection() as conn:
        window = gate0.list_for_review(conn)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_MAPPED_SQL)
            mapped = {d["id"]: d for d in cur.fetchall()}
    created = 0
    for d in window:
        m = mapped.get(d["id"])
        if m and m["notion_page_id"] in pages:
            f = facts(d)
            if content_hash(f) != m["content_hash"]:
                client.update_page(m["notion_page_id"], f)
                with connection() as conn:
                    _record(conn, d["id"], m["notion_page_id"], content_hash(f),
                            m.get("synced_fields") or contact_agreed(d))
            continue
        page = client.create_page({"type": "data_source_id", "data_source_id": source},
                                  new_row(d))
        with connection() as conn:
            _record(conn, d["id"], page["id"], content_hash(facts(d)), contact_agreed(d))
        created += 1
    return created
