"""Create the master OKR document in Notion (`PRD-notion-integration.md`, N2).

Idempotent and non-destructive. On each run it:

1. finds the shared root page ("AFC Richmond Chief-of-Staff");
2. finds or creates the "OKRs — Master" page under it;
3. finds or creates the Objectives, Key Results, and Initiatives databases, in
   that order, because each links to the one before it;
4. renames the reverse side of each two-way relation ("Key Results" on
   Objectives, "Initiatives" on Key Results);
5. records the page and database ids in `notion_sync` (via `record`);
6. with `seed`, enters the Q4 2026 objectives and KRs, only if Objectives is empty.

It never rewrites a database that already exists: the operator owns them (N-S1).
Missing properties are reported, not fixed. The Notion client and the recorder
are injected, so the whole flow unit-tests without network or database.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agents._lib.notion_client import title_of
from agents.notion import okr_schema as s

ROOT_PAGE_TITLE = "AFC Richmond Chief-of-Staff"
SYNC_TABLE = "notion_schema"  # notion_sync.entity_table for setup-created objects

# Notion's API cannot set a relation's "limit to 1 page"; these are done by hand.
MANUAL_STEPS = (
    "In Key Results, open the Objective property → Limit → 1 page.",
    "In Initiatives, open the Key Result property → Limit → 1 page.",
)


class SetupError(Exception):
    """Setup cannot proceed (root page missing or ambiguous)."""


@dataclass
class SetupResult:
    lines: list[str] = field(default_factory=list)
    ids: dict[str, str] = field(default_factory=dict)  # key → Notion page/database id
    drift: dict[str, list[str]] = field(default_factory=dict)  # database → missing props

    def say(self, line: str) -> None:
        self.lines.append(line)


def _find_root(client: Any, title: str) -> str:
    matches = [p for p in client.iter_search(query=title, object_type="page")
               if title_of(p) == title]
    if not matches:
        raise SetupError(f"page {title!r} is not shared with the integration")
    if len(matches) > 1:
        raise SetupError(f"{len(matches)} pages are titled {title!r}; pass --root-id")
    return matches[0]["id"]


def _children(client: Any, page_id: str, block_type: str) -> dict[str, str]:
    """Title → id for child pages or child databases directly under a page."""
    out: dict[str, str] = {}
    for block in client.iter_block_children(page_id):
        if block.get("type") == block_type:
            out[(block.get(block_type) or {}).get("title", "")] = block["id"]
    return out


def _data_source_id(database: dict[str, Any]) -> str:
    sources = database.get("data_sources") or []
    if not sources:
        raise SetupError(f"database {database.get('id')} has no data source")
    return sources[0]["id"]


def run_setup(
    client: Any,
    *,
    record: Callable[[str, str], None],
    root_id: str | None = None,
    dry_run: bool = False,
    seed: bool = False,
) -> SetupResult:
    r = SetupResult()
    root = root_id or _find_root(client, ROOT_PAGE_TITLE)
    r.say(f"OK    root page {ROOT_PAGE_TITLE!r} ({root})")

    # 2. master page
    master = _children(client, root, "child_page").get(s.MASTER_PAGE_TITLE)
    if master:
        r.say(f"OK    found page {s.MASTER_PAGE_TITLE!r} ({master})")
    elif dry_run:
        r.say(f"PLAN  create page {s.MASTER_PAGE_TITLE!r}")
    else:
        master = client.create_page(
            {"page_id": root},
            {"title": {"title": [{"type": "text", "text": {"content": s.MASTER_PAGE_TITLE}}]}},
        )["id"]
        r.say(f"NEW   created page {s.MASTER_PAGE_TITLE!r} ({master})")
    if master:
        r.ids["okr_master_page"] = master

    # 3. databases, in link order
    existing = _children(client, master, "child_database") if master else {}
    source_ids: dict[str, str] = {}
    previous: str | None = None
    for name in s.DATABASE_ORDER:
        related = source_ids.get(previous) if previous else None
        if name in existing:
            db_id = existing[name]
            ds_id = _data_source_id(client.retrieve_database(db_id))
            props = client.retrieve_data_source(ds_id).get("properties") or {}
            missing = s.missing_properties(name, props)
            if missing:
                r.drift[name] = missing
                r.say(f"DRIFT {name}: missing {', '.join(missing)} (not changed — you own it)")
            else:
                r.say(f"OK    found database {name!r} ({db_id})")
        elif dry_run or (previous and related is None):
            n_props = len(s.properties_for(name, "x"))
            r.say(f"PLAN  create database {name!r} with {n_props} properties")
            db_id = ds_id = None
        else:
            created = client.create_database(
                master, name, s.properties_for(name, related), description=s.DESCRIPTIONS[name]
            )
            db_id, ds_id = created["id"], _data_source_id(created)
            r.say(f"NEW   created database {name!r} ({db_id})")
        if db_id:
            r.ids[f"okr_{name.lower().replace(' ', '_')}"] = db_id
        if ds_id:
            source_ids[name] = ds_id
        previous = name

    # 4. readable names for the reverse side of each relation
    for name, target in ((s.KEY_RESULTS, s.OBJECTIVES), (s.INITIATIVES, s.KEY_RESULTS)):
        if name not in source_ids or target not in source_ids:
            continue
        props = client.retrieve_data_source(source_ids[target]).get("properties") or {}
        patch = s.back_relation_rename(props, source_ids[name], s.BACK_RELATION_NAMES[name])
        if patch and not dry_run:
            client.update_data_source(source_ids[target], patch)
            r.say(f"FIX   renamed the {target} → {name} back-relation to "
                  f"{s.BACK_RELATION_NAMES[name]!r}")
        elif patch:
            r.say(f"PLAN  rename the {target} → {name} back-relation to "
                  f"{s.BACK_RELATION_NAMES[name]!r}")

    # 5. record ids
    if not dry_run:
        for key, notion_id in r.ids.items():
            record(key, notion_id)
        r.say(f"OK    recorded {len(r.ids)} ids in notion_sync ({SYNC_TABLE})")

    # 6. optional seed
    if seed:
        _seed(client, r, source_ids, dry_run)

    r.say("TODO  by hand in Notion (the API can't set relation limits):")
    for step in MANUAL_STEPS:
        r.say(f"      - {step}")
    return r


def _seed(client: Any, r: SetupResult, source_ids: dict[str, str], dry_run: bool) -> None:
    n_krs = sum(len(o["krs"]) for o in s.Q4_SEED)
    obj_ds, kr_ds = source_ids.get(s.OBJECTIVES), source_ids.get(s.KEY_RESULTS)
    if not obj_ds or not kr_ds:
        r.say(f"PLAN  seed {len(s.Q4_SEED)} objectives and {n_krs} KRs once the databases exist")
        return
    if next(iter(client.iter_query(obj_ds)), None) is not None:
        r.say("SKIP  seed: Objectives already has rows (seeding runs only into an empty database)")
        return
    if dry_run:
        r.say(f"PLAN  seed {len(s.Q4_SEED)} objectives and {n_krs} KRs")
        return
    for obj in s.Q4_SEED:
        obj_id = client.create_page(
            {"type": "data_source_id", "data_source_id": obj_ds},
            s.objective_row(obj["objective"], obj["order"]),
        )["id"]
        for name, metric, target, unit, direction in obj["krs"]:
            client.create_page(
                {"type": "data_source_id", "data_source_id": kr_ds},
                s.kr_row(name, obj_id, metric, target, unit, direction),
            )
    r.say(f"NEW   seeded {len(s.Q4_SEED)} objectives and {n_krs} KRs (blank targets: "
          "Cold sequences live, LLM spend per month — set them in Notion)")
