"""Create the master OKR document in Notion (`PRD-notion-integration.md`, N2).

Idempotent and non-destructive. On each run it:

1. finds the shared root page ("AFC Richmond Chief-of-Staff");
2. finds or creates the "OKRs — Master" page under it;
3. finds or creates the Objectives, Key Results, and Initiatives databases, in
   that order, because each links to the one before it;
4. renames the reverse side of each two-way relation ("Key Results" on
   Objectives, "Initiatives" on Key Results);
   4b. finds or creates the To-Do database (N3) under the root page, beside the
   master page, with a one-way relation to Initiatives and a Key Result rollup;
   4c. finds or creates the dashboard's KR Scorecard, Initiative Status, and Weekly
   Snapshots databases (N4) under the root page, with one-way relations into the
   master databases, and then the current quarter's dashboard page
   (`dashboard_page.ensure_page`);
5. records the page and database ids in `notion_sync` (via `record`);
6. with `seed`, enters the Q4 2026 objectives and KRs, only if Objectives is empty;
   with `resume_seed`, creates only the seed rows whose names are missing, to
   finish a seed that failed partway.

It never rewrites a database that already exists: the operator owns them (N-S1).
Missing properties and select options are reported, not fixed. The Notion client and the recorder
are injected, so the whole flow unit-tests without network or database.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from agents._lib.notion_client import NotionError, title_of
from agents.notion import dashboard_schema as dash
from agents.notion import okr_schema as s
from agents.notion import todo_schema as todo

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
    resume_seed: bool = False,
    dashboard: Callable[..., None] | None = None,
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
            missing = s.missing_properties(name, props) + s.missing_options(name, props)
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

    # 4b. the To-Do database (N3), under the root page beside the master page
    _todo_database(client, r, root, source_ids.get(s.INITIATIVES), dry_run)

    # 4c. the dashboard databases (N4); the page needs their ids, so it comes after
    # the ids are recorded (step 5)
    dash_sources = dashboard_databases(client, r, root, source_ids.get(s.KEY_RESULTS),
                                       source_ids.get(s.INITIATIVES), dry_run)

    # 5. record ids
    if not dry_run:
        for key, notion_id in r.ids.items():
            record(key, notion_id)
        r.say(f"OK    recorded {len(r.ids)} ids in notion_sync ({SYNC_TABLE})")

    if dashboard is not None:
        dashboard(client, r, root, dash_sources, dry_run)

    # 6. optional seed
    if seed or resume_seed:
        _seed(client, r, source_ids, dry_run, resume=resume_seed)

    r.say("TODO  by hand in Notion (the API can't set relation limits):")
    for step in MANUAL_STEPS:
        r.say(f"      - {step}")
    return r


def _todo_database(client: Any, r: SetupResult, root: str, initiatives_ds: str | None,
                   dry_run: bool) -> None:
    """Find or create the To-Do database (N3). Like the master databases, an
    existing one is never edited: a missing column is reported as drift."""
    name = todo.DATABASE_TITLE
    existing = _children(client, root, "child_database").get(name)
    if existing:
        r.ids[todo.SCHEMA_KEY] = existing
        ds_id = _data_source_id(client.retrieve_database(existing))
        props = client.retrieve_data_source(ds_id).get("properties") or {}
        missing = [p for p in todo.REQUIRED if p not in props]
        if missing:
            r.drift[name] = missing
            r.say(f"DRIFT {name}: missing {', '.join(missing)} (not changed)")
        else:
            r.say(f"OK    found database {name!r} ({existing})")
        return
    if dry_run or initiatives_ds is None:
        r.say(f"PLAN  create database {name!r} with {len(todo.REQUIRED)} properties")
        return
    created = client.create_database(root, name, todo.properties(initiatives_ds),
                                     description=todo.DESCRIPTION)
    r.ids[todo.SCHEMA_KEY] = created["id"]
    r.say(f"NEW   created database {name!r} ({created['id']})")
    try:
        client.update_data_source(_data_source_id(created), todo.ROLLUP)
        r.say(f"NEW   added the Key Result rollup to {name!r}")
    except NotionError as e:
        r.say(f"TODO  add the Key Result rollup to {name!r} by hand: Rollup → relation "
              f"Initiative → property Key Result → Show original ({e})")


def dashboard_databases(client: Any, r: SetupResult, root: str, key_results_ds: str | None,
                        initiatives_ds: str | None, dry_run: bool) -> dict[str, str]:
    """Find or create the three dashboard databases (N4). Returns name → data
    source id for the ones that exist. Like To-Do, an existing one is never edited."""
    existing = _children(client, root, "child_database")
    sources: dict[str, str] = {}
    targets = {dash.SCORECARD: key_results_ds, dash.INITIATIVE_STATUS: initiatives_ds,
               dash.SNAPSHOTS: "none needed"}
    for name in dash.DATABASES:
        if name in existing:
            r.ids[dash.SCHEMA_KEYS[name]] = existing[name]
            ds_id = _data_source_id(client.retrieve_database(existing[name]))
            sources[name] = ds_id
            props = client.retrieve_data_source(ds_id).get("properties") or {}
            missing = [p for p in dash.required(name) if p not in props]
            if missing:
                r.drift[name] = missing
                r.say(f"DRIFT {name}: missing {', '.join(missing)} (not changed)")
            else:
                r.say(f"OK    found database {name!r} ({existing[name]})")
            continue
        if dry_run or targets[name] is None:
            r.say(f"PLAN  create database {name!r} with {len(dash.required(name))} properties")
            continue
        if name == dash.SCORECARD:
            props, rollups = dash.scorecard_properties(key_results_ds), dash.SCORECARD_ROLLUPS
        elif name == dash.INITIATIVE_STATUS:
            props, rollups = dash.initiative_status_properties(initiatives_ds), \
                dash.INITIATIVE_ROLLUPS
        else:
            props, rollups = dash.snapshot_properties(), {}
        created = client.create_database(root, name, props, description=dash.DESCRIPTIONS[name])
        r.ids[dash.SCHEMA_KEYS[name]] = created["id"]
        sources[name] = _data_source_id(created)
        r.say(f"NEW   created database {name!r} ({created['id']})")
        for prop, spec in rollups.items():
            try:
                client.update_data_source(sources[name], {prop: spec})
                r.say(f"NEW   added the {prop} rollup to {name!r}")
            except NotionError as e:
                rel = spec["rollup"]["relation_property_name"]
                r.say(f"TODO  add the {prop} rollup to {name!r} by hand: Rollup → relation "
                      f"{rel} → property {prop} → Show original ({e})")
    return sources


def _seed(client: Any, r: SetupResult, source_ids: dict[str, str], dry_run: bool,
          *, resume: bool = False) -> None:
    n_krs = sum(len(o["krs"]) for o in s.Q4_SEED)
    obj_ds, kr_ds = source_ids.get(s.OBJECTIVES), source_ids.get(s.KEY_RESULTS)
    if not obj_ds or not kr_ds:
        r.say(f"PLAN  seed {len(s.Q4_SEED)} objectives and {n_krs} KRs once the databases exist")
        return
    # Title → page id of what's already there. Without `resume`, any row stops the
    # seed: names the operator has since edited can't be matched safely.
    have_obj = {title_of(p): p["id"] for p in client.iter_query(obj_ds)}
    if have_obj and not resume:
        r.say("SKIP  seed: Objectives already has rows (seeding runs only into an empty "
              "database). To finish a seed that failed partway, use --resume-seed.")
        return
    have_kr = {title_of(p) for p in client.iter_query(kr_ds)} if resume else set()
    todo = [(o, [k for k in o["krs"] if k[0] not in have_kr]) for o in s.Q4_SEED]
    new_obj = sum(1 for o, _ in todo if o["objective"] not in have_obj)
    new_kr = sum(len(krs) for _, krs in todo)
    if not new_obj and not new_kr:
        r.say("OK    seed: every seed objective and KR is already present")
        return
    if dry_run:
        r.say(f"PLAN  seed {new_obj} objectives and {new_kr} KRs")
        return
    for obj, krs in todo:
        obj_id = have_obj.get(obj["objective"]) or client.create_page(
            {"type": "data_source_id", "data_source_id": obj_ds},
            s.objective_row(obj["objective"], obj["order"]),
        )["id"]
        for name, metric, target, unit, direction in krs:
            client.create_page(
                {"type": "data_source_id", "data_source_id": kr_ds},
                s.kr_row(name, obj_id, metric, target, unit, direction),
            )
    r.say(f"NEW   seeded {new_obj} objectives and {new_kr} KRs (blank targets: "
          "Cold sequences live, LLM spend per month — set them in Notion)")
