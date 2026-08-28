"""Apollo organizations/search — pagination probe (Track O, Part 0 sourcing).

barry-agent found `organizations/search` is on Apollo's Free tier and returns
firms by ICP filter (~33k matched the training/coaching ICP). But **matched is not
retrievable.** Three caps decide how much of that set a discovery channel could
actually pull on Free, and none were measured:

  1. **per_page** — records per request. The search family documents up to 100;
     Free may honor fewer. Fewer requests per record = a cheaper channel.
  2. **page-depth ceiling** — the search family caps at 50,000 records (100/page ×
     500 pages); Free may cap far shallower. You cannot page past it however large
     `total_entries` is.
  3. **rate limit** — requests per minute/hour/day (docs cite 600/hour for the
     PAID search; Free is tighter). This paces how fast the reachable pages drain.

Retrievable ≈ min(total_entries, honored_per_page × reachable_pages), drained at
the rate limit. That number — not the 33k `total_entries` — is what a sourcing
design must budget against. This probe measures it, **read-only, storing nothing**.

Frugality is deliberate: the probe spends the very quota it is measuring, so it
walks only a few pages, then does ONE deep-page request to test the ceiling
cheaply rather than walking to it.

    uv run python -m cli.apollo_search_probe
    uv run python -m cli.apollo_search_probe --keyword "leadership development" --employees 50,200
    uv run python -m cli.apollo_search_probe --per-page 100 --max-pages 3 --deep-page 501
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from agents._lib import creds
from agents.outreach import apollo

# Header names Apollo has used for rate-limit budget, matched case-insensitively by
# prefix — printed verbatim when present so we report the real budget, not a guess.
_RATE_HINT_PREFIXES = ("x-rate-limit", "x-minute", "x-hourly", "x-24-hour", "x-daily",
                       "retry-after", "x-ratelimit")


def walk_pages(
    search_fn: Callable[[int, int], dict], *, per_page: int, max_pages: int
) -> dict[str, Any]:
    """Walk pages 1..max_pages via `search_fn(page, per_page)`. Records per-page
    counts and NEW (deduped) domains, and stops early on an empty page, the last
    page, a plan gate, or a rate cap. Pure w.r.t. the injected search_fn — the
    exceptions it translates (ApolloPlanError/ApolloRateLimitError) are the findings."""
    pages: list[dict[str, Any]] = []
    seen: set[str] = set()
    total_entries = total_pages = None
    stopped = "max_pages"

    for page in range(1, max_pages + 1):
        try:
            resp = search_fn(page, per_page)
        except apollo.ApolloPlanError:
            stopped = "plan_gated"
            break
        except apollo.ApolloRateLimitError as exc:
            stopped = f"rate_limited@page{page}"
            if exc.retry_after:
                stopped += f" (retry_after={exc.retry_after}s)"
            break
        except apollo.ApolloCreditsError:
            stopped = f"credits_exhausted@page{page}"
            break

        summary = apollo.search_page_summary(resp)
        pg = summary["pagination"]
        total_entries = pg.get("total_entries", total_entries)
        total_pages = pg.get("total_pages", total_pages)
        new = [d for d in summary["domains"] if d not in seen]
        seen.update(new)
        pages.append({
            "page": page,
            "returned": summary["returned"],
            "with_domain": summary["with_domain"],
            "honored_per_page": pg.get("per_page"),
            "new_domains": len(new),
        })
        if summary["returned"] == 0:
            stopped = "empty_page"
            break
        if total_pages is not None and page >= total_pages:
            stopped = "reached_total_pages"
            break

    return {
        "pages": pages,
        "unique_domains": len(seen),
        "total_entries": total_entries,
        "total_pages": total_pages,
        "stopped": stopped,
    }


def _build_filters(args: argparse.Namespace) -> dict[str, Any]:
    filters: dict[str, Any] = {}
    if args.keyword:
        filters["q_organization_keyword_tags"] = [args.keyword]
    if args.employees:
        filters["organization_num_employees_ranges"] = [args.employees]
    if args.location:
        filters["organization_locations"] = [args.location]
    return filters


def _rate_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in headers.items()
            if any(k.startswith(p) for p in _RATE_HINT_PREFIXES)}


def run(args: argparse.Namespace) -> int:
    try:
        api_key = creds.keychain_get(apollo.APOLLO_KEY_ITEM)
    except RuntimeError as exc:
        print(f"error: {exc}\nAdd the Apollo API key as '{apollo.APOLLO_KEY_ITEM}' "
              f"on the runtime account, then re-run.", file=sys.stderr)
        return 2

    filters = _build_filters(args)
    captured: dict[str, str] = {}

    def _post(url: str, headers: dict[str, str], body: bytes) -> bytes:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            captured.clear()
            captured.update({k.lower(): v for k, v in resp.headers.items()})
            return resp.read()

    def search_fn(page: int, per_page: int) -> dict:
        return apollo.search_organizations(
            filters, page=page, per_page=per_page, api_key=api_key, fetch=_post)

    print(f"Apollo organizations/search pagination probe\n  filters: {filters}\n"
          f"  per_page={args.per_page}, walking up to {args.max_pages} page(s)\n")

    report = walk_pages(search_fn, per_page=args.per_page, max_pages=args.max_pages)

    for p in report["pages"]:
        print(f"  page {p['page']}: {p['returned']} returned "
              f"(honored per_page={p['honored_per_page']}), "
              f"{p['with_domain']} with domain, {p['new_domains']} new")

    te, tp = report["total_entries"], report["total_pages"]
    print(f"\n  total_entries (matched): {te}")
    print(f"  total_pages (at this per_page): {tp}")
    print(f"  unique domains collected: {report['unique_domains']}")
    print(f"  walk stopped: {report['stopped']}")

    rate = _rate_headers(captured)
    print(f"  rate-limit headers: {rate if rate else '(none surfaced)'}")

    # One deep-page request to test the ceiling cheaply, rather than walking to it.
    if args.deep_page:
        print(f"\n  deep-page ceiling check (page {args.deep_page}):")
        try:
            resp = search_fn(args.deep_page, args.per_page)
            summ = apollo.search_page_summary(resp)
            verdict = ("PAST the ceiling returns rows" if summ["returned"]
                       else "empty at/after the ceiling")
            print(f"    → {summ['returned']} returned "
                  f"(pagination={summ['pagination']}) — {verdict}")
        except apollo.ApolloRateLimitError as exc:
            print(f"    → rate limited (retry_after={exc.retry_after}s)")
        except apollo.ApolloPlanError:
            print("    → plan-gated")
        except urllib.error.HTTPError as exc:
            print(f"    → HTTP {exc.code} {exc.reason} "
                  f"({'ceiling enforced' if exc.code in (422, 400) else 'other'})")

    # The retrievable estimate — the number a sourcing design should budget against.
    honored = next((p["honored_per_page"] for p in report["pages"] if p["honored_per_page"]),
                   args.per_page)
    if te is not None and honored:
        ceiling_pages = tp if tp is not None else "?"
        print("\n  RETRIEVABLE ≈ min(total_entries, honored_per_page × reachable_pages)")
        print(f"    honored_per_page={honored}, total_pages={ceiling_pages}, "
              f"total_entries={te}")
        print("    (reachable_pages is bounded by the deep-page result and the rate "
              "limit above, not just total_pages)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keyword", default="professional training & coaching",
                        help="q_organization_keyword_tags value (the ICP industry).")
    parser.add_argument("--employees", default="50,200",
                        help="organization_num_employees_ranges value ('min,max').")
    parser.add_argument("--location", default=None,
                        help="organization_locations value (optional).")
    parser.add_argument("--per-page", type=int, default=100,
                        help="Records per page to request (default 100, the doc max).")
    parser.add_argument("--max-pages", type=int, default=3,
                        help="Pages to walk (kept small — the probe spends the quota "
                             "it measures).")
    parser.add_argument("--deep-page", type=int, default=501,
                        help="A single deep page to test the depth ceiling (0 to skip).")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
