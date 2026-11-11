"""Read a plan page from Notion into `plans` and `plan_items` (`PRD-notion-integration.md` N7).

The caller is `cli.notion_plan_import`. The page must follow the import contract in
the PRD (N7, "Plan page format"):

- a paragraph line `Window: Mon Nov 16 to Sun Dec 13, 2026`, which sets the year;
- one H3 per plan day that starts with its date (`Mon Nov 16 (240 min: …)`). An H3
  that contains `OFF` has no items;
- under each day H3, a table whose header starts `Task | Min | Initiative`; its last
  column is the context;
- under the H2 that starts `Every weekday`, the standing-block table. Its rows go on
  every plan day, ahead of that day's own rows.

A task that starts with 🔻 is marked `cut_first`, and the marker is dropped. Each
Initiative cell must name one active initiative, exactly or by a unique prefix.
A day heading's minutes, as in `(240 min`, must equal the sum of that day's rows.
Any problem fails the whole import and lists every one, so nothing half-imports.

Parsing is pure (`parse`); `read_blocks` and `save` do the I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

CUT_MARK = "🔻"
_MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_WINDOW = re.compile(r"Window:\s*(?:\w{3}\s+)?(\w{3})\s+(\d{1,2})\s+to\s+(?:\w{3}\s+)?"
                     r"(\w{3})\s+(\d{1,2}),\s*(\d{4})")
_DAY = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+(\w{3})\s+(\d{1,2})\b")
_HEADING_MIN = re.compile(r"\((\d[\d,]*)\s*min\b")


@dataclass
class Item:
    title: str
    minutes: int
    initiative_id: int | None
    context: str | None
    standing: bool = False
    cut_first: bool = False


@dataclass
class Plan:
    title: str
    starts_on: date | None = None
    ends_on: date | None = None
    days: dict[date, list[Item]] = field(default_factory=dict)  # OFF days are absent
    off_days: list[date] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def minutes(self) -> int:
        return sum(i.minutes for items in self.days.values() for i in items)


# --- reading the page ----------------------------------------------------------------


def _text(rich: list[dict[str, Any]]) -> str:
    return "".join(t.get("plain_text") or (t.get("text") or {}).get("content", "")
                   for t in rich or [])


def read_blocks(client: Any, page_id: str) -> list[tuple[str, Any]]:
    """The page's top-level blocks as (kind, value): ("h2"|"h3"|"p", text) or
    ("table", rows of cell strings). Other block types are dropped."""
    out: list[tuple[str, Any]] = []
    for b in client.iter_block_children(page_id):
        kind = b.get("type")
        if kind in ("heading_1", "heading_2"):
            out.append(("h2", _text(b[kind].get("rich_text"))))
        elif kind == "heading_3":
            out.append(("h3", _text(b[kind].get("rich_text"))))
        elif kind == "paragraph":
            out.append(("p", _text(b[kind].get("rich_text"))))
        elif kind == "table":
            rows = [[_text(cell) for cell in (r.get("table_row") or {}).get("cells", [])]
                    for r in client.iter_block_children(b["id"])
                    if r.get("type") == "table_row"]
            out.append(("table", rows))
    return out


# --- parsing (pure) --------------------------------------------------------------------


def match_initiative(cell: str, initiatives: dict[str, int]) -> int | None | str:
    """The initiative id a cell names: an exact name, ignoring case, or else a unique
    prefix. Returns an error string when it names none or several."""
    want = cell.strip().lower()
    if not want:
        return "no initiative"
    exact = [i for n, i in initiatives.items() if n.lower() == want]
    if exact:
        return exact[0]
    hits = [n for n in initiatives if n.lower().startswith(want)]
    if len(hits) == 1:
        return initiatives[hits[0]]
    if not hits:
        return f"initiative {cell.strip()!r} matches no active initiative"
    return f"initiative {cell.strip()!r} matches {len(hits)}: {', '.join(sorted(hits))}"


def _window(text: str) -> tuple[date, date] | None:
    m = _WINDOW.search(text)
    if not m or m.group(1) not in _MONTHS or m.group(3) not in _MONTHS:
        return None
    m1, d1, m2, d2, year = (_MONTHS[m.group(1)], int(m.group(2)), _MONTHS[m.group(3)],
                            int(m.group(4)), int(m.group(5)))
    return date(year - (1 if m1 > m2 else 0), m1, d1), date(year, m2, d2)


def _day(text: str, window: tuple[date, date]) -> date | str | None:
    """The date an H3 names, or an error string, or None if it isn't a day heading."""
    m = _DAY.match(text.strip())
    if not m:
        return None
    if m.group(2) not in _MONTHS:
        return f"heading {text!r}: unknown month {m.group(2)!r}"
    start, end = window
    for year in (start.year, end.year):
        try:
            d = date(year, _MONTHS[m.group(2)], int(m.group(3)))
        except ValueError:
            continue
        if start <= d <= end:
            if _DAYS[d.weekday()] != m.group(1):
                return f"heading {text!r}: {d.isoformat()} is a {_DAYS[d.weekday()]}"
            return d
    return f"heading {text!r}: date is outside the plan window"


def _rows(table: list[list[str]], where: str, initiatives: dict[str, int], *,
          standing: bool, errors: list[str]) -> list[Item]:
    if not table:
        errors.append(f"{where}: empty table")
        return []
    header = [c.strip().lower() for c in table[0]]
    if header[:3] != ["task", "min", "initiative"]:
        errors.append(f"{where}: table header must start Task | Min | Initiative "
                      f"(found {' | '.join(table[0][:3])})")
        return []
    items = []
    for n, row in enumerate(table[1:], 1):
        if not any(c.strip() for c in row):
            continue
        title = row[0].strip()
        cut = title.startswith(CUT_MARK)
        if cut:
            title = title[len(CUT_MARK):].strip()
        if not title:
            errors.append(f"{where}, row {n}: no task")
            continue
        try:
            minutes = int(row[1].replace(",", "").strip())
        except ValueError:
            errors.append(f"{where}, row {n} ({title[:40]!r}): Min {row[1]!r} is not a number")
            continue
        initiative = match_initiative(row[2], initiatives)
        if isinstance(initiative, str):
            errors.append(f"{where}, row {n} ({title[:40]!r}): {initiative}")
            continue
        context = row[-1].strip() if len(row) > 3 else ""
        items.append(Item(title, minutes, initiative, context or None, standing, cut))
    return items


def parse(title: str, blocks: list[tuple[str, Any]], initiatives: dict[str, int]) -> Plan:
    """Build the plan from the page's blocks. `initiatives` maps each active
    initiative's name to its id. Check `plan.errors` before using the result."""
    plan = Plan(title)
    window = next((w for kind, v in blocks if kind == "p" and (w := _window(v))), None)
    if window is None:
        plan.errors.append("no 'Window: Mon Nov 16 to Sun Dec 13, 2026' line")
        return plan
    plan.starts_on, plan.ends_on = window
    standing: list[Item] | None = None
    own: dict[date, list[Item]] = {}
    expected: dict[date, int] = {}
    section, day, off = "", None, False
    for kind, value in blocks:
        if kind == "h2":
            section, day = value.strip(), None
        elif kind == "h3":
            parsed = _day(value, window)
            if isinstance(parsed, str):
                plan.errors.append(parsed)
                day = None
            elif parsed is not None:
                day = parsed
                off = "OFF" in value
                if day in own or day in plan.off_days:
                    plan.errors.append(f"heading {value!r}: {day.isoformat()} appears twice")
                elif off:
                    plan.off_days.append(day)
                else:
                    own[day] = []
                    m = _HEADING_MIN.search(value)
                    if m:
                        expected[day] = int(m.group(1).replace(",", ""))
            else:
                day = None
        elif kind == "table":
            if day is not None and not off and not own[day]:
                before = len(plan.errors)
                own[day] = _rows(value, f"{day:%a %b} {day.day}", initiatives,
                                 standing=False, errors=plan.errors)
                if len(plan.errors) > before:
                    expected.pop(day, None)  # a bad row already explains the sum
            elif day is None and standing is None and section.lower().startswith("every weekday"):
                standing = _rows(value, "standing block", initiatives, standing=True,
                                 errors=plan.errors)
    if not own:
        plan.errors.append("no day headings with tasks")
    for d in sorted(own):
        plan.days[d] = [*(standing or []), *own[d]]
        got = sum(i.minutes for i in plan.days[d])
        if d in expected and got != expected[d]:
            plan.errors.append(f"{d:%a %b} {d.day}: rows add up to {got} min, "
                               f"the heading says {expected[d]}")
    return plan


# --- saving ------------------------------------------------------------------------------


def save(conn: Any, plan: Plan, notion_page_id: str, *, from_day: date | None = None) -> dict:
    """Write the plan in one transaction. Retires any other active plan. A day that
    already has a task, or falls before `from_day`, is left as it is; other days'
    items are replaced. Returns counts."""
    assert not plan.errors, "save() needs a plan without errors"
    counts = {"days": 0, "items": 0, "kept_days": 0, "skipped_days": 0}
    with conn.transaction():
        conn.execute("UPDATE plans SET status = 'retired' "
                     "WHERE status = 'active' AND notion_page_id <> %s", (notion_page_id,))
        plan_id = conn.execute(
            """
            INSERT INTO plans (notion_page_id, title, starts_on, ends_on)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (notion_page_id) DO UPDATE SET
                title = EXCLUDED.title, starts_on = EXCLUDED.starts_on,
                ends_on = EXCLUDED.ends_on, status = 'active', imported_at = now()
            RETURNING id
            """, (notion_page_id, plan.title, plan.starts_on, plan.ends_on)).fetchone()[0]
        started = {r[0] for r in conn.execute(
            "SELECT DISTINCT day FROM plan_items WHERE plan_id = %s AND task_id IS NOT NULL",
            (plan_id,)).fetchall()}
        conn.execute("DELETE FROM plan_items WHERE plan_id = %s AND task_id IS NULL "
                     "AND day <> ALL(%s)", (plan_id, list(started)))
        for d, items in sorted(plan.days.items()):
            if from_day and d < from_day:
                counts["skipped_days"] += 1
                continue
            if d in started:
                counts["kept_days"] += 1
                continue
            for pos, i in enumerate(items, 1):
                conn.execute(
                    """
                    INSERT INTO plan_items (plan_id, day, position, title, minutes,
                        okr_initiative_id, context, standing, cut_first)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (plan_id, d, pos, i.title, i.minutes, i.initiative_id, i.context,
                          i.standing, i.cut_first))
                counts["items"] += 1
            counts["days"] += 1
    return counts


def active_initiatives(conn: Any) -> dict[str, int]:
    """Name → id for the initiatives a plan row may name: Active, valid, not archived."""
    return dict(conn.execute(
        "SELECT name, id FROM okr_initiatives WHERE archived_at IS NULL "
        "AND problem IS NULL AND status = 'Active'").fetchall())


def report(plan: Plan) -> list[str]:
    """One line per day and a total, for the dry run."""
    lines = []
    for d, items in sorted(plan.days.items()):
        cut = sum(i.cut_first for i in items)
        lines.append(f"{d:%a %b} {d.day:>2}: {len(items):>2} tasks, "
                     f"{sum(i.minutes for i in items):>4} min"
                     + (f", {cut} marked {CUT_MARK}" if cut else ""))
    for d in plan.off_days:
        lines.append(f"{d:%a %b} {d.day:>2}: OFF")
    lines.append(f"Total: {len(plan.days)} days, "
                 f"{sum(len(v) for v in plan.days.values())} tasks, {plan.minutes:,} min")
    return lines
