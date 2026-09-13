"""Data model, persistence, and pure helper functions for the Timesheets app.

Mirrors the state shape from the original design prototype: timesheet data is
keyed by ISO Monday-of-week, each week holding a list of rows (a project or a
non-project category) with per-day hours, a draft/submitted/approved status,
and an optional note. A small set of fake "team" records lets the My Team tab
work without a real multi-user backend.
"""

from __future__ import annotations

import copy
import html
import json
import os
import random
import re
import socket
import uuid
from datetime import date, datetime, timedelta

DATA_FILE = os.path.join(os.path.dirname(__file__), "timesheet_data.json")

# Bump whenever seed_state()'s shape or content changes in a way that a locally
# persisted install should pick up (e.g. more/different synthetic history) --
# load_persisted() regenerates the whole seed when this doesn't match.
SEED_VERSION = 2

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
    "approved_tint": "#dcf3e3",
    "approved_text": "#0a6631",
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
    "approved": {"label": "Approved", "bg": COLOR["approved_tint"], "fg": COLOR["approved_text"]},
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
    {
        "key": "azureTable",
        "label": "Azure Table Storage",
        "target_label": "Table name",
        "target_placeholder": "e.g. TimesheetEntries",
        "secret_label": "Connection string",
        "secret_placeholder": "DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net",
        "guide": [
            "Azure Portal → your Storage account → **Access keys** → copy the **Connection string** for key1.",
            "Paste it into the Connection string field above (kept only for this session, never saved to disk).",
            "The table name above is created automatically the first time data is written if it doesn't exist yet.",
            "**Live mode** actually lists the tables in that storage account to confirm the credentials work.",
        ],
    },
    {
        "key": "sharepoint",
        "label": "SharePoint list",
        "target_label": "Site URL and list name",
        "target_placeholder": "https://contoso.sharepoint.com/sites/Ops – Timesheets",
        "secret_label": None,
        "secret_placeholder": None,
        "guide": [
            "Enter the site URL and list name together, e.g. `https://contoso.sharepoint.com/sites/Ops – Timesheets`.",
            "Full read/write access needs an **Azure AD app registration** with `Sites.ReadWrite.All` consented by an admin — SharePoint Online no longer accepts simpler auth.",
            "**Live mode** only checks that the site URL responds over HTTPS; it can't validate the list or credentials without that app registration.",
        ],
    },
    {
        "key": "sqlDatabase",
        "label": "SQL database",
        "target_label": "Connection string",
        "target_placeholder": "server, database, or connection string",
        "secret_label": None,
        "secret_placeholder": None,
        "guide": [
            "Paste a connection string containing a server/host (and optional port), e.g. `Server=tcp:myserver.database.windows.net,1433;Database=Timesheets;...`.",
            "**Live mode** opens a plain TCP connection to that host and port to confirm the database server is reachable on the network.",
            "It does not validate credentials or run a query — that needs the matching driver (e.g. ODBC Driver 18 for SQL Server) installed on the host running this app.",
        ],
    },
]
PERIOD_OPTIONS = ["4 weeks", "8 weeks", "12 weeks", "All time"]
PERIOD_KEYS = {"4 weeks": 4, "8 weeks": 8, "12 weeks": 12, "All time": None}
SCOPE_OPTIONS = ["All entries", "Projects only", "Non-project only"]

# Fallback lists used only in Demo mode or before a Live-mode fetch has run --
# once connected, the actual picker options come from the connected project itself.
ADO_STATE_PRESETS = ["New", "Active", "Resolved", "Closed", "Removed", "In Progress", "Done", "Proposed"]
ADO_DEMO_WORK_ITEM_TYPES = ["Epic", "Feature", "User Story", "Task", "Bug", "Issue"]
ADO_DEFAULT_WORK_ITEM_TYPE = "Epic"
ADO_DEFAULT_STATES = ["New", "Active"]


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


def _empty_week(status: str = "draft") -> dict:
    return {"rows": [], "status": status, "submitted_at": None, "approved_at": None, "note": ""}


def _row(kind: str, ref_id: str, name: str, hours: dict) -> dict:
    h = empty_hours()
    h.update(hours)
    return {"id": new_id("row"), "kind": kind, "ref_id": ref_id, "name": name, "hours": h}


def _status_for_offset(i: int) -> str:
    """A repeatable pattern of statuses across 12 weeks of synthetic history:
    the current week is in-progress, one older week was simply missed, most
    are submitted, and the oldest few have already been through manager
    approval -- giving every period/scope filter combination something to show.
    """
    if i == 0:
        return "draft"
    if i == 2:
        return "draft"  # a missed week: nothing logged, never submitted
    if i >= 8:
        return "approved"
    return "submitted"


def _split_hours(rng: random.Random, total: float, n: int) -> list[float]:
    """Split `total` hours across `n` shares, rounded to the nearest half hour."""
    if n <= 0:
        return []
    if n == 1:
        return [round(total * 2) / 2]
    weights = [rng.uniform(0.6, 1.4) for _ in range(n)]
    w_sum = sum(weights)
    return [round(total * w / w_sum * 2) / 2 for w in weights]


def _gen_history_week(rng: random.Random, week_start: str, offset: int, projects: list[dict],
                       categories: list[dict], working_week: dict) -> dict:
    """A day-total-driven generator: each active day gets a realistic total
    close to that day's working-week target, which is then split across the
    week's projects (and an occasional leave/admin category) -- so combined
    weekly hours stay in the right ballpark instead of stacking a near-full
    day onto every project independently.
    """
    status = _status_for_offset(offset)
    if status == "draft" and offset == 2:
        return _empty_week("draft")

    active_days = [k for k in DAY_KEYS if working_week[k]["active"]]
    n_projects = 1 if len(projects) == 1 else rng.choice([1, 1, 2])
    chosen_projects = rng.sample(projects, k=min(n_projects, len(projects)))
    category = rng.choice(categories) if categories and rng.random() < 0.3 else None

    project_hours = {p["id"]: empty_hours() for p in chosen_projects}
    category_hours = empty_hours() if category else None

    for k in active_days:
        day_target = working_week[k]["hours"] if working_week[k]["active"] else 7.5
        day_total = max(0.0, round((day_target + rng.uniform(-1.5, 1.0)) * 2) / 2)
        cat_share = 0.0
        if category is not None and rng.random() < 0.5:
            cat_share = min(day_total, round(rng.uniform(0.5, 2.0) * 2) / 2)
        for p, share in zip(chosen_projects, _split_hours(rng, max(0.0, day_total - cat_share), len(chosen_projects))):
            project_hours[p["id"]][k] = share
        if category is not None:
            category_hours[k] = cat_share

    rows = [_row("project", p["id"], p["name"], project_hours[p["id"]]) for p in chosen_projects]
    if category is not None:
        rows.append(_row("category", category["id"], category["name"], category_hours))

    submitted_at = approved_at = None
    if status in ("submitted", "approved"):
        submitted_at = shift_date(week_start, 4) + f"T{16 + rng.randint(0, 2):02d}:{rng.randint(0, 59):02d}:00"
    if status == "approved":
        approved_at = shift_date(week_start, 5) + f"T{9 + rng.randint(0, 3):02d}:{rng.randint(0, 59):02d}:00"
    return {"rows": rows, "status": status, "submitted_at": submitted_at, "approved_at": approved_at, "note": ""}


def seed_state() -> dict:
    """The demo data a brand-new install starts with: 12 weeks (the current
    in-progress week plus 11 weeks of synthetic history) for the signed-in
    user and for every team member, so every Period/Scope filter combination
    on the My History and My Team tabs has something real to show.
    """
    rng = random.Random(20260101)
    tm = today_monday()
    working_week = default_working_week()

    weeks = {
        tm: {
            "rows": [
                _row("project", "p1", "Website Redesign", {"mon": 4, "tue": 5, "wed": 3, "thu": 4, "fri": 2}),
                _row("project", "p2", "Client Onboarding Platform", {"mon": 3, "tue": 2, "wed": 4, "thu": 3}),
                _row("category", "c5", "Internal / Admin Time", {"mon": 1, "tue": 1, "wed": 1, "thu": 1, "fri": 1}),
            ],
            "status": "draft", "submitted_at": None, "approved_at": None, "note": "",
        },
    }
    for i in range(1, 12):
        ws = shift_date(tm, -7 * i)
        weeks[ws] = _gen_history_week(rng, ws, i, PROJECTS[:5], DEFAULT_CATEGORIES, working_week)

    def member_seed(name: str, project_id: str, project_name: str, this_hours: dict, this_status: str,
                     this_submitted: str | None) -> dict:
        member_weeks = {
            tm: {
                "rows": [_row("project", project_id, project_name, this_hours)],
                "status": this_status, "submitted_at": this_submitted, "approved_at": None, "note": "",
            },
        }
        member_projects = [p for p in PROJECTS if p["id"] == project_id] or PROJECTS[:1]
        for i in range(1, 12):
            ws = shift_date(tm, -7 * i)
            member_weeks[ws] = _gen_history_week(rng, ws, i, member_projects, DEFAULT_CATEGORIES, working_week)
        return {"id": new_id("member"), "name": name, "weeks": member_weeks}

    team = [
        member_seed("Alice Chen", "p3", "Mobile App – iOS",
                    {"mon": 8, "tue": 8, "wed": 8, "thu": 8, "fri": 8}, "submitted", tm + "T16:10:00"),
        member_seed("Ben Ortiz", "p4", "Data Migration",
                    {"mon": 6, "tue": 6, "wed": 5, "thu": 5}, "draft", None),
        member_seed("Priya Nair", "p6", "Q3 Marketing Campaign",
                    {"mon": 8, "tue": 8, "wed": 8, "thu": 8, "fri": 8}, "submitted", tm + "T09:05:00"),
        member_seed("Sam Whitfield", "p7", "Platform Reliability",
                    {"mon": 8, "tue": 6, "wed": 8, "thu": 8, "fri": 6}, "draft", None),
    ]

    return {
        "seed_version": SEED_VERSION,
        "working_week": working_week,
        "enabled_projects": {p["id"]: True for p in PROJECTS},
        "categories": copy.deepcopy(DEFAULT_CATEGORIES),
        "weeks": weeks,
        "team": team,
        "connections": {
            "mode": "demo",
            "ado_org_url": "",
            "ado_project_name": "",
            "ado_connected": False,
            "ado_work_item_type": ADO_DEFAULT_WORK_ITEM_TYPE,
            "ado_states": list(ADO_DEFAULT_STATES),
            "storage_provider": "azureTable",
            "storage_target": "",
        },
    }


def _backfill_fields(data: dict) -> dict:
    """Non-destructively add keys later versions of the app introduced, without
    discarding anything -- used for restoring a user's own uploaded backup,
    where silently replacing their data would be wrong even if it's old."""
    for wk in data.get("weeks", {}).values():
        wk.setdefault("approved_at", None)
    for m in data.get("team", []):
        for wk in m.get("weeks", {}).values():
            wk.setdefault("approved_at", None)
    data.setdefault("connections", {})
    data["connections"].setdefault("mode", "demo")
    data["connections"].setdefault("ado_work_item_type", ADO_DEFAULT_WORK_ITEM_TYPE)
    data["connections"].setdefault("ado_states", list(ADO_DEFAULT_STATES))
    data.setdefault("seed_version", SEED_VERSION)
    return data


def load_persisted() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
            if data.get("seed_version") != SEED_VERSION:
                # This local file is ephemeral demo data by design (see README) --
                # an install persisted before the current synthetic-history
                # generator existed would otherwise be stuck on its original
                # stale seed forever, since this is the only place seed_state()
                # normally gets called.
                return seed_state()
            return _backfill_fields(data)
        except (json.JSONDecodeError, OSError):
            pass
    return seed_state()


def persist(data: dict) -> None:
    """Save everything except the (session-only) secrets, which we never write to disk."""
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def get_week(data: dict, week_start: str) -> dict:
    if week_start not in data["weeks"]:
        data["weeks"][week_start] = _empty_week()
    return data["weeks"][week_start]


def get_member_week(member: dict, week_start: str) -> dict:
    member.setdefault("weeks", {})
    if week_start not in member["weeks"]:
        member["weeks"][week_start] = _empty_week()
    return member["weeks"][week_start]


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


# ---------------------------------------------------------------------------
# Approval workflow
# ---------------------------------------------------------------------------
def approve_week(week: dict) -> bool:
    """Manager action: lock a submitted week so it can no longer be edited."""
    if week["status"] != "submitted":
        return False
    week["status"] = "approved"
    week["approved_at"] = datetime.now().isoformat(timespec="seconds")
    return True


def release_week(week: dict) -> bool:
    """Manager action: undo an approval so the owner can edit the week again."""
    if week["status"] != "approved":
        return False
    week["status"] = "draft"
    week["approved_at"] = None
    return True


# ---------------------------------------------------------------------------
# Connections: Azure DevOps + storage provider live checks
# ---------------------------------------------------------------------------
def test_ado_connection(mode: str, org_url: str, project_name: str, pat: str) -> tuple[bool, str]:
    org_url = (org_url or "").strip()
    project_name = (project_name or "").strip()
    pat = pat or ""
    if not (org_url and project_name and pat.strip()):
        return False, "Fill in organization URL, project, and personal access token first."
    if mode != "live":
        return True, "Connected to Azure DevOps (demo mode — no real API call made)."
    return _test_ado_live(org_url, project_name, pat.strip())


def _test_ado_live(org_url: str, project_name: str, pat: str) -> tuple[bool, str]:
    import requests

    api_url = f"{org_url.rstrip('/')}/_apis/projects/{project_name}?api-version=7.1"
    try:
        # A PAT-authenticated REST call should never need to follow a redirect -- one
        # usually means the org doesn't exist and Azure DevOps is routing toward an
        # interactive sign-in page instead of the API, which is a failure either way.
        resp = requests.get(api_url, auth=("", pat), timeout=10, allow_redirects=False)
    except requests.RequestException as e:
        return False, f"Couldn't reach Azure DevOps: {e}"
    if resp.status_code == 200:
        try:
            proj = resp.json()
        except ValueError:
            proj = {}
        return True, f"Connected — found project \"{proj.get('name', project_name)}\" ({proj.get('state', 'unknown')} state)."
    if resp.status_code == 401:
        return False, "Authentication failed — check the personal access token. It needs at least 'Project and Team (Read)' scope."
    if resp.status_code == 404:
        return False, f"Organization reachable, but project \"{project_name}\" wasn't found there."
    if 300 <= resp.status_code < 400:
        return False, "Azure DevOps redirected the request instead of returning data — check the organization URL and project name."
    return False, f"Azure DevOps returned HTTP {resp.status_code}."


def fetch_ado_work_item_types(mode: str, org_url: str, project_name: str, pat: str) -> tuple[bool, str, list[str]]:
    """The work item TYPE picker should offer only types that actually exist in the
    connected project, not a guessed generic list -- this is that real lookup."""
    org_url = (org_url or "").strip()
    project_name = (project_name or "").strip()
    pat = pat or ""
    if not (org_url and project_name and pat.strip()):
        return False, "Fill in organization URL, project, and personal access token first.", []
    if mode != "live":
        return True, "Demo mode — showing common work item types, not your real project's.", list(ADO_DEMO_WORK_ITEM_TYPES)
    return _fetch_ado_work_item_types_live(org_url, project_name, pat.strip())


def _fetch_ado_work_item_types_live(org_url: str, project_name: str, pat: str) -> tuple[bool, str, list[str]]:
    import requests

    url = f"{org_url.rstrip('/')}/{project_name}/_apis/wit/workitemtypes?api-version=7.1"
    try:
        resp = requests.get(url, auth=("", pat), timeout=15, allow_redirects=False)
    except requests.RequestException as e:
        return False, f"Couldn't reach Azure DevOps: {e}", []
    if resp.status_code == 401:
        return False, "Authentication failed — check the personal access token. It needs at least 'Work Items (Read)' scope.", []
    if resp.status_code == 404:
        return False, "Organization/project not found (or the token can't see it).", []
    if 300 <= resp.status_code < 400:
        return False, "Azure DevOps redirected the request instead of returning data — check the organization URL and project name.", []
    if resp.status_code != 200:
        return False, f"Azure DevOps returned HTTP {resp.status_code}.", []
    try:
        values = resp.json().get("value", [])
    except ValueError:
        return False, "Azure DevOps returned an unexpected response.", []
    names = sorted({v.get("name") for v in values if v.get("name") and not v.get("isDisabled", False)})
    if not names:
        return False, "No work item types found for that project.", []
    return True, f"Found {len(names)} work item type(s) in this project.", names


def fetch_ado_states(mode: str, org_url: str, project_name: str, pat: str,
                      work_item_type: str) -> tuple[bool, str, list[str]]:
    """The states picker should offer only states the connected project's process
    template actually defines for this work item type, not a generic guess."""
    org_url = (org_url or "").strip()
    project_name = (project_name or "").strip()
    pat = pat or ""
    work_item_type = (work_item_type or "").strip()
    if not (org_url and project_name and pat.strip() and work_item_type):
        return False, "Fill in the organization, project, personal access token, and work item type first.", []
    if mode != "live":
        return True, "Demo mode — showing common states, not your real project's.", list(ADO_STATE_PRESETS)
    return _fetch_ado_states_live(org_url, project_name, pat.strip(), work_item_type)


def _fetch_ado_states_live(org_url: str, project_name: str, pat: str, work_item_type: str) -> tuple[bool, str, list[str]]:
    import urllib.parse

    import requests

    encoded_type = urllib.parse.quote(work_item_type, safe="")
    url = f"{org_url.rstrip('/')}/{project_name}/_apis/wit/workitemtypes/{encoded_type}/states?api-version=7.1"
    try:
        resp = requests.get(url, auth=("", pat), timeout=15, allow_redirects=False)
    except requests.RequestException as e:
        return False, f"Couldn't reach Azure DevOps: {e}", []
    if resp.status_code == 401:
        return False, "Authentication failed — check the personal access token.", []
    if resp.status_code == 404:
        return False, f"Work item type \"{work_item_type}\" wasn't found in this project.", []
    if 300 <= resp.status_code < 400:
        return False, "Azure DevOps redirected the request instead of returning data — check the organization URL and project name.", []
    if resp.status_code != 200:
        return False, f"Azure DevOps returned HTTP {resp.status_code}.", []
    try:
        values = resp.json().get("value", [])
    except ValueError:
        return False, "Azure DevOps returned an unexpected response.", []
    names = [v.get("name") for v in values if v.get("name")]
    if not names:
        return False, f"No states found for work item type \"{work_item_type}\".", []
    return True, f"Found {len(names)} state(s) for {work_item_type}.", names


def fetch_ado_work_items(mode: str, org_url: str, project_name: str, pat: str,
                          work_item_type: str, states: list[str]) -> tuple[bool, str, list[dict]]:
    """Preview which Azure DevOps work items would count as "projects" under
    the configured work item type + included states, so the setting can be
    checked against the real board rather than typed in blind."""
    org_url = (org_url or "").strip()
    project_name = (project_name or "").strip()
    pat = pat or ""
    work_item_type = (work_item_type or "").strip()
    states = [s.strip() for s in (states or []) if s.strip()]
    if not (org_url and project_name and pat.strip() and work_item_type and states):
        return False, "Fill in the organization, project, work item type, and at least one state first.", []
    if mode != "live":
        return True, "Demo mode — no real query made. Switch to Live mode to preview real work items.", []
    return _fetch_ado_work_items_live(org_url, project_name, pat.strip(), work_item_type, states)


def _wiql_literal(value: str) -> str:
    """Quote a string for embedding in a WIQL query -- WIQL has no parameterized
    queries for this endpoint, so single quotes are doubled per its own syntax."""
    return "'" + value.replace("'", "''") + "'"


def _fetch_ado_work_items_live(org_url: str, project_name: str, pat: str,
                                work_item_type: str, states: list[str]) -> tuple[bool, str, list[dict]]:
    import requests

    org_url = org_url.rstrip("/")
    states_clause = ", ".join(_wiql_literal(s) for s in states)
    query = (
        "SELECT [System.Id] FROM WorkItems "
        f"WHERE [System.TeamProject] = @project AND [System.WorkItemType] = {_wiql_literal(work_item_type)} "
        f"AND [System.State] IN ({states_clause})"
    )
    wiql_url = f"{org_url}/{project_name}/_apis/wit/wiql?api-version=7.1"
    try:
        resp = requests.post(wiql_url, auth=("", pat), json={"query": query}, timeout=15, allow_redirects=False)
    except requests.RequestException as e:
        return False, f"Couldn't reach Azure DevOps: {e}", []
    if resp.status_code == 401:
        return False, "Authentication failed — check the personal access token. It needs at least 'Work Items (Read)' scope.", []
    if resp.status_code == 404:
        return False, f"Organization/project not found (or the token can't see it).", []
    if 300 <= resp.status_code < 400:
        return False, "Azure DevOps redirected the request instead of returning data — check the organization URL and project name.", []
    if resp.status_code != 200:
        return False, f"Azure DevOps returned HTTP {resp.status_code} for the query.", []
    try:
        work_items = resp.json().get("workItems", [])
    except ValueError:
        return False, "Azure DevOps returned an unexpected response.", []
    if not work_items:
        return True, f"No {work_item_type} work items found in state(s) {', '.join(states)}.", []

    ids = [str(w["id"]) for w in work_items[:200]]
    detail_url = f"{org_url}/_apis/wit/workitems?ids={','.join(ids)}&fields=System.Title,System.State&api-version=7.1"
    try:
        detail_resp = requests.get(detail_url, auth=("", pat), timeout=15, allow_redirects=False)
    except requests.RequestException as e:
        return True, f"Found {len(work_items)} matching work item(s), but couldn't fetch titles: {e}", []
    items = []
    if detail_resp.status_code == 200:
        try:
            for wi in detail_resp.json().get("value", []):
                fields = wi.get("fields", {})
                items.append({
                    "id": wi.get("id"),
                    "name": fields.get("System.Title", f"Work item {wi.get('id')}"),
                    "state": fields.get("System.State", ""),
                })
        except ValueError:
            pass
    return True, f"Found {len(work_items)} matching {work_item_type} work item(s).", items


def test_storage_connection(mode: str, provider_key: str, target: str, secret: str) -> tuple[bool, str]:
    target = target or ""
    if not target.strip():
        return False, "Fill in the connection details first."
    if mode != "live":
        return True, "Connection settings look good (demo mode — no real API call made)."
    if provider_key == "azureTable":
        return _test_azure_table_live(secret or "", target.strip())
    if provider_key == "sharepoint":
        return _test_sharepoint_live(target.strip())
    if provider_key == "sqlDatabase":
        return _test_sql_live(target.strip())
    return False, "Unknown storage provider."


def _test_azure_table_live(conn_str: str, table_name: str) -> tuple[bool, str]:
    if not conn_str.strip():
        return False, "Paste the storage account connection string first."
    try:
        from azure.data.tables import TableServiceClient

        client = TableServiceClient.from_connection_string(conn_str)
        tables = [t.name for t in client.list_tables()]
    except Exception as e:  # noqa: BLE001 - surfacing the SDK's own error is the point
        return False, f"Couldn't connect to Azure Table Storage: {e}"
    if table_name and table_name not in tables:
        return True, (
            f"Connected to the storage account, but table \"{table_name}\" doesn't exist yet "
            f"(found {len(tables)} other table(s)). It will be created on first write."
        )
    return True, f"Connected — {len(tables)} table(s) visible in this storage account."


def _test_sharepoint_live(target: str) -> tuple[bool, str]:
    import requests

    site_url = target.split(" ")[0].strip()
    if not site_url.startswith("http"):
        return False, "Enter the SharePoint site URL first, e.g. https://contoso.sharepoint.com/sites/..."
    try:
        resp = requests.head(site_url, timeout=8, allow_redirects=True)
    except requests.RequestException as e:
        return False, f"Couldn't reach {site_url}: {e}"
    if resp.status_code < 500:
        return True, (
            f"Site responded (HTTP {resp.status_code}). This only confirms the site is reachable — "
            "full list access needs an Azure AD app registration (see the guide below)."
        )
    return False, f"Site responded with HTTP {resp.status_code}."


def _test_sql_live(target: str) -> tuple[bool, str]:
    m = re.search(r"(?:Server|Data Source|Host)\s*=\s*([^;]+)", target, re.IGNORECASE)
    host_part = (m.group(1) if m else target).strip()
    host_part = re.sub(r"^tcp:", "", host_part, flags=re.IGNORECASE)
    if ":" in host_part:
        host, _, port_s = host_part.partition(":")
    elif "," in host_part:
        host, _, port_s = host_part.partition(",")
    else:
        host, port_s = host_part, ""
    host = host.strip()
    port = int(port_s.strip()) if port_s.strip().isdigit() else 1433
    if not host:
        return False, "Enter a server/host in the connection string first."
    try:
        with socket.create_connection((host, port), timeout=6):
            pass
    except OSError as e:
        return False, f"Couldn't reach {host}:{port} — {e}"
    return True, (
        f"{host}:{port} is reachable. This checks network connectivity only — it does not validate "
        "credentials or run a query (see the guide below)."
    )
