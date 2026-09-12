"""Data model, persistence, and pure helper functions for the Timesheets app.

Mirrors the state shape from the original design prototype: timesheet data is
keyed by ISO Monday-of-week, each week holding a list of rows (a project or a
non-project category) with per-day hours, a draft/submitted status, and an
optional note. A small set of fake "team" records lets the My Team tab work
without a real multi-user backend.
"""

from __future__ import annotations

import copy
import html
import json
import os
import uuid
from datetime import date, datetime, timedelta

DATA_FILE = os.path.join(os.path.dirname(__file__), "timesheet_data.json")

DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
DAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Design palette (converted from the handoff's OKLCH tokens to sRGB hex).
COLOR = {
    "border": "#e4e0dd",
    "text": "#231e1b",
    "text_secondary": "#69625d",
    "text_muted": "#76706c",
    "accent": "#b92d7e",
    "accent_hover": "#a00b69",
    "accent_tint": "#ffe5f2",
    "accent_tint_text": "#7d1453",
    "success_tint": "#d6f0ff",
    "success_text": "#004f82",
    "danger": "#ac1922",
    "danger_tint": "#ffe2de",
    "progress_tint": "#e7f0f8",
    "progress_text": "#495766",
    "disabled_bg": "#f5f3f1",
    "page_bg": "#fcf9f7",
    "white": "#ffffff",
    "warn_bg": "#faf1dc",
    "warn_text": "#614100",
    "row_hover": "#fdfbfa",
    "divider": "#ebe7e4",
    "category_bar": "#aca490",
}
AVATAR_TINTS = [
    ("#d3def6", "#1f3c86"),
    ("#c4e6e5", "#005558"),
    ("#cee5d2", "#005416"),
    ("#f3d7cb", "#762100"),
]

STATUS_META = {
    "draft": {"label": "Draft", "bg": "#f0ede9", "fg": "#4c453f"},
    "submitted": {"label": "Submitted", "bg": COLOR["accent_tint"], "fg": COLOR["accent_tint_text"]},
}

PROJECTS = [
    {"id": "p1", "name": "Website Redesign"},
    {"id": "p2", "name": "Client Onboarding Platform"},
    {"id": "p3", "name": "Mobile App – iOS"},
    {"id": "p4", "name": "Data Migration"},
    {"id": "p5", "name": "Internal Tools"},
    {"id": "p6", "name": "Q3 Marketing Campaign"},
    {"id": "p7", "name": "Platform Reliability"},
]
DEFAULT_CATEGORIES = [
    {"id": "c1", "name": "Annual Leave / PTO"},
    {"id": "c2", "name": "Public Holiday"},
    {"id": "c3", "name": "Sick Leave"},
    {"id": "c4", "name": "Training"},
    {"id": "c5", "name": "Internal / Admin Time"},
]
STORAGE_PROVIDERS = [
    {"key": "azureTable", "label": "Azure Table Storage", "target_label": "Table name",
     "target_placeholder": "e.g. TimesheetEntries"},
    {"key": "sharepoint", "label": "SharePoint list", "target_label": "Site URL and list name",
     "target_placeholder": "https://contoso.sharepoint.com/sites/Ops – Timesheets"},
    {"key": "sqlDatabase", "label": "SQL database", "target_label": "Connection string",
     "target_placeholder": "server, database, or connection string"},
]
PERIOD_OPTIONS = ["4 weeks", "8 weeks", "12 weeks", "All time"]
PERIOD_KEYS = {"4 weeks": 4, "8 weeks": 8, "12 weeks": 12, "All time": None}
SCOPE_OPTIONS = ["All entries", "Projects only", "Non-project only"]


def esc(text: str) -> str:
    """HTML-escape user-entered text before it goes into a markdown/HTML span."""
    return html.escape(str(text), quote=True)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def empty_hours() -> dict:
    return {k: 0.0 for k in DAY_KEYS}


def iso(d: date) -> str:
    return d.isoformat()


def monday_of(d: date) -> str:
    return iso(d - timedelta(days=d.weekday()))


def today_monday() -> str:
    return monday_of(date.today())


def shift_date(iso_date: str, days: int) -> str:
    d = date.fromisoformat(iso_date)
    return iso(d + timedelta(days=days))


def fmt_hours(n: float) -> str:
    n = float(n or 0)
    return str(int(n)) if n % 1 == 0 else f"{n:.1f}"


def initials_of(name: str) -> str:
    parts = [w for w in name.split(" ") if w]
    return "".join(w[0] for w in parts[:2]).upper()


def format_submitted_at(iso_dt: str | None) -> str:
    if not iso_dt:
        return "Not submitted yet"
    dt = datetime.fromisoformat(iso_dt)
    return dt.strftime("%a, %b %-d · %-I:%M %p")


def week_range_label(week_start: str) -> str:
    start = date.fromisoformat(week_start)
    end = start + timedelta(days=6)
    if start.month == end.month:
        return f"{start.strftime('%b')} {start.day}–{end.day}, {end.year}"
    return f"{start.strftime('%b %-d')} – {end.strftime('%b %-d')}, {end.year}"


def default_working_week() -> dict:
    wk = {k: {"active": True, "hours": 8.0} for k in DAY_KEYS[:5]}
    wk["sat"] = {"active": False, "hours": 0.0}
    wk["sun"] = {"active": False, "hours": 0.0}
    return wk


def day_meta(week_start: str, working_week: dict) -> list[dict]:
    start = date.fromisoformat(week_start)
    out = []
    for i, key in enumerate(DAY_KEYS):
        d = start + timedelta(days=i)
        wd = working_week[key]
        out.append({
            "key": key,
            "label": DAY_ABBR[i],
            "date_label": d.strftime("%b %-d"),
            "active": wd["active"],
            "target": wd["hours"] if wd["active"] else 0.0,
        })
    return out


def seed_state() -> dict:
    """The demo data a brand-new install starts with, matching the design's seed."""
    tm = today_monday()
    prev = shift_date(tm, -7)

    def week(rows, status, submitted_at=None):
        return {"rows": rows, "status": status, "submitted_at": submitted_at, "note": ""}

    def row(kind, ref_id, name, hours):
        h = empty_hours()
        h.update(hours)
        return {"id": new_id("row"), "kind": kind, "ref_id": ref_id, "name": name, "hours": h}

    weeks = {
        prev: week(
            [
                row("project", "p1", "Website Redesign", {"mon": 5, "tue": 4, "wed": 4, "thu": 4, "fri": 4}),
                row("project", "p2", "Client Onboarding Platform", {"mon": 3, "tue": 4, "wed": 4, "thu": 4, "fri": 4}),
            ],
            "submitted", prev + "T17:32:00",
        ),
        tm: week(
            [
                row("project", "p1", "Website Redesign", {"mon": 4, "tue": 5, "wed": 3, "thu": 4, "fri": 2}),
                row("project", "p2", "Client Onboarding Platform", {"mon": 3, "tue": 2, "wed": 4, "thu": 3}),
                row("category", "c5", "Internal / Admin Time", {"mon": 1, "tue": 1, "wed": 1, "thu": 1, "fri": 1}),
            ],
            "draft",
        ),
    }

    def member(name, project_id, project_name, prev_hours, this_hours, this_status, this_submitted):
        return {
            "id": new_id("member"),
            "name": name,
            "weeks": {
                prev: week([row("project", project_id, project_name, prev_hours)], "submitted", prev + "T16:00:00"),
                tm: week([row("project", project_id, project_name, this_hours)], this_status, this_submitted),
            },
        }

    team = [
        member("Alice Chen", "p3", "Mobile App – iOS",
               {"mon": 8, "tue": 8, "wed": 8, "thu": 8, "fri": 8},
               {"mon": 8, "tue": 8, "wed": 8, "thu": 8, "fri": 8}, "submitted", tm + "T16:10:00"),
        member("Ben Ortiz", "p4", "Data Migration",
               {"mon": 7, "tue": 7, "wed": 7, "thu": 7, "fri": 7},
               {"mon": 6, "tue": 6, "wed": 5, "thu": 5}, "draft", None),
        member("Priya Nair", "p6", "Q3 Marketing Campaign",
               {"mon": 8, "tue": 8, "wed": 8, "thu": 8, "fri": 8},
               {"mon": 8, "tue": 8, "wed": 8, "thu": 8, "fri": 8}, "submitted", tm + "T09:05:00"),
        member("Sam Whitfield", "p7", "Platform Reliability",
               {"mon": 8, "tue": 8, "wed": 8, "thu": 8, "fri": 8},
               {"mon": 8, "tue": 6, "wed": 8, "thu": 8, "fri": 6}, "draft", None),
    ]

    return {
        "working_week": default_working_week(),
        "enabled_projects": {p["id"]: True for p in PROJECTS},
        "categories": copy.deepcopy(DEFAULT_CATEGORIES),
        "weeks": weeks,
        "team": team,
        "connections": {
            "ado_org_url": "",
            "ado_project_name": "",
            "ado_connected": False,
            "storage_provider": "azureTable",
            "storage_target": "",
        },
    }


def load_persisted() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return seed_state()


def persist(data: dict) -> None:
    """Save everything except the (session-only) PAT, which we never write to disk."""
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def get_week(data: dict, week_start: str) -> dict:
    if week_start not in data["weeks"]:
        data["weeks"][week_start] = {"rows": [], "status": "draft", "submitted_at": None, "note": ""}
    return data["weeks"][week_start]


def get_member_week(member: dict, week_start: str) -> dict:
    return member.get("weeks", {}).get(week_start, {"rows": [], "status": "draft", "submitted_at": None, "note": ""})


def week_total(week: dict) -> float:
    return sum(sum(r["hours"].values()) for r in week["rows"])


def week_target(days: list[dict]) -> float:
    return sum(d["target"] for d in days)


def row_total(row: dict) -> float:
    return sum(row["hours"].values())


def build_breakdown(weeks_iter, scope: str) -> tuple[list[dict], float]:
    """Aggregate hours by row name across an iterable of week dicts."""
    by_name: dict[str, dict] = {}
    total = 0.0
    for wk in weeks_iter:
        for r in wk["rows"]:
            if scope == "Projects only" and r["kind"] != "project":
                continue
            if scope == "Non-project only" and r["kind"] != "category":
                continue
            rt = row_total(r)
            if rt <= 0:
                continue
            total += rt
            entry = by_name.setdefault(r["name"], {"name": r["name"], "kind": r["kind"], "hours": 0.0})
            entry["hours"] += rt
    items = sorted(by_name.values(), key=lambda x: -x["hours"])
    for item in items:
        item["pct"] = round(item["hours"] / total * 100) if total > 0 else 0
    return items, total


def period_week_starts(period_label: str, data: dict, include_team: bool = False) -> list[str]:
    tm = today_monday()
    n = PERIOD_KEYS[period_label]
    if n is None:
        keys = set(data["weeks"].keys())
        if include_team:
            for m in data["team"]:
                keys.update(m.get("weeks", {}).keys())
        keys.add(tm)
        return sorted(keys)
    return [shift_date(tm, -7 * i) for i in range(n)]
