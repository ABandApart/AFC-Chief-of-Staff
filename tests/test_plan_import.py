"""Tests for the plan page import (PRD-notion-integration.md N7, verification 1).

`PLAN_2026_11` copies the block structure of the operator's 2026-11-11 plan page:
the window line, the standing block, 19 day headings with their minutes, the
Thanksgiving OFF day, the 🔻 rows, and the tables that must be ignored. Titles
and context are shortened; minutes and initiatives are the page's own.
"""

from __future__ import annotations

from datetime import date

import pytest

from agents.notion import plan_import as pi
from cli.notion_plan_import import page_id

INITIATIVES = {
    "Outreach": 1,
    "December Fifty call sprint": 2,
    "Content Engine (LinkedIn posts, Dream 100, hand-raisers)": 3,
    "Lead Magnet Breakdown and Micro-Magnets": 4,
    "Convert: proposals and January starts": 5,
    "Nurture and Follow-through": 6,
}
CELL = {"DF": "December Fifty", "N": "Nurture and Follow-through", "O": "Outreach",
        "CE": "Content Engine", "LM": "Lead Magnet Breakdown and Micro-Magnets",
        "CV": "Convert: proposals and January starts"}
HEADER = ["Task", "Min", "Initiative", "KR", "Obj", "Context and done-when"]

# (heading, rows) per day, None = OFF. Each row is "<minutes> <initiative>", and a
# trailing ! marks a 🔻 row.
DAYS = [
    ('Mon Nov 16 (240 min: 60 standing + 180)',
     '45 CE, 45 DF, 30 DF, 60 DF'),
    ('Tue Nov 17 (240 min: 60 standing + 180)',
     '90 LM, 30 DF, 60 DF'),
    ('Wed Nov 18 (225 min: 60 standing + 165)',
     '60 CE, 30 DF, 60 DF, 15 N'),
    ('Thu Nov 19 (225 min: 60 standing + 165)',
     '60 LM, 90 DF, 15 N'),
    ('Fri Nov 20 (255 min: 60 standing + 195)',
     '60 LM, 90 DF, 30 DF, 15 N'),
    ('Mon Nov 23 (225 min: 60 standing + 165)',
     '15 CE, 60 O, 30 DF, 60 DF'),
    ('Tue Nov 24 (225 min: 60 standing + 165)',
     '60 N, 30 N, 60 DF, 15 N'),
    ('Wed Nov 25 (225 min: 60 standing + 165)',
     '15 CE, 45 LM, 45 O, 60 DF'),
    ('Thu Nov 26: OFF (US Thanksgiving)', None),
    ('Fri Nov 27 (245 min: 60 standing + 185)',
     '15 CE, 20 LM, 30 DF, 30 DF, 60 DF, 30 DF'),
    ('Mon Nov 30 (225 min: 60 standing + 165)',
     '15 CE, 45 O, 45 DF, 30 DF, 30 DF'),
    ('Tue Dec 1 (265 min: 60 standing + 205)',
     '15 O, 30 O, 30 LM!, 25 DF, 90 DF, 15 N'),
    ('Wed Dec 2 (270 min: 60 standing + 210)',
     '15 CE, 15 O, 30 LM, 25 DF, 20 DF, 90 DF, 15 N'),
    ('Thu Dec 3 (210 min: 60 standing + 150)',
     '30 LM, 15 O, 90 DF, 15 N'),
    ('Fri Dec 4 (220 min: 60 standing + 160)',
     '15 CE, 40 N!, 30 DF, 60 DF, 15 N'),
    ('Mon Dec 7 (255 min: 60 standing + 195)',
     '90 O, 15 DF, 30 N, 60 DF'),
    ('Tue Dec 8 (255 min: 60 standing + 195)',
     '90 CE!, 30 CV, 60 DF, 15 N'),
    ('Wed Dec 9 (240 min: 60 standing + 180)',
     '15 CE, 60 N!, 30 CV, 60 DF, 15 N'),
    ('Thu Dec 10 (255 min: 60 standing + 195)',
     '30 LM!, 30 O, 45 CE!, 15 N, 60 DF, 15 N'),
    ('Fri Dec 11 (270 min: 60 standing + 210)',
     '15 CE, 60 CE!, 30 CE, 30 DF, 60 DF, 15 N'),
]
WEEK_BEFORE = {"Mon Nov 16": "Week 1: Mon Nov 16 to Fri Nov 20 (about 1,185 min)",
               "Mon Nov 23": "Week 2: Mon Nov 23 to Fri Nov 27 (about 920 min, Thu off)",
               "Mon Nov 30": "Week 3: Mon Nov 30 to Fri Dec 4 (about 1,190 min)",
               "Mon Dec 7": "Week 4: Mon Dec 7 to Fri Dec 11 (about 1,275 min)"}


def _plan_blocks() -> list[tuple[str, object]]:
    blocks: list[tuple[str, object]] = [
        ("p", "Window: Mon Nov 16 to Sun Dec 13, 2026. Weekdays only; weekends off.\n"
              "Sources: Social Media Phases 1 to 4 and The December Fifty."),
        ("h2", "OKR alignment"),
        ("table", [["Initiative", "Key Result (Q4 target)", "Objective"],
                   ["Outreach (existing)", "Touches sent on schedule (90%)", "O2"]]),
        ("h2", "Every weekday: the standing block (60 min)"),
        ("table", [HEADER,
                   ["Warm connects: 20 with notes", "30", "December Fifty", "", "O2",
                    "Lane 4."],
                   ["Reply to every comment same day", "15", "Nurture and Follow-through", "",
                    "O4", ""],
                   ["Send today's due nudges", "15", "Outreach", "", "O2", "Log it."]]),
        ("h2", "Weekly targets at a glance"),
        ("table", [["Week", "Dates", "Theme"], ["1", "Nov 16 to 20", "Build"]]),
    ]
    for heading, rows in DAYS:
        week = WEEK_BEFORE.get(heading[:10].strip())
        if week:
            blocks.append(("h2", week))
            blocks.append(("p", "Goal: hold the calls."))
        blocks.append(("h3", heading))
        if rows is None:
            blocks.append(("p", "No tasks. Optional 10 minutes: check hand-raiser DMs only."))
            continue
        table = [HEADER]
        for n, row in enumerate(rows.split(", "), 1):
            minutes, ini = row.rstrip("!").split()
            title = f"{'🔻 ' if row.endswith('!') else ''}{heading[:10]} task {n}"
            table.append([title, minutes, CELL[ini], "KR", "O2", f"why {n}"])
        blocks.append(("table", table))
    blocks += [("h2", "The honest math"),
               ("table", [["Week", "Needed (minutes)"], ["Total", "4,570"]]),
               ("h2", "How the daily routine works"), ("p", "Each weekday at 6:00 AM…")]
    return blocks


@pytest.fixture
def plan():
    return pi.parse("4-Week Plan", _plan_blocks(), INITIATIVES)


def test_parses_the_whole_plan(plan):
    assert plan.errors == []
    assert (plan.starts_on, plan.ends_on) == (date(2026, 11, 16), date(2026, 12, 13))
    assert len(plan.days) == 19
    assert plan.minutes == 4570
    assert plan.off_days == [date(2026, 11, 26)]


def test_standing_block_leads_every_day(plan):
    for items in plan.days.values():
        assert [i.title for i in items[:3]] == ["Warm connects: 20 with notes",
                                                 "Reply to every comment same day",
                                                 "Send today's due nudges"]
        assert all(i.standing for i in items[:3]) and not any(i.standing for i in items[3:])


def test_cut_first_rows_are_marked_and_stripped(plan):
    cut = [i for items in plan.days.values() for i in items if i.cut_first]
    assert len(cut) == 7 and sum(i.minutes for i in cut) == 355  # the page's "355 minutes"
    assert not any(i.title.startswith(pi.CUT_MARK) for i in cut)


def test_initiatives_and_context(plan):
    mon = plan.days[date(2026, 11, 16)]
    assert mon[0].initiative_id == 2 and mon[0].context == "Lane 4."
    assert mon[1].context is None  # an empty context cell
    assert mon[3].initiative_id == 3  # "Content Engine" is a unique prefix
    assert plan.days[date(2026, 12, 8)][4].initiative_id == 5  # Convert, exact


def test_report_totals(plan):
    lines = pi.report(plan)
    assert lines[-1] == "Total: 19 days, 145 tasks, 4,570 min"
    assert "Thu Nov 26: OFF" in lines


@pytest.mark.parametrize("cell,expected", [
    ("Outreach", 1),
    ("outreach", 1),
    ("December Fifty", 2),
    ("Content Engine", 3),
    ("Sales", "initiative 'Sales' matches no active initiative"),
    ("", "no initiative"),
])
def test_match_initiative(cell, expected):
    assert pi.match_initiative(cell, INITIATIVES) == expected


def test_ambiguous_prefix_is_an_error():
    got = pi.match_initiative("Co", INITIATIVES)
    assert isinstance(got, str) and "matches 2" in got


def _mini(heading="Mon Nov 16 (40 min)", rows=(["A", "30", "Outreach", "x"],
                                              ["B", "10", "Outreach", "y"]),
          window="Window: Mon Nov 16 to Sun Dec 13, 2026"):
    return [("p", window), ("h2", "Week 1"), ("h3", heading),
            ("table", [["Task", "Min", "Initiative", "Context"], *rows])]


def test_heading_minutes_must_match():
    plan = pi.parse("p", _mini(heading="Mon Nov 16 (45 min)"), INITIATIVES)
    assert plan.errors == ["Mon Nov 16: rows add up to 40 min, the heading says 45"]


def test_weekday_must_match_the_date():
    plan = pi.parse("p", _mini(heading="Tue Nov 16 (40 min)"), INITIATIVES)
    assert "2026-11-16 is a Mon" in plan.errors[0]


def test_every_bad_row_is_listed():
    plan = pi.parse("p", _mini(rows=(["A", "x", "Outreach", ""], ["B", "10", "Nope", ""])),
                    INITIATIVES)
    assert len(plan.errors) == 2
    assert "Min 'x' is not a number" in plan.errors[0]
    assert "'Nope' matches no active initiative" in plan.errors[1]


def test_missing_window_and_bad_header():
    assert pi.parse("p", _mini(window="no window"), INITIATIVES).errors[0].startswith("no 'Window")
    bad = [("p", "Window: Mon Nov 16 to Sun Dec 13, 2026"), ("h3", "Mon Nov 16"),
           ("table", [["Item", "Minutes"], ["A", "1"]])]
    assert "header must start Task | Min | Initiative" in pi.parse("p", bad, INITIATIVES).errors[0]


def test_window_across_new_year():
    plan = pi.parse("p", _mini(heading="Mon Jan 4 (40 min)",
                               window="Window: Mon Dec 28 to Fri Jan 8, 2027"), INITIATIVES)
    assert plan.errors == [] and list(plan.days) == [date(2027, 1, 4)]
    assert plan.starts_on == date(2026, 12, 28)


def test_page_id_from_url_or_id():
    url = ("https://app.notion.com/p/415fd8b468ca81fa8fe8fa5a47d0c842?pvs=204")
    assert page_id(url) == "415fd8b4-68ca-81fa-8fe8-fa5a47d0c842"
    assert page_id("415fd8b4-68ca-81fa-8fe8-fa5a47d0c842") == "415fd8b4-68ca-81fa-8fe8-fa5a47d0c842"
    with pytest.raises(ValueError):
        page_id("not a page")


def test_read_blocks_flattens_tables():
    class Fake:
        def iter_block_children(self, bid):
            if bid == "page":
                h3 = {"rich_text": [{"plain_text": "Mon Nov 16"}]}
                return [{"type": "heading_3", "heading_3": h3},
                        {"type": "table", "id": "t1", "table": {}},
                        {"type": "divider"}]
            cells = [[{"plain_text": "Task"}], [{"plain_text": "Min"}]]
            return [{"type": "table_row", "table_row": {"cells": cells}}]
    assert pi.read_blocks(Fake(), "page") == [("h3", "Mon Nov 16"), ("table", [["Task", "Min"]])]
