"""Tests for the master OKR document setup (pure — a fake Notion, no network, no DB)."""

from __future__ import annotations

import itertools

import pytest

from agents.notion import okr_schema as s
from agents.notion import setup as st

ROOT = "root-page"


class FakeNotion:
    """Just enough of Notion for setup: pages, child blocks, databases with one data
    source each, auto-named reverse relations, and rows."""

    def __init__(self, root_title=st.ROOT_PAGE_TITLE, extra_roots=0):
        self._ids = (f"id-{n}" for n in itertools.count(1))
        self.children: dict[str, list[dict]] = {ROOT: []}
        self.pages = [{"object": "page", "id": ROOT, "properties": {
            "title": {"type": "title", "title": [{"plain_text": root_title}]}}}]
        for i in range(extra_roots):
            self.pages.append({"object": "page", "id": f"dup-{i}", "properties": {
                "title": {"type": "title", "title": [{"plain_text": root_title}]}}})
        self.databases: dict[str, dict] = {}
        self.sources: dict[str, dict] = {}  # ds id → {"properties": {...}}
        self.rows: dict[str, list[dict]] = {}
        self.writes: list[tuple] = []

    # --- client surface used by setup ---------------------------------------------
    def iter_search(self, *, query="", object_type=None):
        return iter(self.pages)

    def iter_block_children(self, block_id):
        return iter(self.children.get(block_id, []))

    def create_page(self, parent, properties):
        pid = next(self._ids)
        self.writes.append(("create_page", parent, properties))
        if "page_id" in parent:
            title = properties["title"]["title"][0]["text"]["content"]
            self.children.setdefault(parent["page_id"], []).append(
                {"type": "child_page", "id": pid, "child_page": {"title": title}})
            self.children[pid] = []
        else:
            self.rows.setdefault(parent["data_source_id"], []).append(
                {"id": pid, "properties": properties})
        return {"id": pid}

    def create_database(self, parent_page_id, title, properties, *, description=""):
        db_id, ds_id = next(self._ids), next(self._ids)
        self.writes.append(("create_database", title, properties))
        props = {name: {"type": next(iter(cfg)), **cfg} for name, cfg in properties.items()}
        self.sources[ds_id] = {"properties": props}
        self.databases[db_id] = {"id": db_id, "data_sources": [{"id": ds_id}]}
        self.children[parent_page_id].append(
            {"type": "child_database", "id": db_id, "child_database": {"title": title}})
        # Notion auto-creates the reverse side of a dual relation with its own name.
        for cfg in properties.values():
            rel = cfg.get("relation")
            if rel and rel["type"] == "dual_property":
                self.sources[rel["data_source_id"]]["properties"][f"Related to {title}"] = {
                    "type": "relation", "relation": {"data_source_id": ds_id}}
        return {"id": db_id, "data_sources": [{"id": ds_id}]}

    def retrieve_database(self, database_id):
        return self.databases[database_id]

    def retrieve_data_source(self, data_source_id):
        return self.sources[data_source_id]

    def update_data_source(self, data_source_id, properties):
        self.writes.append(("update_data_source", data_source_id, properties))
        props = self.sources[data_source_id]["properties"]
        for old, change in properties.items():
            props[change["name"]] = props.pop(old)

    def iter_query(self, data_source_id, *, filter=None):
        return iter(self.rows.get(data_source_id, []))

    # --- helpers -----------------------------------------------------------------
    def ds_of(self, title):
        master = next(b["id"] for b in self.children[ROOT] if b["type"] == "child_page")
        db_id = next(b["id"] for b in self.children[master]
                     if b["type"] == "child_database" and b["child_database"]["title"] == title)
        return self.databases[db_id]["data_sources"][0]["id"]


def _run(fake, **kw):
    recorded = {}
    result = st.run_setup(fake, record=lambda k, v: recorded.__setitem__(k, v), **kw)
    return result, recorded


# --- fresh workspace -----------------------------------------------------------------


def test_fresh_creates_master_page_and_three_linked_databases():
    fake = FakeNotion()
    result, recorded = _run(fake)
    created = [w[1] for w in fake.writes if w[0] == "create_database"]
    assert created == ["Objectives", "Key Results", "Initiatives"]
    kr_props = fake.sources[fake.ds_of("Key Results")]["properties"]
    init_props = fake.sources[fake.ds_of("Initiatives")]["properties"]
    assert kr_props["Objective"]["relation"]["data_source_id"] == fake.ds_of("Objectives")
    assert init_props["Key Result"]["relation"]["data_source_id"] == fake.ds_of("Key Results")
    assert set(recorded) == {"okr_master_page", "okr_objectives", "okr_key_results",
                             "okr_initiatives"}
    assert not result.drift


def test_fresh_renames_reverse_relations():
    fake = FakeNotion()
    _run(fake)
    assert "Key Results" in fake.sources[fake.ds_of("Objectives")]["properties"]
    assert "Initiatives" in fake.sources[fake.ds_of("Key Results")]["properties"]


def test_manual_relation_limit_steps_always_listed():
    result, _ = _run(FakeNotion())
    assert all(step in "\n".join(result.lines) for step in st.MANUAL_STEPS)


# --- re-runs, dry runs, drift --------------------------------------------------------


def test_rerun_creates_nothing():
    fake = FakeNotion()
    _run(fake)
    before = len(fake.writes)
    result, recorded = _run(fake)
    assert len(fake.writes) == before
    assert len(recorded) == 4
    assert not result.drift


def test_dry_run_writes_nothing_and_records_nothing():
    fake = FakeNotion()
    result, recorded = _run(fake, dry_run=True)
    assert fake.writes == []
    assert recorded == {}
    assert sum(line.startswith("PLAN") for line in result.lines) == 4  # page + 3 databases


def test_drift_is_reported_not_fixed():
    fake = FakeNotion()
    _run(fake)
    del fake.sources[fake.ds_of("Key Results")]["properties"]["Metric"]
    before = len(fake.writes)
    result, _ = _run(fake)
    assert result.drift == {"Key Results": ["Metric"]}
    assert len(fake.writes) == before


# --- root page -----------------------------------------------------------------------


def test_missing_root_fails():
    with pytest.raises(st.SetupError, match="not shared"):
        _run(FakeNotion(root_title="Something else"))


def test_ambiguous_root_fails_unless_id_given():
    with pytest.raises(st.SetupError, match="--root-id"):
        _run(FakeNotion(extra_roots=1))
    result, _ = _run(FakeNotion(extra_roots=1), root_id=ROOT)
    assert result.ids["okr_master_page"]


# --- seed ----------------------------------------------------------------------------


def test_seed_enters_q4_objectives_and_linked_krs():
    fake = FakeNotion()
    _run(fake, seed=True)
    objectives = fake.rows[fake.ds_of("Objectives")]
    krs = fake.rows[fake.ds_of("Key Results")]
    assert len(objectives) == len(s.Q4_SEED)
    assert len(krs) == sum(len(o["krs"]) for o in s.Q4_SEED)
    objective_ids = {o["id"] for o in objectives}
    assert all(k["properties"]["Objective"]["relation"][0]["id"] in objective_ids for k in krs)


def test_seed_skips_when_objectives_not_empty():
    fake = FakeNotion()
    _run(fake, seed=True)
    before = len(fake.writes)
    result, _ = _run(fake, seed=True)
    assert len(fake.writes) == before
    assert any(line.startswith("SKIP") for line in result.lines)


def test_seed_dry_run_writes_nothing():
    fake = FakeNotion()
    _run(fake, seed=True, dry_run=True)
    assert fake.writes == []


# --- schema --------------------------------------------------------------------------


def test_every_seed_metric_is_known():
    for obj in s.Q4_SEED:
        for _, metric, _, unit, direction in obj["krs"]:
            assert metric in s.METRICS
            assert unit in s.UNITS
            assert direction in s.DIRECTIONS


def test_metric_select_offers_exactly_the_catalog():
    options = s.properties_for(s.KEY_RESULTS, "ds")["Metric"]["select"]["options"]
    assert [o["name"] for o in options] == list(s.METRICS)
    assert "manual" in s.METRICS


def test_kr_row_rejects_unknown_metric():
    with pytest.raises(ValueError):
        s.kr_row("x", "obj", "typo_metric", 1, "count", "Higher is better")


def test_linked_databases_need_a_data_source():
    with pytest.raises(ValueError):
        s.properties_for(s.INITIATIVES)


def test_back_relation_rename_cases():
    props = {"Related to Key Results": {"type": "relation", "relation": {"data_source_id": "kr"}},
             "Name": {"type": "title"}}
    assert s.back_relation_rename(props, "kr", "Key Results") == {
        "Related to Key Results": {"name": "Key Results"}}
    done = {"Key Results": {"type": "relation", "relation": {"data_source_id": "kr"}}}
    assert s.back_relation_rename(done, "kr", "Key Results") is None
    assert s.back_relation_rename({}, "kr", "Key Results") is None
