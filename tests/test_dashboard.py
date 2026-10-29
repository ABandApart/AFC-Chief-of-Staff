"""Unit tests for the N4 dashboard: schema, page build, and push (no network, no DB)."""

from __future__ import annotations

import itertools
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from agents._lib import kr_metrics as km
from agents.notion import dashboard_page as dp
from agents.notion import dashboard_push as push
from agents.notion import dashboard_schema as dash

D = Decimal
TZ = timezone(timedelta(hours=-4))
SOURCES = {dash.SCORECARD: "ds-sc", dash.INITIATIVE_STATUS: "ds-ini", dash.SNAPSHOTS: "ds-snap"}


def _result(i, metric, current, target, status, score=None, progress=None, name=None):
    return km.Result(i, name or f"KR {i}", metric, current, target,
                     km.Score(status, score, progress))


# --- schema (pure) -----------------------------------------------------------------------


def test_page_title_and_header():
    assert dash.page_title("2026-Q4") == "Q4 2026 Dashboard"
    text = dash.header_text("2026-Q4", date(2026, 11, 2), date(2026, 11, 2))
    assert "day 32 of 92" in text and "Oct 1 – Dec 31" in text


def test_attention_is_counts_only_and_skips_zeros():
    text = dash.attention_text({"discoveries_to_review": 12, "missed_touches": 0,
                                "failing_agents": 2})
    assert "12 sourced companies await review" in text
    assert "touches" not in text and "2 agents failed" in text
    assert "1 agent failed" in dash.attention_text({"failing_agents": 1})
    assert dash.attention_text({}).endswith("Nothing this week.")


def test_scorecard_row_leaves_progress_blank_for_lower_is_better():
    r = _result(3, "llm_spend_month", D("36.01"), D(60), km.ON_TRACK, 1.5, None)
    row = dash.scorecard_row(r, kr_page_id="kr-3", quarter="2026-Q4",
                             direction=km.LOWER, updated=date(2026, 11, 2))
    assert row["Progress"] == {"number": None}
    assert row["Status"] == {"select": {"name": "🟢 On track"}}
    assert row["Key Result"] == {"relation": [{"id": "kr-3"}]}
    assert row["Quarter"]["rich_text"][0]["text"]["content"] == "2026-Q4"
    assert row["Note"]["rich_text"][0]["text"]["content"] == "Lower is better"


def test_notes_explain_the_gray_statuses():
    def note(metric, status):
        return dash.note_for(_result(1, metric, None, None, status), km.HIGHER)
    assert note("manual", km.NO_DATA).startswith("Enter the value")
    assert note("proposals_sent", km.SET_TARGET).startswith("Set the target")
    assert note("proposals_sent", km.TOO_EARLY).startswith("Early in the window")


def test_snapshot_row_has_one_column_per_metric_and_no_manual():
    row = dash.snapshot_row(date(2026, 10, 26), "2026-Q4",
                            {"agent_run_success_rate": D("79.06")})
    assert row["Agent run success %"] == {"number": 79.06}
    assert row["Proposals sent"] == {"number": None}
    assert "manual" not in dash.SNAPSHOT_COLUMNS
    assert set(dash.SNAPSHOT_COLUMNS) == set(km.REGISTRY) - {"manual"}


def test_tables_are_filtered_to_the_quarter_and_charts_span_every_week():
    ids = dp._placeholder_ids()
    specs = dash.views("2026-Q4", ids)
    assert [s["block"] for s in specs].count("trends") == len(dash.CHARTS)
    for s in specs:
        if s["type"] == "table":
            assert "2026-Q4" in str(s["filter"]), s["name"]
        else:
            assert "filter" not in s, s["name"]
    chart = next(s for s in specs if s["type"] == "chart")
    assert chart["configuration"]["chart_type"] == "line"
    assert chart["configuration"]["x_axis_property_id"] == "Week start"


def test_stat_callouts_are_four_with_no_company_names():
    results = [_result(16, "engagements_signed", D(0), D(3), km.OFF_TRACK, 0.0, 0.0),
               _result(4, "agent_run_success_rate", D("79.06"), D(95), km.OFF_TRACK, 0.83)]
    stats = dash.stat_callouts(results, {"open_todos": 5})
    assert [e for e, _ in stats] == ["🤝", "🔎", "✅", "⚙️"]
    assert "Engagements signed: 0 / 3" in stats[0][1]
    assert "Agent runs OK (7 days): 79%" in stats[3][1]
    assert "Open to-dos: 5" in stats[2][1]


# --- the page (fake Notion) --------------------------------------------------------------


class FakeNotion:
    def __init__(self, *, drop_filter=False, fail_on_view=None):
        self._ids = (f"id-{n}" for n in itertools.count(1))
        self.writes: list[tuple] = []
        self.views: dict[str, dict] = {}
        self.drop_filter, self.fail_on_view = drop_filter, fail_on_view
        self.pages: dict[str, dict] = {}
        self.rows: dict[str, list[dict]] = {}

    def retrieve_data_source(self, ds):
        names = ("Quarter", "Status", "Week start", *dash.SNAPSHOT_COLUMNS.values())
        return {"properties": {n: {"id": f"{ds}:{n}"} for n in names}}

    def create_page(self, parent, properties):
        pid = next(self._ids)
        self.writes.append(("create_page", parent, properties))
        if "data_source_id" in parent:
            self.rows.setdefault(parent["data_source_id"], []).append({"id": pid})
        return {"id": pid}

    def update_page(self, page_id, properties):
        self.writes.append(("update_page", page_id, properties))

    def iter_query(self, ds, *, filter=None):
        return iter(self.rows.get(ds, []))

    def append_blocks(self, page_id, children):
        self.writes.append(("append_blocks", page_id, len(children)))
        return [{"id": next(self._ids)} for _ in children]

    def update_block(self, block_id, body):
        self.writes.append(("update_block", block_id, body))

    def create_view(self, body):
        if body["name"] == self.fail_on_view:
            raise RuntimeError("boom")
        vid = next(self._ids)
        db = body.get("database_id") or f"db-{vid}"
        saved = dict(body, filter=None if self.drop_filter else body.get("filter"))
        self.views[vid] = saved
        self.writes.append(("create_view", body))
        return {"id": vid, "parent": {"type": "database_id", "database_id": db}}

    def retrieve_view(self, vid):
        return self.views[vid]


def _ensure(fake, store, **kw):
    lines = []
    ids = dp.ensure_page(fake, root="root", quarter="2026-Q4", sources=SOURCES,
                         lookup=store.get, record=store.__setitem__, say=lines.append, **kw)
    return ids, lines


def test_page_build_creates_blocks_and_views_once():
    fake, store = FakeNotion(), {}
    ids, lines = _ensure(fake, store)
    n_views = len(dash.views("2026-Q4", dp._placeholder_ids()))
    views = [w for w in fake.writes if w[0] == "create_view"]
    assert len(views) == n_views
    # The first view under each heading makes the linked block; the rest reuse it.
    made = [w[1] for w in views if "create_database" in w[1]]
    assert len(made) == 3
    assert made[0]["create_database"]["position"] == {"type": "after_block",
                                                      "block_id": ids["scorecard"]}
    assert set(dp.STATS) <= set(ids) and "attention" in ids
    before = len(fake.writes)
    _ensure(fake, store)
    assert len(fake.writes) == before  # a re-run builds nothing


def test_a_failed_view_resumes_without_a_second_page():
    fake, store = FakeNotion(fail_on_view="Initiatives"), {}
    try:
        _ensure(fake, store)
    except RuntimeError:
        pass
    fake.fail_on_view = None
    _ensure(fake, store)
    assert sum(1 for w in fake.writes if w[0] == "create_page") == 1
    names = [w[1]["name"] for w in fake.writes if w[0] == "create_view"]
    assert len(names) == len(set(names)) == len(dash.views("2026-Q4", dp._placeholder_ids()))


def test_a_view_saved_without_its_filter_is_reported():
    _, lines = _ensure(FakeNotion(drop_filter=True), {})
    assert any(line.startswith("WARN") and "without its filter" in line for line in lines)


def test_dry_run_writes_nothing():
    fake, store = FakeNotion(), {}
    _, lines = _ensure(fake, store, dry_run=True)
    assert fake.writes == [] and store == {}
    assert any(line.startswith("PLAN  create page 'Q4 2026 Dashboard'") for line in lines)


# --- the push ----------------------------------------------------------------------------


def test_week_of_returns_the_week_just_ended():
    week_start, as_of = push.week_of(datetime(2026, 11, 2, 7, 0, tzinfo=TZ))
    assert week_start == date(2026, 10, 26)
    assert as_of.date() == date(2026, 11, 2) and as_of.hour == 0
    assert push.week_of(datetime(2026, 11, 4, 9, tzinfo=TZ))[0] == date(2026, 10, 26)


class _SyncDB:
    """notion_sync as a dict, behind the two statements the push runs."""

    def __init__(self):
        self.rows: dict[tuple[str, str], tuple[str, str]] = {}

    @contextmanager
    def connection(self):
        yield self

    def execute(self, sql, params):
        db = self

        class _R:
            def fetchall(self):
                return [(e, p, h) for (t, e), (p, h) in db.rows.items() if t == params[0]]

        if sql.lstrip().startswith("INSERT"):
            table, entity, page, digest = params
            self.rows[(table, entity)] = (page, digest)
        return _R()


def test_push_rows_skips_unchanged_rows_on_a_rerun():
    fake, sync = FakeNotion(), _SyncDB()
    rows = [("2026-Q4:1", {"Current": {"number": 1}}), ("2026-Q4:2", {"Current": {"number": 2}})]
    first = push.push_rows(fake, sync.connection, dash.SCORECARD, "ds-sc", rows)
    assert first == {"created": 2, "updated": 0, "skipped": 0}
    again = push.push_rows(fake, sync.connection, dash.SCORECARD, "ds-sc", rows)
    assert again == {"created": 0, "updated": 0, "skipped": 2}  # verification 3
    rows[0] = ("2026-Q4:1", {"Current": {"number": 5}})
    assert push.push_rows(fake, sync.connection, dash.SCORECARD, "ds-sc", rows)["updated"] == 1


def test_push_rows_recreates_a_row_deleted_in_notion():
    fake, sync = FakeNotion(), _SyncDB()
    rows = [("2026-Q4:1", {"Current": {"number": 1}})]
    push.push_rows(fake, sync.connection, dash.SCORECARD, "ds-sc", rows)
    fake.rows["ds-sc"] = []
    assert push.push_rows(fake, sync.connection, dash.SCORECARD, "ds-sc", rows)["created"] == 1


def test_push_blocks_updates_only_changed_blocks():
    fake, sync = FakeNotion(), _SyncDB()
    bodies = {"stat1": ("callout", {"callout": {"rich_text": "a"}}),
              "attention": ("callout", {"callout": {"rich_text": "b"}})}
    ids = {"stat1": "blk-1", "attention": "blk-2"}
    assert push.push_blocks(fake, sync.connection, "2026-Q4", ids, bodies) == 2
    assert push.push_blocks(fake, sync.connection, "2026-Q4", ids, bodies) == 0
    bodies["stat1"] = ("callout", {"callout": {"rich_text": "c"}})
    assert push.push_blocks(fake, sync.connection, "2026-Q4", ids, bodies) == 1


def _week(results, attention=None):
    return push.Week("2026-Q4", date(2026, 10, 26), datetime(2026, 11, 2, tzinfo=TZ), [],
                     results, [], attention or {})


def test_summary_is_five_lines_with_the_worst_kr_and_the_link():
    results = [_result(16, "engagements_signed", D(0), D(3), km.OFF_TRACK, 0.0),
               _result(13, "cold_sequences_live", D(11), D(150), km.OFF_TRACK, 0.07,
                       name="Cold sequences live"),
               _result(4, "agent_run_success_rate", D("79.06"), D(95), km.OFF_TRACK, 0.83),
               _result(3, "llm_spend_month", D("36.01"), D(60), km.ON_TRACK, 1.5)]
    text = push.summary(_week(results, {"missed_touches": 20, "cards_pending": 0}),
                        "abc-def")
    lines = text.split("\n")
    assert len(lines) == 5
    assert lines[2].startswith("Worst KR: KR 16")  # score 0.0 is lower than 0.07
    assert lines[3] == "Attention: 20 missed touches"
    assert "runs OK 79%" in lines[4] and "$36.01" in lines[4]
    assert lines[4].endswith("https://www.notion.so/abcdef")


def test_summary_with_nothing_wrong():
    text = push.summary(_week([_result(3, "llm_spend_month", D(1), D(60), km.ON_TRACK, 9)]),
                        None)
    assert "Worst KR: none off track or at risk" in text and "Attention: nothing" in text


# --- backfill ----------------------------------------------------------------------------


def test_backfill_weeks_run_from_july_27_to_the_last_ended_week():
    from cli import notion_backfill_snapshots as bf
    w = bf.weeks(date(2026, 10, 29))  # a Thursday
    assert w[0] == date(2026, 7, 27) and w[-1] == date(2026, 10, 19)
    assert bf.weeks(date(2026, 11, 2))[-1] == date(2026, 10, 26)  # a Monday
    assert all(d.weekday() == 0 for d in w)
