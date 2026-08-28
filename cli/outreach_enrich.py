"""Outreach enrichment — the Apollo probe (read-only) and storing adapter (writes).

`PRD-outreach-company-profile.md` Part 3. Two modes:

  --probe  measures coverage and writes nothing (V2, §3.3). It is what proved
           Apollo before any storing code existed.
  --apply  writes the firmographic spine onto targets and lands funding rounds as
           evidence (§3.1 outcomes 1 & 3). Firmographics run on Apollo's Free tier;
           --with-contacts adds the contact onramp, which no-ops on Free because
           Apollo People is paid-only, and starts filling contact fields the moment
           a plan is added — no rebuild (operator decision, 2026-08-28).

Runs on barry-agent (holds `db-url` and `apollo-api-key`); on the build box the
keychain lookup for the Apollo key raises, by design.

    uv run python -m cli.outreach_enrich --probe              # 5 targets (firmographic coverage)
    uv run python -m cli.outreach_enrich --probe --people     # contact coverage (title/email/li)
    uv run python -m cli.outreach_enrich --probe --raw > out.json   # full payloads for the adapter
    uv run python -m cli.outreach_enrich --apply             # write firmographics, all targets
    uv run python -m cli.outreach_enrich --apply --ids 18,22 # specific targets
    uv run python -m cli.outreach_enrich --apply --with-contacts    # + contact onramp (paid)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from psycopg.rows import dict_row

from agents._lib import creds, db
from agents.outreach import apollo, enrich

# V2 asks for "5 of the 14 real targets" (§3.3). Default the sample to 5.
DEFAULT_SAMPLE = 5

_TARGETS_SQL = """
    SELECT id, company_name, company_domain
      FROM outreach_targets
     WHERE company_domain IS NOT NULL
       AND status NOT IN ('archived', 'dropped')
     ORDER BY id
"""

# The People probe needs a named contact + domain to match on.
_CONTACTS_SQL = """
    SELECT id, company_name, company_domain, contact_name
      FROM outreach_targets
     WHERE company_domain IS NOT NULL
       AND contact_name IS NOT NULL
       AND status NOT IN ('archived', 'dropped')
     ORDER BY id
"""

# The storing adapter enriches every active target that has a domain; contact_name
# rides along (nullable) so the contact onramp can attempt a match where one exists.
_APPLY_SQL = """
    SELECT id, company_name, company_domain, contact_name
      FROM outreach_targets
     WHERE company_domain IS NOT NULL
       AND status NOT IN ('archived', 'dropped')
     ORDER BY id
"""


def _select(cur, sql: str, ids: list[int] | None, limit: int | None) -> list[dict[str, Any]]:
    cur.execute(sql)
    rows = cur.fetchall()
    if ids:
        wanted = set(ids)
        return [r for r in rows if r["id"] in wanted]
    return rows if limit is None else rows[:limit]


def _select_targets(cur, ids: list[int] | None, limit: int) -> list[dict[str, Any]]:
    return _select(cur, _TARGETS_SQL, ids, limit)


def _fmt(value: Any) -> str:
    return "—" if value in (None, "") else str(value)


def run_probe(ids: list[int] | None, limit: int, raw: bool = False) -> int:
    """Fetch + map + count. Returns a process exit code.

    With `raw=True`, emit ONLY a JSON array of `{id, company_name, organization}`
    (the full Apollo payload per target, `organization` null when none matched) and
    nothing else — so it can be redirected to a file and used as the ground truth
    for building the storing adapter. Still read-only.
    """
    try:
        api_key = creds.keychain_get(apollo.APOLLO_KEY_ITEM)
    except RuntimeError as exc:
        print(
            f"error: {exc}\n"
            f"The V2 probe needs the Apollo API key in the keychain as "
            f"'{apollo.APOLLO_KEY_ITEM}'. Add it on the runtime account, then re-run.",
            file=sys.stderr,
        )
        return 2

    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            targets = _select_targets(cur, ids, limit)

    if not targets:
        print("No matching targets.", file=sys.stderr)
        return 1

    # One Apollo call per target, up front — both output modes read from this.
    try:
        fetched = [
            (t, apollo.enrich_organization(t["company_domain"], api_key)) for t in targets
        ]
    except apollo.ApolloPlanError as exc:
        print(f"error: {exc} (403 API_INACCESSIBLE). A paid Apollo plan is required.",
              file=sys.stderr)
        return 3

    if raw:
        dump = [
            {"id": t["id"], "company_name": t["company_name"], "organization": org}
            for t, org in fetched
        ]
        print(json.dumps(dump, indent=2, default=str))
        return 0

    mapped_rows: list[dict[str, Any]] = []
    raw_keys: set[str] = set()
    no_org: list[str] = []

    print(f"Apollo V2 coverage probe — {len(targets)} target(s)\n")
    for t, org in fetched:
        if org is None:
            no_org.append(t["company_name"])
            print(f"  {t['company_name']:<28} → no organization returned")
            continue
        raw_keys.update(org.keys())
        spine = apollo.map_organization(org)
        mapped_rows.append(spine)
        filled = sum(1 for f in apollo.SPINE_FIELDS if spine[f] not in (None, ""))
        print(
            f"  {t['company_name']:<28} → {filled}/{len(apollo.SPINE_FIELDS)} spine fields"
            f"  [sector={_fmt(spine['sector'])}, headcount={_fmt(spine['headcount'])}, "
            f"raised={_fmt(spine['total_raised_usd'])}, founded={_fmt(spine['founded_year'])}]"
        )

    n = len(targets)
    print(f"\nField coverage across {n} target(s):")
    counts = apollo.coverage(mapped_rows)
    for field in apollo.SPINE_FIELDS:
        c = counts[field]
        bar = "█" * c + "·" * (n - c)
        print(f"  {field:<18} {c}/{n}  {bar}")

    if no_org:
        print(f"\n{len(no_org)} target(s) returned no Apollo organization: "
              f"{', '.join(no_org)}")

    # The union of raw Apollo keys teaches the true schema for building the
    # storing adapter next — printed so the run itself is the source of truth,
    # not this file's field guesses.
    print(f"\nRaw Apollo organization keys observed ({len(raw_keys)}):")
    print("  " + ", ".join(sorted(raw_keys)) if raw_keys else "  (none)")
    return 0


def run_people_probe(ids: list[int] | None, limit: int) -> int:
    """People-enrichment coverage: can Apollo fill the contact gap (title, email,
    LinkedIn) for our known contacts? Read-only, reveal flags OFF — so it prints
    statuses and counts, NEVER a person's raw email or LinkedIn. Returns an exit
    code.
    """
    try:
        api_key = creds.keychain_get(apollo.APOLLO_KEY_ITEM)
    except RuntimeError as exc:
        print(
            f"error: {exc}\n"
            f"The People probe needs the Apollo API key in the keychain as "
            f"'{apollo.APOLLO_KEY_ITEM}'. Add it on the runtime account, then re-run.",
            file=sys.stderr,
        )
        return 2

    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            targets = _select(cur, _CONTACTS_SQL, ids, limit)

    if not targets:
        print("No targets with a contact_name to match on.", file=sys.stderr)
        return 1

    mapped_rows: list[dict[str, Any]] = []
    no_match: list[str] = []

    print(f"Apollo People coverage probe — {len(targets)} contact(s) "
          f"(reveal OFF; no raw values printed)\n")
    for t in targets:
        try:
            person = apollo.match_person(t["contact_name"], t["company_domain"], api_key)
        except apollo.ApolloPlanError as exc:
            print(f"\nerror: {exc} (403 API_INACCESSIBLE).\n"
                  f"Apollo's People endpoints are paid-only; the Free plan cannot "
                  f"measure contact coverage. A paid plan is required.", file=sys.stderr)
            return 3
        if person is None:
            no_match.append(t["company_name"])
            print(f"  {t['company_name']:<28} → no person matched")
            continue
        fields = apollo.map_person(person)
        mapped_rows.append(fields)
        print(
            f"  {t['company_name']:<28} → "
            f"title={'y' if fields['contact_title'] else '—'}, "
            f"linkedin={'y' if fields['contact_linkedin_url'] else '—'}, "
            f"email={apollo.email_kind_of(fields['contact_email'])}"
            f"({_fmt(fields['email_status'])})"
        )

    n = len(targets)
    cov = apollo.person_coverage(mapped_rows)
    print(f"\nContact coverage across {n} contact(s):")
    print(f"  title present     {cov['title']}/{n}")
    print(f"  linkedin present  {cov['linkedin']}/{n}")
    ek = cov["email_kinds"]
    print(f"  email             revealed {ek['revealed']} · locked {ek['locked']} "
          f"· none {ek['none']}")
    print("    (locked = Apollo has it but a credit is needed to reveal — decides "
          "whether card field #6 closes for free)")
    statuses = ", ".join(f"{k}:{v}" for k, v in sorted(cov["email_status"].items()))
    print("  email_status      " + statuses)
    if no_match:
        print(f"\n{len(no_match)} contact(s) unmatched: {', '.join(no_match)}")
    return 0


def run_apply(ids: list[int] | None, limit: int | None, with_contacts: bool) -> int:
    """Storing adapter: write Apollo firmographics (and funding evidence) onto
    targets. `with_contacts` also attempts the contact onramp, which no-ops on Free
    (People is plan-gated). Each target's writes are one transaction. Exit code."""
    try:
        api_key = creds.keychain_get(apollo.APOLLO_KEY_ITEM)
    except RuntimeError as exc:
        print(f"error: {exc}\nAdd the Apollo API key as '{apollo.APOLLO_KEY_ITEM}' "
              f"on the runtime account, then re-run.", file=sys.stderr)
        return 2

    with db.connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            targets = _select(cur, _APPLY_SQL, ids, limit)
        if not targets:
            print("No matching targets.", file=sys.stderr)
            return 1

        print(f"Enriching {len(targets)} target(s)"
              f"{' + contacts (onramp)' if with_contacts else ''}...\n")
        no_org: list[str] = []
        plan_gated = False
        for t in targets:
            try:
                with conn.transaction():
                    res = enrich.enrich_target(conn, t, api_key,
                                               with_contacts=with_contacts)
            except apollo.ApolloPlanError as exc:
                # Only reachable via the org call; contacts catch their own gate.
                print(f"error: {exc} (403). A paid Apollo plan is required.",
                      file=sys.stderr)
                return 3
            if not res["org"]:
                no_org.append(t["company_name"])
                print(f"  {t['company_name']:<28} → no organization; skipped")
                continue
            if res.get("contacts") == "plan_gated":
                plan_gated = True
            print(f"  {t['company_name']:<28} → {res['firmographic_fields']}"
                  f"/{len(apollo.SPINE_FIELDS)} firmographic, "
                  f"{res['funding_new']} funding round(s), contacts={res['contacts']}")

    if no_org:
        print(f"\n{len(no_org)} target(s) had no Apollo organization: "
              f"{', '.join(no_org)}")
    if plan_gated:
        print("\ncontacts=plan_gated: Apollo People is paid-only; firmographics were "
              "written, contact fields left for after a plan upgrade (the onramp).")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true",
                      help="Read-only coverage probe (no writes).")
    mode.add_argument("--apply", action="store_true",
                      help="Storing adapter: WRITE Apollo firmographics + funding "
                           "evidence onto targets. Default: all active targets.")
    parser.add_argument("--limit", type=int, default=None,
                        help=f"Cap the target count (probe defaults to {DEFAULT_SAMPLE}; "
                             f"apply defaults to all).")
    parser.add_argument("--ids", type=str, default=None,
                        help="Comma-separated target ids instead of a sample.")
    parser.add_argument("--raw", action="store_true",
                        help="(probe) Emit only the full Apollo JSON per target; "
                             "still read-only. Redirect to a file.")
    parser.add_argument("--people", action="store_true",
                        help="(probe) People contact-coverage instead of firmographic. "
                             "Reveal OFF; prints statuses/counts, never raw values.")
    parser.add_argument("--with-contacts", action="store_true",
                        help="(apply) Also run the contact onramp. No-ops on Free "
                             "(Apollo People is paid-only); ready once a plan is added.")
    args = parser.parse_args(argv)

    if args.raw and args.people:
        parser.error("--raw is not supported with --people (personal data is not "
                     "dumped raw; the People probe reports counts only)")
    if (args.raw or args.people) and args.apply:
        parser.error("--raw/--people are probe-only flags")
    if args.with_contacts and not args.apply:
        parser.error("--with-contacts applies to --apply")

    ids = [int(x) for x in args.ids.split(",")] if args.ids else None
    try:
        if args.apply:
            return run_apply(ids, args.limit, with_contacts=args.with_contacts)
        sample = args.limit if args.limit is not None else DEFAULT_SAMPLE
        if args.people:
            return run_people_probe(ids, sample)
        return run_probe(ids, sample, raw=args.raw)
    finally:
        db.close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
