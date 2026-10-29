"""Find or build one quarter's dashboard page (`PRD-notion-integration.md` N4).

`cli.notion_setup` builds the current quarter's page. The Monday push calls the
same function, so the first push of a new quarter builds that quarter's page
(rollover); the previous page stays and stops updating.

The page, from the top: a header line, 4 stat callouts, the "Needs your attention"
callout, then three headings, each followed by a linked database block holding its
views (`dashboard_schema.views`), all filtered to the page's quarter.

Each block and view id is recorded in `notion_sync` (entity_table
`dashboard_block`, entity_id `<quarter>:<key>`) as soon as it exists, so a run that
fails partway resumes where it stopped instead of building a second page.

Notion fixed a bug on 2026-08-13 where a view could save without its filter, so
each new view is read back and a missing filter is reported.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agents._lib.notion_client import rich_text
from agents.notion import dashboard_schema as dash

STATS = ("stat1", "stat2", "stat3", "stat4")
SECTIONS = (("scorecard", "🎯 KR Scorecard"), ("initiatives", "🧭 Initiatives"),
            ("trends", "📈 Trends"))


def property_ids(client: Any, sources: dict[str, str]) -> dict[str, dict[str, str]]:
    """Database name → {'data_source_id': …, property name → property id}."""
    out: dict[str, dict[str, str]] = {}
    for name, ds in sources.items():
        props = client.retrieve_data_source(ds).get("properties") or {}
        out[name] = {"data_source_id": ds, **{p: v.get("id", p) for p, v in props.items()}}
    return out


def ensure_page(
    client: Any,
    *,
    root: str,
    quarter: str,
    sources: dict[str, str],
    lookup: Callable[[str], str | None],
    record: Callable[[str, str], None],
    say: Callable[[str], None],
    dry_run: bool = False,
) -> dict[str, str]:
    """Build whatever of `quarter`'s page is missing. Returns block key → id.

    `lookup(key)` and `record(key, id)` read and write the `<quarter>:<key>` rows.
    """
    title = dash.page_title(quarter)
    keys = ["page", "header", *STATS, "attention", *(k for k, _ in SECTIONS)]
    ids = {k: v for k in keys if (v := lookup(k))}
    missing_dbs = [n for n in dash.DATABASES if n not in sources]
    if missing_dbs:
        say(f"PLAN  build page {title!r} once {', '.join(missing_dbs)} exist")
        return ids

    if "page" not in ids:
        if dry_run:
            say(f"PLAN  create page {title!r} with 6 callouts and "
                f"{len(dash.views(quarter, _placeholder_ids()))} views")
            return ids
        ids["page"] = client.create_page(
            {"page_id": root}, {"title": {"title": rich_text(title)}})["id"]
        record("page", ids["page"])
        say(f"NEW   created page {title!r} ({ids['page']})")
    else:
        say(f"OK    found page {title!r} ({ids['page']})")

    blocks = [k for k in keys[1:] if k not in ids]
    if blocks and not dry_run:
        if blocks != keys[1:]:
            raise RuntimeError(f"page {title!r} is half-built (missing blocks {blocks}); "
                               "delete the page and its notion_sync rows, then re-run")
        children = [dash.paragraph(f"Quarter {quarter} · the first Monday push fills this page.")]
        children += [dash.callout("⏳", "Waiting for the first Monday push.") for _ in STATS]
        children.append(dash.callout("⚠️", "Needs your attention\nWaiting for the first push."))
        children += [dash.heading(label) for _, label in SECTIONS]
        made = client.append_blocks(ids["page"], children)
        for key, block in zip(keys[1:], made, strict=True):
            ids[key] = block["id"]
            record(key, block["id"])
        say(f"NEW   added the header, 5 callouts, and 3 headings to {title!r}")

    _ensure_views(client, quarter=quarter, sources=sources, ids=ids, lookup=lookup,
                  record=record, say=say, dry_run=dry_run)
    return ids


def _placeholder_ids() -> dict[str, dict[str, str]]:
    names = ("data_source_id", "Quarter", "Status", "Week start",
             *dash.SNAPSHOT_COLUMNS.values())
    return {n: {k: k for k in names} for n in dash.DATABASES}


def _ensure_views(client: Any, *, quarter: str, sources: dict[str, str], ids: dict[str, str],
                  lookup: Callable[[str], str | None], record: Callable[[str, str], None],
                  say: Callable[[str], None], dry_run: bool) -> None:
    specs = dash.views(quarter, property_ids(client, sources))
    made = 0
    for spec in specs:
        key = f"view:{spec['name']}"
        if lookup(key):
            continue
        if dry_run or spec["block"] not in ids:
            say(f"PLAN  add view {spec['name']!r}")
            continue
        body = {k: v for k, v in spec.items() if k != "block"}
        db_key = f"db:{spec['block']}"
        database_id = lookup(db_key)
        if database_id:
            body["database_id"] = database_id
        else:
            body["create_database"] = {
                "parent": {"type": "page_id", "page_id": ids["page"]},
                "position": {"type": "after_block", "block_id": ids[spec["block"]]},
                "show_data_source_title": False,
            }
        view = client.create_view(body)
        if not database_id:
            database_id = (view.get("parent") or {}).get("database_id")
            if database_id:
                record(db_key, database_id)
        record(key, view["id"])
        made += 1
        saved = client.retrieve_view(view["id"])
        if spec.get("filter") and not saved.get("filter"):
            say(f"WARN  view {spec['name']!r} saved without its filter; set "
                f"Quarter = {quarter} on it by hand in Notion")
    if made:
        say(f"NEW   added {made} view(s) to {dash.page_title(quarter)!r}")


# --- notion_sync access (the CLI and the push pass these in) --------------------------


def recorder(connection: Any, quarter: str) -> tuple[Callable[[str], str | None],
                                                      Callable[[str, str], None]]:
    """`lookup` and `record` over notion_sync rows `dashboard_block` / `<quarter>:<key>`."""
    def lookup(key: str) -> str | None:
        with connection() as conn:
            row = conn.execute(
                "SELECT notion_page_id FROM notion_sync WHERE entity_table = %s "
                "AND entity_id = %s", (dash.SYNC_BLOCKS, f"{quarter}:{key}")).fetchone()
        return row[0] if row else None

    def record(key: str, notion_id: str) -> None:
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO notion_sync (entity_table, entity_id, notion_page_id, last_pushed_at)
                VALUES (%s, %s, %s, now())
                ON CONFLICT (entity_table, entity_id)
                DO UPDATE SET notion_page_id = EXCLUDED.notion_page_id, last_pushed_at = now()
                """,
                (dash.SYNC_BLOCKS, f"{quarter}:{key}", notion_id))
    return lookup, record
