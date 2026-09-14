"""Weekly Timesheet — a Streamlit port of the Claude Design handoff.

Single-user app (no login) with a fake "team" roster so the My Team tab has
something to show. Data is stored in a local JSON file next to this script,
which is a reasonable stand-in for the design's browser localStorage — but
note the caveat in README.md about Streamlit Community Cloud's storage being
ephemeral across redeploys. The Manage Connections panel can run in Demo mode
(a UI placeholder that simulates success) or Live mode (real API/reachability
checks) — see lib.py's test_ado_connection/test_storage_connection.
"""

import json

import pandas as pd
import streamlit as st
from st_keyup import st_keyup

import lib

st.set_page_config(page_title="Weekly Timesheet", page_icon="\U0001f5d3", layout="wide")

# ---------------------------------------------------------------------------
# Style
# ---------------------------------------------------------------------------
C = lib.COLOR
st.markdown(
    f"""
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700;800&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style>
      html, body, [class*="css"] {{ font-family: 'Public Sans', system-ui, sans-serif; }}
      .stApp {{ background: {C['page_bg']}; }}
      .block-container {{ max-width: 1180px; }}
      .ts-mono {{ font-family: 'Space Mono', monospace; }}
      .ts-nowrap {{ white-space: nowrap; }}
      .ts-pill {{ display:inline-flex; align-items:center; padding:5px 12px; border-radius:100px;
                  font-size:12.5px; font-weight:700; white-space:nowrap; }}
      .ts-card {{ background:{C['white']}; border:1px solid {C['border']}; border-radius:12px; padding:14px 16px; }}
      .ts-bar-track {{ height:8px; background:{C['disabled_bg']}; border-radius:4px; overflow:hidden; }}
      .ts-bar-fill {{ height:100%; border-radius:4px; }}
      .ts-muted {{ color:{C['text_muted']}; font-size:13px; }}
      .ts-section-label {{ font-size:11.5px; font-weight:700; color:{C['text_secondary']};
                            text-transform:uppercase; letter-spacing:0.04em; }}
      div[data-testid="stVerticalBlockBorderWrapper"] {{ border-radius: 12px; }}
      button[kind="secondary"] {{ border-color: {C['border']} !important; }}
      /* `st.columns(..., wrap=False)` keeps a row from stacking on narrow screens,
         but Streamlit still floors every column at 128px so it can scroll instead
         of squeezing -- way oversized for compact toolbar rows of a button/avatar/
         badge, which just need to shrink to their own content instead. The hours
         grid is the one place a scrollable, fixed-ish column width is actually
         wanted, so it keeps a smaller (not zero) floor to stay tap-friendly. */
      div[data-testid="stHorizontalBlock"][data-test-wrap="false"] > div[data-testid="stColumn"] {{
        min-width: 0 !important;
      }}
      .st-key-hours_grid div[data-testid="stHorizontalBlock"][data-test-wrap="false"] > div[data-testid="stColumn"] {{
        min-width: 54px !important;
      }}
      /* These rows mix short widgets with real text/pills that mustn't shrink
         below their own content (unlike the icon-sized toolbar clusters above) --
         a smaller-than-default floor avoids both the wasted 128px-per-column
         scroll AND text/pills overlapping their neighbors. */
      [class*="st-key-history_row_"] div[data-testid="stHorizontalBlock"][data-test-wrap="false"] > div[data-testid="stColumn"],
      [class*="st-key-team_roster_row_"] div[data-testid="stHorizontalBlock"][data-test-wrap="false"] > div[data-testid="stColumn"] {{
        min-width: 92px !important;
      }}
      /* A no-wrap row that's a couple of px short of fitting shows a thin
         scrollbar-on-hover even though nothing meaningful is cut off -- these
         rows were all sized to fit, so hide the browser's scroll affordance
         without disabling the actual scroll (still swipeable on mobile where
         a row genuinely doesn't fit, e.g. the hours grid or History/Team rows). */
      div[data-testid="stHorizontalBlock"][data-test-wrap="false"] {{
        scrollbar-width: none;
      }}
      div[data-testid="stHorizontalBlock"][data-test-wrap="false"]::-webkit-scrollbar {{
        display: none;
      }}
      /* st.container(key=...) has no styling of its own -- these two give the
         breakdown/trend cards the same look as the .ts-card markdown-built ones
         used elsewhere, since real widgets inside them (segmented_control) can't
         be part of a raw HTML string the way the stat tiles above are. */
      .st-key-history_breakdown_card, .st-key-team_by_project_card {{
        background: {C['white']}; border: 1px solid {C['border']}; border-radius: 12px; padding: 14px 16px;
      }}
    </style>
    """,
    unsafe_allow_html=True,
)


def pill(label: str, bg: str, fg: str) -> str:
    return f'<span class="ts-pill" style="background:{bg};color:{fg};">{lib.esc(label)}</span>'


def bar(name: str, hours_display: str, pct: int, kind: str) -> str:
    fill_color = C["accent"] if kind == "project" else C["category_bar"]
    return f"""
    <div style="margin-bottom:12px;">
      <div style="display:flex;justify-content:space-between;font-size:13px;margin-bottom:5px;">
        <div style="font-weight:600;">{lib.esc(name)}</div>
        <div class="ts-mono" style="font-weight:700;color:{C['text_secondary']};">{hours_display}h &middot; {pct}%</div>
      </div>
      <div class="ts-bar-track"><div class="ts-bar-fill" style="width:{pct}%;background:{fill_color};"></div></div>
    </div>
    """


def trend_chart_frame(week_starts: list[str], weeks_dict: dict, scope: str) -> tuple[pd.DataFrame, list[str]]:
    """Wide-format weekly-hours-by-project frame for st.bar_chart, plus the
    matching color list (same order as the columns).

    Column order -- and therefore color assignment -- follows a FIXED master
    order (every real project, then every category, in their stable app-wide
    order) rather than each render's own totals, so a given project keeps the
    same color as the Period/Scope filters change instead of being reshuffled
    by rank. Entities past the 8 validated categorical slots, or not in the
    fixed list (e.g. a since-removed category), fold into one neutral "Other"
    column rather than making up a new hue.
    """
    fixed_order = [p["name"] for p in lib.PROJECTS] + [c["name"] for c in data["categories"]]
    weekly = lib.weekly_breakdown(week_starts, weeks_dict, scope)

    present = {name for wk in weekly for name in wk["by_name"]}
    named_columns: list[str] = []
    colors: list[str] = []
    for idx, n in enumerate(fixed_order):
        if idx >= len(lib.CHART_CATEGORICAL):
            break
        if n in present:
            named_columns.append(n)
            colors.append(lib.CHART_CATEGORICAL[idx])
    has_other = any(n not in named_columns for n in present)
    columns = named_columns + (["Other"] if has_other else [])
    colors = colors + ([lib.CHART_OTHER] if has_other else [])

    records = []
    for wk in weekly:
        row = {"Week": pd.Timestamp(wk["week_start"])}
        for col in named_columns:
            row[col] = wk["by_name"].get(col, 0.0)
        if has_other:
            row["Other"] = sum(h for n, h in wk["by_name"].items() if n not in named_columns)
        records.append(row)
    df = pd.DataFrame(records).set_index("Week")
    return df[columns] if columns else df, colors


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------
if "data" not in st.session_state:
    st.session_state.data = lib.load_persisted()

_DEFAULTS = {
    "current_week": lib.today_monday(),
    "active_tab": "My Time",
    "confirm_submit_open": False,
    "settings_open": False,
    "settings_section": "My working week",
    "new_category_name": "",
    "new_member_name": "",
    "add_query_nonce": 0,
    "current_user_id": "self",  # who the "Viewing as" switcher says you are -- for demo/testing only
    "ado_pat": "",  # session-only: never written to disk
    "storage_secret": "",  # session-only: never written to disk
    "ado_test_result": None,
    "ado_preview_result": None,
    "ado_types_result": None,
    "ado_states_result": None,
    "storage_test_result": None,
}
for _k, _v in _DEFAULTS.items():
    st.session_state.setdefault(_k, _v)

# Streamlit dialogs can also be dismissed via their own built-in close button,
# clicking outside them, or Escape. `on_dismiss` (passed to each @st.dialog
# below) runs one of these handlers in exactly that case, so the flag is reset
# immediately instead of going stale at True -- which used to make the dialog
# silently pop back open on the next unrelated rerun (e.g. switching tabs).
_DIALOG_FLAGS = ["settings_open", "confirm_submit_open"]


def _dismiss_handler(flag_name: str):
    def _handler() -> None:
        st.session_state[flag_name] = False
    return _handler


def open_dialog(flag_name: str) -> None:
    """Belt-and-braces: force every other dialog flag off before opening one,
    so a flag that somehow went stale can never combine with a freshly-opened
    dialog and trip Streamlit's "only one dialog at a time" error."""
    for f in _DIALOG_FLAGS:
        st.session_state[f] = (f == flag_name)

data = st.session_state.data


def save() -> None:
    lib.persist(data)


def current_person() -> dict:
    """Who the "Viewing as" switcher in the top bar says you are right now --
    a demo/testing stand-in for real per-user login. Drives admin-only UI
    (Approve/Release, Settings' Team/Categories/Connections) AND which
    timesheet My Time/My History actually read and edit -- "weeks_dict" is
    that person's own week-keyed store (data["weeks"] for self, otherwise
    their team-member record's "weeks") so switching viewer genuinely
    switches whose data you're looking at, not just the permission badge."""
    uid = st.session_state.current_user_id
    if uid == "self":
        return {
            "id": "self", "name": "Morgan Lee", "initials": "ML",
            "avatar_bg": C["accent_tint"], "avatar_fg": C["accent_tint_text"],
            "is_admin": data.get("self_is_admin", True),
            "weeks_dict": data["weeks"],
        }
    for idx, m in enumerate(data["team"]):
        if m["id"] == uid:
            tint, tint_text = lib.AVATAR_TINTS[idx % len(lib.AVATAR_TINTS)]
            m.setdefault("weeks", {})
            return {
                "id": m["id"], "name": m["name"], "initials": lib.initials_of(m["name"]),
                "avatar_bg": tint, "avatar_fg": tint_text, "is_admin": m.get("is_admin", False),
                "weeks_dict": m["weeks"],
            }
    st.session_state.current_user_id = "self"
    return current_person()


def current_week_dict() -> dict:
    """The currently-viewed person's week record for st.session_state.current_week,
    creating it if this is the first time they've touched that week."""
    return lib.get_week_from(current_person()["weeks_dict"], st.session_state.current_week)


# ---------------------------------------------------------------------------
# Mutators (used as on_click callbacks so they may safely prime the
# session_state of OTHER already-instantiated widgets before the next rerun)
# ---------------------------------------------------------------------------
def add_row(kind: str, ref_id: str, name: str) -> None:
    week = current_week_dict()
    if any(r["ref_id"] == ref_id for r in week["rows"]):
        return
    week["rows"].append({"id": lib.new_id("row"), "kind": kind, "ref_id": ref_id, "name": name, "hours": lib.empty_hours()})
    # st_keyup ignores externally-assigned session_state; bumping the nonce
    # remounts the search box under a fresh key, which is how it actually clears.
    st.session_state.add_query_nonce += 1
    save()


def remove_row(row_id: str) -> None:
    week = current_week_dict()
    week["rows"] = [r for r in week["rows"] if r["id"] != row_id]
    save()


def fill_row_evenly(row_id: str) -> None:
    week_start = st.session_state.current_week
    week = current_week_dict()
    if week["status"] != "draft":
        return
    row = next((r for r in week["rows"] if r["id"] == row_id), None)
    if not row:
        return
    days = lib.day_meta(week_start, data["working_week"])
    active = [d for d in days if d["active"]]
    existing = next((row["hours"][d["key"]] for d in active if row["hours"][d["key"]] > 0), None)
    if not existing:
        st.toast("Enter a value first")
        return
    for d in active:
        row["hours"][d["key"]] = existing
        st.session_state[f"cell_{row_id}_{d['key']}"] = existing
    save()
    st.toast(f"Applied {lib.fmt_hours(existing)}h to all active days")


def fill_remaining() -> None:
    week_start = st.session_state.current_week
    week = current_week_dict()
    if week["status"] != "draft" or not week["rows"]:
        return
    days = lib.day_meta(week_start, data["working_week"])
    first = week["rows"][0]
    changed = False
    for d in days:
        if not d["active"]:
            continue
        total_other = sum(r["hours"][d["key"]] for r in week["rows"] if r["id"] != first["id"])
        remaining = d["target"] - total_other - first["hours"][d["key"]]
        if remaining > 0:
            first["hours"][d["key"]] += remaining
            st.session_state[f"cell_{first['id']}_{d['key']}"] = first["hours"][d["key"]]
            changed = True
    if not changed:
        st.toast("Nothing to fill")
        return
    save()
    st.toast(f"Filled remaining hours into {first['name']}")


def copy_last_week() -> None:
    week_start = st.session_state.current_week
    prev = lib.get_week_from(current_person()["weeks_dict"], lib.shift_date(week_start, -7))
    if not prev["rows"]:
        st.toast("No projects logged last week")
        return
    week = current_week_dict()
    existing_refs = {r["ref_id"] for r in week["rows"]}
    to_add = [
        {"id": lib.new_id("row"), "kind": r["kind"], "ref_id": r["ref_id"], "name": r["name"], "hours": lib.empty_hours()}
        for r in prev["rows"] if r["ref_id"] not in existing_refs
    ]
    if not to_add:
        st.toast("Already added those projects")
        return
    week["rows"].extend(to_add)
    save()
    st.toast(f"Copied {len(to_add)} project{'s' if len(to_add) > 1 else ''} from last week")


def submit_week() -> None:
    from datetime import datetime
    week = current_week_dict()
    week["status"] = "submitted"
    week["submitted_at"] = datetime.now().isoformat(timespec="seconds")
    save()
    st.toast("Submitted for approval")


def edit_week() -> None:
    week = current_week_dict()
    if week["status"] != "submitted":
        return
    week["status"] = "draft"
    save()


def apply_standard_hours() -> None:
    for k in lib.DAY_KEYS:
        if data["working_week"][k]["active"]:
            data["working_week"][k]["hours"] = 7.5
            st.session_state[f"wwhours_{k}"] = 7.5
    save()


def add_category() -> None:
    name = st.session_state.new_category_name.strip()
    if not name:
        return
    data["categories"].append({"id": lib.new_id("cat"), "name": name})
    st.session_state.new_category_name = ""
    save()


def remove_category(cat_id: str) -> None:
    data["categories"] = [c for c in data["categories"] if c["id"] != cat_id]
    save()


def add_team_member() -> None:
    name = st.session_state.new_member_name.strip()
    if not name:
        return
    data["team"].append({"id": lib.new_id("member"), "name": name, "weeks": {}, "is_admin": False})
    st.session_state.new_member_name = ""
    save()


def remove_team_member(member_id: str) -> None:
    data["team"] = [m for m in data["team"] if m["id"] != member_id]
    save()


def open_history_week(week_start: str) -> None:
    st.session_state.current_week = week_start
    st.session_state["_pending_tab"] = "My Time"


def approve_member_week(week: dict) -> None:
    if lib.approve_week(week):
        save()
        st.toast("Timesheet approved")


def release_member_week(week: dict) -> None:
    if lib.release_week(week):
        save()
        st.toast("Timesheet released — it can be edited again")


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------
def _render_working_week_section() -> None:
    st.caption("Used to check each day adds up before you submit. Saved automatically.")
    for i, k in enumerate(lib.DAY_KEYS):
        wd = data["working_week"][k]
        c1, c2, c3 = st.columns([0.4, 2, 1], wrap=False)
        active_key = f"wwactive_{k}"
        extra_a = {} if active_key in st.session_state else {"value": wd["active"]}
        active = c1.checkbox(" ", key=active_key, label_visibility="collapsed", **extra_a)
        wd["active"] = active
        c2.markdown(lib.DAY_FULL[i])
        hours_key = f"wwhours_{k}"
        # apply_standard_hours() primes this key directly; see the cell input for why
        # `value=` is conditional.
        extra_h = {} if hours_key in st.session_state else {"value": float(wd["hours"])}
        hours = c3.number_input(
            "hours", min_value=0.0, max_value=24.0, step=0.5,
            key=hours_key, disabled=not active, label_visibility="collapsed", **extra_h,
        )
        wd["hours"] = hours
    save()
    st.write("")
    st.button("Set all active days to 7.5h", on_click=apply_standard_hours)


def _render_projects_section() -> None:
    enabled_count = sum(1 for p in lib.PROJECTS if data["enabled_projects"].get(p["id"], True))
    st.markdown(
        f"**My projects**  \n<span class='ts-muted'>Choose which projects appear in your Add project list &mdash; {enabled_count} enabled.</span>",
        unsafe_allow_html=True,
    )
    query = st_keyup(
        "Search projects", key="project_admin_query", placeholder="Search projects...",
        label_visibility="collapsed", debounce=150,
    )
    q = query.strip().lower()
    matching = [p for p in lib.PROJECTS if q in p["name"].lower()]
    matching.sort(key=lambda p: not data["enabled_projects"].get(p["id"], True))
    cap = 8
    for p in matching[:cap]:
        c1, c2 = st.columns([0.4, 2], wrap=False)
        enabled = c1.checkbox(" ", value=data["enabled_projects"].get(p["id"], True), key=f"projtoggle_{p['id']}", label_visibility="collapsed")
        data["enabled_projects"][p["id"]] = enabled
        c2.markdown(p["name"])
    if len(matching) > cap:
        st.caption(f"+{len(matching) - cap} more — keep typing to find them.")
    elif not matching:
        st.caption("No matching projects.")
    save()


def _render_team_section() -> None:
    st.markdown(
        "**Team**  \n<span class='ts-muted'>People who show up in your Team rollup. "
        "Admin controls who can approve timesheets and manage this Settings menu — "
        "toggle it to test what a non-admin teammate sees.</span>",
        unsafe_allow_html=True,
    )
    hc1, hc2, hc3 = st.columns([4, 1, 0.6], wrap=False)
    hc2.markdown("<div class='ts-muted' style='text-align:center;'>Admin</div>", unsafe_allow_html=True)

    c1, c2, _ = st.columns([4, 1, 0.6], wrap=False)
    c1.markdown("<div class='ts-card' style='padding:8px 10px;'>Morgan Lee (You)</div>", unsafe_allow_html=True)
    data["self_is_admin"] = c2.checkbox(
        "Admin", value=data.get("self_is_admin", True), key="admin_self", label_visibility="collapsed",
    )

    for m in data["team"]:
        c1, c2, c3 = st.columns([4, 1, 0.6], wrap=False)
        c1.markdown(f"<div class='ts-card' style='padding:8px 10px;'>{lib.esc(m['name'])}</div>", unsafe_allow_html=True)
        m["is_admin"] = c2.checkbox(
            "Admin", value=m.get("is_admin", False), key=f"admin_{m['id']}", label_visibility="collapsed",
        )
        c3.button("", key=f"rmmem_{m['id']}", icon=":material/close:", help="Remove", on_click=remove_team_member, args=(m["id"],))
    save()

    c1, c2 = st.columns([3, 1])
    c1.text_input("New member", key="new_member_name", placeholder="New team member name...", label_visibility="collapsed")
    c2.button("Add", on_click=add_team_member, disabled=not st.session_state.new_member_name.strip())


def _render_categories_section() -> None:
    st.markdown("**Categories**  \n<span class='ts-muted'>These appear alongside projects in everyone's Add picker.</span>", unsafe_allow_html=True)
    for c in data["categories"]:
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"<div class='ts-card' style='padding:8px 10px;'>{lib.esc(c['name'])}</div>", unsafe_allow_html=True)
        c2.button("", key=f"rmcat_{c['id']}", icon=":material/close:", help="Remove", on_click=remove_category, args=(c["id"],))
    c1, c2 = st.columns([3, 1])
    c1.text_input("New category", key="new_category_name", placeholder="New category name...", label_visibility="collapsed")
    c2.button("Add", on_click=add_category, disabled=not st.session_state.new_category_name.strip())


def _render_connections_section() -> None:
    conn = data["connections"]
    st.markdown("**Connections**", unsafe_allow_html=True)
    st.caption(
        "Admin only. Demo mode simulates a successful connection. Live mode makes a real "
        "network call to check credentials or reachability."
    )

    mode_label_for = {"demo": "Demo", "live": "Live"}
    prev_mode = conn.get("mode", "demo")
    st.session_state.setdefault("connections_mode_control", mode_label_for[prev_mode])
    chosen_mode_label = st.segmented_control(
        "Mode", ["Demo", "Live"], key="connections_mode_control", label_visibility="collapsed",
    )
    new_mode = "live" if chosen_mode_label == "Live" else "demo"
    if new_mode != prev_mode:
        # A "Connected" pill or test result from the OTHER mode is meaningless here --
        # Demo always trivially "succeeds", so leaving it showing after switching to
        # Live would look like a real Live-mode result it never was.
        conn["ado_connected"] = False
        st.session_state.ado_test_result = None
        st.session_state.ado_preview_result = None
        st.session_state.ado_types_result = None
        st.session_state.ado_states_result = None
        st.session_state.storage_test_result = None
    conn["mode"] = new_mode
    if conn["mode"] == "live":
        st.caption("Live mode is on — Test connection below will make a real call.")
    save()

    st.divider()
    st.markdown("<div class='ts-section-label'>Azure DevOps</div>", unsafe_allow_html=True)
    prev_org_url, prev_project_name = conn["ado_org_url"], conn["ado_project_name"]
    conn["ado_org_url"] = st.text_input("Organization URL", value=conn["ado_org_url"], placeholder="https://dev.azure.com/yourorg")
    conn["ado_project_name"] = st.text_input("Project name", value=conn["ado_project_name"], placeholder="e.g. Platform")
    if (conn["ado_org_url"], conn["ado_project_name"]) != (prev_org_url, prev_project_name):
        # Cached work item types/states belong to whatever project was connected
        # when they were fetched -- pointing at a different org/project invalidates them.
        st.session_state.ado_types_result = None
        st.session_state.ado_states_result = None
    st.text_input("Personal access token", key="ado_pat", type="password", placeholder="Paste PAT (not saved between sessions)")
    c1, c2 = st.columns([1, 1])
    if conn["ado_connected"]:
        c1.markdown(pill("Connected", C["success_tint"], C["success_text"]), unsafe_allow_html=True)
    else:
        c1.markdown(pill("Not connected", C["danger_tint"], C["danger"]), unsafe_allow_html=True)
    if c2.button("Test connection", key="test_ado", use_container_width=True):
        ok, msg = lib.test_ado_connection(
            conn["mode"], conn["ado_org_url"], conn["ado_project_name"], st.session_state.ado_pat,
        )
        conn["ado_connected"] = ok
        save()
        st.session_state.ado_test_result = (ok, msg)
    if st.session_state.ado_test_result:
        ok, msg = st.session_state.ado_test_result
        (st.success if ok else st.error)(msg)
    with st.expander("How to set this up"):
        for line in [
            "**Organization URL** — `https://dev.azure.com/<your-org>`, found under Organization Settings or in your browser's address bar.",
            "**Project name** — the exact project name (Project Settings → Overview, or the URL segment right after the org name).",
            "**Personal access token** — top-right avatar → **Personal access tokens** → **New Token**. Grant at least **Project and Team (Read)**; add **Work Items (Read)** too if you'll use the preview below. Set an expiry and copy the token immediately — it's shown once.",
            "The token above is kept only for this browser session and is never written to disk.",
        ]:
            st.markdown(f"- {line}")

    st.markdown("<div class='ts-section-label' style='margin-top:14px;'>Which work items are &quot;projects&quot;</div>", unsafe_allow_html=True)
    st.caption("Loaded from the connected project itself, not a generic guess -- click Refresh (Live mode) after connecting.")

    if st.button("Refresh from Azure DevOps", key="refresh_ado_meta", use_container_width=True):
        ok_t, msg_t, types = lib.fetch_ado_work_item_types(
            conn["mode"], conn["ado_org_url"], conn["ado_project_name"], st.session_state.ado_pat,
        )
        st.session_state.ado_types_result = (ok_t, msg_t, types)
        if ok_t and types:
            wit = conn.get("ado_work_item_type") if conn.get("ado_work_item_type") in types else types[0]
            st.session_state.ado_states_result = lib.fetch_ado_states(
                conn["mode"], conn["ado_org_url"], conn["ado_project_name"], st.session_state.ado_pat, wit,
            )
        else:
            st.session_state.ado_states_result = None

    types_result = st.session_state.ado_types_result
    if types_result and types_result[0] and types_result[2]:
        ok_t, msg_t, available_types = types_result
        st.caption(msg_t)
        current_type = conn.get("ado_work_item_type", lib.ADO_DEFAULT_WORK_ITEM_TYPE)
        if current_type not in available_types:
            current_type = available_types[0]
        chosen_type = st.selectbox("Work item type", options=available_types, index=available_types.index(current_type))
        if chosen_type != conn.get("ado_work_item_type"):
            # Selecting a different type invalidates the states list -- it belongs to
            # whichever type it was fetched for -- so refetch for the new one.
            st.session_state.ado_states_result = lib.fetch_ado_states(
                conn["mode"], conn["ado_org_url"], conn["ado_project_name"], st.session_state.ado_pat, chosen_type,
            )
        conn["ado_work_item_type"] = chosen_type
    else:
        if types_result and not types_result[0]:
            st.error(types_result[1])
        conn["ado_work_item_type"] = st.text_input(
            "Work item type", value=conn.get("ado_work_item_type", lib.ADO_DEFAULT_WORK_ITEM_TYPE),
            placeholder="e.g. Epic, Feature, User Story, Issue",
            help="Click Refresh above (Live mode, connected) to pick from your real project's work item types instead of typing.",
        )

    states_result = st.session_state.ado_states_result
    if states_result and states_result[0] and states_result[2]:
        ok_s, msg_s, available_states = states_result
        st.caption(msg_s)
        default_states = [s for s in conn.get("ado_states", lib.ADO_DEFAULT_STATES) if s in available_states] or available_states[:1]
        conn["ado_states"] = st.multiselect(
            "Included states", options=available_states, default=default_states,
            help="Only work items in these states are treated as active projects.",
        )
    else:
        if states_result and not states_result[0]:
            st.error(states_result[1])
        conn["ado_states"] = st.multiselect(
            "Included states", options=lib.ADO_STATE_PRESETS,
            default=conn.get("ado_states", lib.ADO_DEFAULT_STATES),
            accept_new_options=True,
            help="Click Refresh above (Live mode, connected) to pick from your real project's states instead of this generic list.",
        )
    save()
    if st.button("Preview matching work items", key="preview_ado_items", use_container_width=True):
        ok, msg, items = lib.fetch_ado_work_items(
            conn["mode"], conn["ado_org_url"], conn["ado_project_name"], st.session_state.ado_pat,
            conn["ado_work_item_type"], conn["ado_states"],
        )
        st.session_state.ado_preview_result = (ok, msg, items)
    if st.session_state.ado_preview_result:
        ok, msg, items = st.session_state.ado_preview_result
        (st.success if ok else st.error)(msg)
        if items:
            for it in items[:10]:
                st.markdown(f"- **{lib.esc(it['name'])}** &nbsp; <span class='ts-muted'>{lib.esc(it['state'])}</span>", unsafe_allow_html=True)
            if len(items) > 10:
                st.caption(f"+{len(items) - 10} more")

    st.divider()
    st.markdown("<div class='ts-section-label'>Data storage</div>", unsafe_allow_html=True)
    st.caption("Where submitted timesheet entries would be written.")
    provider_labels = [p["label"] for p in lib.STORAGE_PROVIDERS]
    current_provider = next(p for p in lib.STORAGE_PROVIDERS if p["key"] == conn["storage_provider"])
    chosen = st.radio("Storage provider", provider_labels, index=provider_labels.index(current_provider["label"]), label_visibility="collapsed")
    chosen_provider = next(p for p in lib.STORAGE_PROVIDERS if p["label"] == chosen)
    conn["storage_provider"] = chosen_provider["key"]
    conn["storage_target"] = st.text_input(chosen_provider["target_label"], value=conn["storage_target"], placeholder=chosen_provider["target_placeholder"])
    if chosen_provider["secret_label"]:
        st.text_input(chosen_provider["secret_label"], key="storage_secret", type="password", placeholder=chosen_provider["secret_placeholder"])

    c1, c2 = st.columns([1, 1])
    if c2.button("Test connection", key="test_storage", use_container_width=True):
        ok, msg = lib.test_storage_connection(
            conn["mode"], conn["storage_provider"], conn["storage_target"],
            st.session_state.storage_secret,
        )
        save()
        st.session_state.storage_test_result = (ok, msg)
    if st.session_state.storage_test_result:
        ok, msg = st.session_state.storage_test_result
        (st.success if ok else st.error)(msg)
    with st.expander("How to set this up"):
        for line in chosen_provider["guide"]:
            st.markdown(f"- {line}")
    save()


_SETTINGS_SECTIONS = {
    "My working week": _render_working_week_section,
    "My projects": _render_projects_section,
    "Team": _render_team_section,
    "Categories": _render_categories_section,
    "Connections": _render_connections_section,
}
_ADMIN_ONLY_SECTIONS = {"Team", "Categories", "Connections"}


@st.dialog("Settings", width="large", on_dismiss=_dismiss_handler("settings_open"))
def settings_dialog():
    is_admin = current_person()["is_admin"]
    section_names = [s for s in _SETTINGS_SECTIONS if is_admin or s not in _ADMIN_ONLY_SECTIONS]
    if st.session_state.settings_section not in section_names:
        # e.g. the viewer switched to a non-admin person, or demoted themselves,
        # while a now-hidden admin-only section was still selected.
        st.session_state.settings_section = section_names[0]

    nav_col, content_col = st.columns([1, 3], gap="medium")
    with nav_col:
        section = st.radio(
            "Section", section_names,
            key="settings_section", label_visibility="collapsed",
        )
        if not is_admin:
            st.caption("Signed in as a non-admin — team, categories, and connections are hidden.")

    with content_col:
        _SETTINGS_SECTIONS[section]()

    st.divider()
    if st.button("Done", type="primary", use_container_width=True):
        st.session_state.settings_open = False
        st.rerun()


@st.dialog("Check before you submit", on_dismiss=_dismiss_handler("confirm_submit_open"))
def confirm_submit_dialog(mismatch_lines, is_future_week, week_range):
    if is_future_week:
        st.info(f"This is a future week ({week_range}). You're submitting hours ahead of time.")
    if mismatch_lines:
        st.write("You can still submit, but check these days first:")
        for line in mismatch_lines:
            c1, c2 = st.columns([1, 1])
            c1.markdown(f"**{line['label']}**")
            c2.markdown(f"<div class='ts-mono' style='text-align:right;'>{line['detail']}</div>", unsafe_allow_html=True)
    c1, c2 = st.columns([1, 1])
    if c1.button("Go back", use_container_width=True):
        st.session_state.confirm_submit_open = False
        st.rerun()
    if c2.button("Submit anyway", type="primary", use_container_width=True):
        submit_week()
        st.session_state.confirm_submit_open = False
        st.rerun()


if st.session_state.settings_open:
    settings_dialog()

# A widget's session_state key can't be reassigned after that widget has
# already been drawn this run, so a request to switch tabs (e.g. from the
# History "Open" button) is queued here and applied before the nav widget
# below is instantiated, rather than set directly from the button handler.
if "_pending_tab" in st.session_state:
    st.session_state.active_tab = st.session_state.pop("_pending_tab")

# ---------------------------------------------------------------------------
# Top bar
# ---------------------------------------------------------------------------
top_l, top_m, top_r = st.columns([2, 2.3, 3.7])
with top_l:
    st.markdown(
        f"""<div style="display:flex;align-items:center;gap:12px;">
        <div style="width:38px;height:38px;border-radius:10px;background:{C['accent']};color:white;
                    display:flex;align-items:center;justify-content:center;font-weight:800;font-size:14px;">WT</div>
        <div style="font-size:18px;font-weight:700;">My Time</div></div>""",
        unsafe_allow_html=True,
    )
with top_m:
    active_tab = st.segmented_control(
        "Navigation", ["My Time", "My History", "My Team"],
        key="active_tab", label_visibility="collapsed",
    )
with top_r:
    person = current_person()
    c1, c2, c3, c4 = st.columns([0.5, 0.45, 1.8, 0.8], gap="xsmall", wrap=False)
    if c1.button("", icon=":material/settings:", help="Settings"):
        open_dialog("settings_open")
        st.rerun()
    c2.markdown(
        f"""<div style="width:32px;height:32px;border-radius:50%;background:{person['avatar_bg']};color:{person['avatar_fg']};
        display:flex;align-items:center;justify-content:center;font-weight:700;font-size:12.5px;margin-top:2px;">{person['initials']}</div>""",
        unsafe_allow_html=True,
    )

    # "Viewing as" is a demo/testing stand-in for real per-user login: it lets you
    # compare what an admin vs. a non-admin teammate sees, without needing real auth.
    viewer_names = {"self": "Morgan Lee (You)"}
    for m in data["team"]:
        viewer_names[m["id"]] = m["name"]
    viewer_ids = list(viewer_names.keys())
    viewer_labels = [viewer_names[i] for i in viewer_ids]
    st.session_state.setdefault("viewer_select", viewer_names.get(st.session_state.current_user_id, viewer_labels[0]))
    chosen_label = c3.selectbox(
        "Viewing as", options=viewer_labels, key="viewer_select", label_visibility="collapsed",
        help="Demo/testing: switch who you're viewing as to compare admin vs. non-admin access.",
    )
    chosen_id = viewer_ids[viewer_labels.index(chosen_label)]
    if chosen_id != st.session_state.current_user_id:
        st.session_state.current_user_id = chosen_id
        st.rerun()

    c4.markdown(
        pill(
            "Admin" if person["is_admin"] else "Member",
            C["success_tint"] if person["is_admin"] else C["disabled_bg"],
            C["success_text"] if person["is_admin"] else C["text_secondary"],
        ),
        unsafe_allow_html=True,
    )

active_tab = active_tab or "My Time"
current_week = st.session_state.current_week
days = lib.day_meta(current_week, data["working_week"])
viewer = current_person()
# "My Time"/"My History" show and edit whoever's currently selected in "Viewing
# as" -- NOT necessarily Morgan -- so this must come from their own weeks_dict,
# not a hardcoded lib.get_week(data, ...) (which is always Morgan's).
week = lib.get_week_from(viewer["weeks_dict"], current_week)
locked = week["status"] in ("submitted", "approved")
is_future_week = current_week > lib.today_monday()

# ---------------------------------------------------------------------------
# My Time
# ---------------------------------------------------------------------------
if active_tab == "My Time":
    nav1, nav2, nav3, nav4, nav5 = st.columns([0.5, 1.6, 0.5, 0.7, 1], gap="xsmall", wrap=False)
    if nav1.button("", icon=":material/chevron_left:", help="Previous week"):
        st.session_state.current_week = lib.shift_date(current_week, -7)
        st.rerun()
    nav2.markdown(f"<div style='text-align:center;font-weight:700;font-size:15px;padding-top:6px;'>{lib.week_range_label(current_week)}</div>", unsafe_allow_html=True)
    if nav3.button("", icon=":material/chevron_right:", help="Next week"):
        st.session_state.current_week = lib.shift_date(current_week, 7)
        st.rerun()
    if current_week != lib.today_monday():
        if nav4.button("Today"):
            st.session_state.current_week = lib.today_monday()
            st.rerun()
    meta = lib.STATUS_META[week["status"]]
    nav5.markdown(f"<div style='text-align:right;'>{pill(meta['label'], meta['bg'], meta['fg'])}</div>", unsafe_allow_html=True)

    week_total = lib.week_total(week)
    week_target = lib.week_target(days)
    progress_pct = min(100, round(week_total / week_target * 100)) if week_target > 0 else 0
    diff = week_target - week_total
    if week_target == 0:
        remaining_label = "No working days set"
    elif diff > 0:
        remaining_label = f"{lib.fmt_hours(diff)}h remaining"
    elif diff < 0:
        remaining_label = f"{lib.fmt_hours(-diff)}h over"
    else:
        remaining_label = "All accounted for"

    st.markdown(
        f"""<div class="ts-card" style="display:flex;align-items:center;gap:16px;margin-top:12px;">
        <div class="ts-mono" style="font-size:22px;font-weight:700;white-space:nowrap;">{lib.fmt_hours(week_total)}
          <span style="font-size:13px;color:{C['text_secondary']};font-weight:500;"> / {lib.fmt_hours(week_target)}h</span></div>
        <div style="flex:1;" class="ts-bar-track"><div class="ts-bar-fill" style="width:{progress_pct}%;background:{C['accent']};"></div></div>
        <div style="font-size:13px;font-weight:600;color:{C['text_secondary']};white-space:nowrap;">{remaining_label}</div>
        </div>""",
        unsafe_allow_html=True,
    )

    if week_target == 0:
        wc1, wc2 = st.columns([4, 1])
        wc1.warning("Set your working week to get started — we'll use it to check your hours each week.")
        if wc2.button("Set working week"):
            open_dialog("settings_open")
            st.rerun()

    st.write("")
    b1, b2, b3, b4 = st.columns([1.3, 1.6, 1.6, 5])
    with b1.popover("+ Add project", disabled=locked):
        query = st_keyup(
            "Search", key=f"add_query_{st.session_state.add_query_nonce}",
            placeholder="Search projects...", label_visibility="collapsed", debounce=150,
        )
        q = query.strip().lower()
        existing_refs = {r["ref_id"] for r in week["rows"]}
        add_projects = [p for p in lib.PROJECTS if data["enabled_projects"].get(p["id"], True)
                        and p["id"] not in existing_refs and q in p["name"].lower()]
        add_cats = [c for c in data["categories"] if c["id"] not in existing_refs and q in c["name"].lower()]
        if add_projects:
            st.caption("PROJECTS")
            for p in add_projects:
                st.button(p["name"], key=f"addp_{p['id']}", on_click=add_row, args=("project", p["id"], p["name"]), use_container_width=True)
        if add_cats:
            st.caption("LEAVE & OTHER")
            for c in add_cats:
                st.button(c["name"], key=f"addc_{c['id']}", on_click=add_row, args=("category", c["id"], c["name"]), use_container_width=True)
        if not add_projects and not add_cats:
            st.caption("No matches.")
    b2.button("Copy last week's projects", on_click=copy_last_week, disabled=locked)
    b3.button("Fill remaining hours", on_click=fill_remaining, disabled=locked or not week["rows"] or week_target == 0)

    st.write("")
    mismatch_days = []
    if not week["rows"]:
        st.info("No projects added yet. Add one above to get started.")
    else:
        with st.container(key="hours_grid"):
            widths = [1.9, 0.9] + [1] * 7 + [1.0, 0.4]
            hdr = st.columns(widths, wrap=False)
            hdr[0].markdown("<div class='ts-section-label'>Project</div>", unsafe_allow_html=True)
            for i, d in enumerate(days):
                color = C["text_secondary"] if d["active"] else C["text_muted"]
                hdr[2 + i].markdown(
                    f"<div style='text-align:center;color:{color};'><div style='font-size:11.5px;font-weight:700;text-transform:uppercase;'>{d['label']}</div>"
                    f"<div style='font-size:11px;'>{d['date_label']}</div></div>",
                    unsafe_allow_html=True,
                )
            hdr[-2].markdown("<div class='ts-section-label' style='text-align:right;'>Total</div>", unsafe_allow_html=True)

            for row in week["rows"]:
                cols = st.columns(widths, wrap=False)
                cols[0].markdown(f"**{lib.esc(row['name'])}**")
                cols[1].button("Even", key=f"even_{row['id']}", on_click=fill_row_evenly, args=(row["id"],),
                                disabled=locked, use_container_width=True, help="Apply first day's hours to all active days")
                for i, d in enumerate(days):
                    cell_key = f"cell_{row['id']}_{d['key']}"
                    # Omit `value=` once the key exists: fill_row_evenly/fill_remaining prime
                    # session_state directly, and passing both makes Streamlit warn.
                    extra = {} if cell_key in st.session_state else {"value": float(row["hours"][d["key"]])}
                    val = cols[2 + i].number_input(
                        d["label"], min_value=0.0, max_value=24.0, step=0.5, key=cell_key,
                        disabled=locked or not d["active"], label_visibility="collapsed", **extra,
                    )
                    row["hours"][d["key"]] = val
                total = lib.row_total(row)
                cols[-2].markdown(f"<div class='ts-mono ts-nowrap' style='text-align:right;font-weight:700;padding-top:8px;'>{lib.fmt_hours(total)}</div>", unsafe_allow_html=True)
                cols[-1].button("", key=f"rm_{row['id']}", icon=":material/close:", help="Remove", on_click=remove_row, args=(row["id"],), disabled=locked)
            save()

            footer = st.columns(widths, wrap=False)
            footer[0].markdown("**Total**")
            for i, d in enumerate(days):
                day_total = sum(r["hours"][d["key"]] for r in week["rows"])
                if d["active"]:
                    if day_total == d["target"]:
                        bg, fg = C["success_tint"], C["success_text"]
                    elif day_total > d["target"]:
                        bg, fg = C["progress_tint"], C["progress_text"]
                        mismatch_days.append({"label": d["label"], "total": day_total, "target": d["target"]})
                    else:
                        bg, fg = C["progress_tint"], C["progress_text"]
                        mismatch_days.append({"label": d["label"], "total": day_total, "target": d["target"]})
                    display = f"{lib.fmt_hours(day_total)}/{lib.fmt_hours(d['target'])}"
                else:
                    bg, fg, display = "transparent", C["text_muted"], "—"
                footer[2 + i].markdown(
                    f"<div class='ts-mono ts-nowrap' style='text-align:center;padding:6px 2px;border-radius:6px;background:{bg};color:{fg};font-size:12.5px;font-weight:700;'>{display}</div>",
                    unsafe_allow_html=True,
                )
            footer[-2].markdown(f"<div class='ts-mono ts-nowrap' style='text-align:right;font-weight:800;'>{lib.fmt_hours(week_total)}/{lib.fmt_hours(week_target)}</div>", unsafe_allow_html=True)

    st.write("")
    week["note"] = st.text_area(
        "Anything we should know? (optional)", value=week.get("note", ""),
        placeholder="e.g. out sick Wednesday afternoon, conference travel...",
        height=68, disabled=locked, key=f"note_{viewer['id']}_{current_week}",
    )
    save()

    st.write("")
    helper_col, btn_col = st.columns([3, 1])
    if week["status"] == "approved":
        helper_text = f"Approved {lib.format_submitted_at(week['approved_at'])}. Locked until your manager releases it."
    elif week["status"] == "submitted":
        helper_text = f"Submitted {lib.format_submitted_at(week['submitted_at'])}. Locked for editing."
    elif week_target == 0:
        helper_text = "Set your working week in Settings first."
    elif not week["rows"]:
        helper_text = "Add a project above to get started."
    elif not mismatch_days:
        helper_text = "All hours accounted for."
    elif len(mismatch_days) == 1:
        helper_text = f"{mismatch_days[0]['label']} looks different from your usual hours."
    else:
        helper_text = f"{len(mismatch_days)} days differ from your usual hours: {', '.join(d['label'] for d in mismatch_days)}."
    helper_col.markdown(f"<div class='ts-muted' style='padding-top:8px;'>{lib.esc(helper_text)}</div>", unsafe_allow_html=True)

    if week["status"] == "draft":
        submit_disabled = not (week_target > 0 and week["rows"])
        if btn_col.button("Submit for approval", type="primary", disabled=submit_disabled, use_container_width=True):
            if not mismatch_days and not is_future_week:
                submit_week()
                st.rerun()
            else:
                open_dialog("confirm_submit_open")
                st.rerun()
    elif week["status"] == "submitted":
        if btn_col.button("Edit", use_container_width=True):
            edit_week()
            st.rerun()
    else:
        btn_col.button(
            "Locked", disabled=True, use_container_width=True,
            help="Approved by your manager. Ask them to release it before you can edit.",
        )

    if st.session_state.confirm_submit_open:
        lines = [{"label": d["label"], "detail": f"{lib.fmt_hours(d['total'])}h logged · {lib.fmt_hours(d['target'])}h usual"} for d in mismatch_days]
        confirm_submit_dialog(lines, is_future_week, lib.week_range_label(current_week))

# ---------------------------------------------------------------------------
# My History
# ---------------------------------------------------------------------------
elif active_tab == "My History":
    f1, f2 = st.columns([2, 2])
    period = f1.segmented_control("Period", lib.PERIOD_OPTIONS, default="8 weeks", key="dashboard_period")
    scope = f2.segmented_control("Scope", lib.SCOPE_OPTIONS, default="All entries", key="dashboard_scope")
    period = period or "8 weeks"
    scope = scope or "All entries"

    week_starts = lib.person_period_week_starts(period, viewer["weeks_dict"])
    weeks_iter = [viewer["weeks_dict"][w] for w in week_starts if w in viewer["weeks_dict"]]
    breakdown, total_hours = lib.build_breakdown(weeks_iter, scope)
    weeks_with_data = sum(1 for w in weeks_iter if w["rows"])
    weeks_submitted = sum(1 for w in weeks_iter if w["status"] in ("submitted", "approved"))
    avg_per_week = total_hours / weeks_with_data if weeks_with_data else 0

    m1, m2, m3 = st.columns(3)
    m1.markdown(f"<div class='ts-card'><div class='ts-mono' style='font-size:22px;font-weight:800;'>{lib.fmt_hours(total_hours)}h</div><div class='ts-muted'>Total logged</div></div>", unsafe_allow_html=True)
    m2.markdown(f"<div class='ts-card'><div class='ts-mono' style='font-size:22px;font-weight:800;'>{lib.fmt_hours(avg_per_week)}h</div><div class='ts-muted'>Avg per week</div></div>", unsafe_allow_html=True)
    not_submitted = len(week_starts) - weeks_submitted
    extra = f"<div style='font-size:11.5px;color:{C['danger']};font-weight:700;margin-top:3px;'>{not_submitted} not submitted</div>" if not_submitted > 0 else ""
    m3.markdown(f"<div class='ts-card'><div class='ts-mono' style='font-size:22px;font-weight:800;'>{weeks_submitted}/{len(week_starts)}</div><div class='ts-muted'>Weeks submitted</div>{extra}</div>", unsafe_allow_html=True)

    st.write("")
    with st.container(key="history_breakdown_card"):
        label_col, view_col = st.columns([2, 1.4])
        label_col.markdown("<div class='ts-section-label' style='padding-top:6px;'>Where your time went</div>", unsafe_allow_html=True)
        breakdown_view = view_col.segmented_control(
            "View", ["Breakdown", "Trend"], default="Breakdown", key="history_view", label_visibility="collapsed",
        )
        breakdown_view = breakdown_view or "Breakdown"
        if breakdown_view == "Breakdown":
            if breakdown:
                for b in breakdown:
                    st.markdown(bar(b["name"], lib.fmt_hours(b["hours"]), b["pct"], b["kind"]), unsafe_allow_html=True)
            else:
                st.caption("No hours logged in this period.")
        else:
            trend_df, trend_colors = trend_chart_frame(week_starts, viewer["weeks_dict"], scope)
            if trend_df.empty or trend_df.to_numpy().sum() == 0:
                st.caption("No hours logged in this period.")
            else:
                st.bar_chart(trend_df, color=trend_colors, stack=True, height=280, x_label="", y_label="Hours")

    st.divider()
    st.caption("Past weeks you've logged. Select one to view or edit it.")
    history_weeks = sorted((w for w in viewer["weeks_dict"] if viewer["weeks_dict"][w]["rows"]), reverse=True)
    if not history_weeks:
        st.info("No timesheet history yet.")
    else:
        for ws in history_weeks:
            wkx = viewer["weeks_dict"][ws]
            daysx = lib.day_meta(ws, data["working_week"])
            totalx = lib.week_total(wkx)
            targetx = lib.week_target(daysx)
            metax = lib.STATUS_META[wkx["status"]]
            with st.container(key=f"history_row_{ws}"):
                c1, c2, c3, c4, c5 = st.columns([2, 2, 1, 1.4, 1.6], wrap=False)
                c1.markdown(f"**{lib.week_range_label(ws)}**" + (" · *current*" if ws == current_week else ""))
                c2.caption(wkx.get("note") or "")
                c3.markdown(pill(metax["label"], metax["bg"], metax["fg"]), unsafe_allow_html=True)
                c4.markdown(f"<div class='ts-mono ts-nowrap' style='text-align:right;'>{lib.fmt_hours(totalx)}/{lib.fmt_hours(targetx)}h</div>", unsafe_allow_html=True)
                c5.button("Open", key=f"open_{ws}", on_click=open_history_week, args=(ws,), use_container_width=True)

# ---------------------------------------------------------------------------
# My Team
# ---------------------------------------------------------------------------
elif active_tab == "My Team":
    nav1, nav2, nav3, nav4 = st.columns([0.5, 2, 0.5, 0.7], wrap=False)
    if nav1.button("", icon=":material/chevron_left:", help="Previous week", key="team_prev"):
        st.session_state.current_week = lib.shift_date(current_week, -7)
        st.rerun()
    nav2.markdown(f"<div style='text-align:center;font-weight:700;font-size:15px;padding-top:6px;'>{lib.week_range_label(current_week)}</div>", unsafe_allow_html=True)
    if nav3.button("", icon=":material/chevron_right:", help="Next week", key="team_next"):
        st.session_state.current_week = lib.shift_date(current_week, 7)
        st.rerun()
    if current_week != lib.today_monday():
        if nav4.button("Today", key="team_today"):
            st.session_state.current_week = lib.today_monday()
            st.rerun()
    viewer_is_admin = current_person()["is_admin"]
    if viewer_is_admin:
        st.caption("Manage team, categories, and connections from Settings (⚙ in the top bar). Demo note: this build has no real login, so \"Viewing as\" above simulates a different signed-in user for testing.")

    week_target = lib.week_target(days)
    submitted_count = sum(1 for m in data["team"] if lib.get_member_week(m, current_week)["status"] in ("submitted", "approved"))
    approved_count = sum(1 for m in data["team"] if lib.get_member_week(m, current_week)["status"] == "approved")
    hours_logged = sum(lib.week_total(lib.get_member_week(m, current_week)) for m in data["team"])
    hours_target = week_target * len(data["team"])

    st.write("")
    s1, s2, s3 = st.columns(3)
    s1.markdown(f"<div class='ts-card'><div class='ts-mono' style='font-size:22px;font-weight:800;'>{submitted_count}/{len(data['team'])}</div><div class='ts-muted'>Submitted</div></div>", unsafe_allow_html=True)
    s2.markdown(f"<div class='ts-card'><div class='ts-mono' style='font-size:22px;font-weight:800;'>{approved_count}/{len(data['team'])}</div><div class='ts-muted'>Approved</div></div>", unsafe_allow_html=True)
    s3.markdown(f"<div class='ts-card'><div class='ts-mono' style='font-size:22px;font-weight:800;'>{lib.fmt_hours(hours_logged)}/{lib.fmt_hours(hours_target)}</div><div class='ts-muted'>Hours logged</div></div>", unsafe_allow_html=True)

    st.write("")
    # The roster always lists Morgan specifically here, regardless of who "Viewing
    # as" currently has selected -- `week` above follows the viewer for My Time/My
    # History, so this needs its own always-Morgan lookup rather than reusing it.
    morgan_week = lib.get_week(data, current_week)
    self_total = lib.week_total(morgan_week)
    self_meta = lib.STATUS_META[morgan_week["status"]]
    roster = [{
        "id": "self", "name": "Morgan Lee (You)", "initials": "ML",
        "avatar_bg": C["accent_tint"], "avatar_fg": C["accent_tint_text"],
        "status": self_meta, "status_key": morgan_week["status"], "total": self_total, "target": week_target,
        "submitted_at": morgan_week["submitted_at"], "rows": morgan_week["rows"], "week_ref": morgan_week,
    }]
    for idx, m in enumerate(data["team"]):
        mwk = lib.get_member_week(m, current_week)
        tint, tint_text = lib.AVATAR_TINTS[idx % len(lib.AVATAR_TINTS)]
        roster.append({
            "id": m["id"], "name": m["name"], "initials": lib.initials_of(m["name"]),
            "avatar_bg": tint, "avatar_fg": tint_text,
            "status": lib.STATUS_META[mwk["status"]], "status_key": mwk["status"], "total": lib.week_total(mwk), "target": week_target,
            "submitted_at": mwk["submitted_at"], "rows": mwk["rows"], "week_ref": mwk,
        })

    for member in roster:
        with st.container(border=True, key=f"team_roster_row_{member['id']}"):
            c1, c2, c3, c4, c5, c6 = st.columns([0.5, 1.7, 1.1, 1.1, 1.4, 1.2], wrap=False)
            c1.markdown(f"<div style='width:32px;height:32px;border-radius:50%;background:{member['avatar_bg']};color:{member['avatar_fg']};display:flex;align-items:center;justify-content:center;font-weight:700;font-size:12.5px;'>{member['initials']}</div>", unsafe_allow_html=True)
            c2.markdown(f"**{lib.esc(member['name'])}**")
            c3.markdown(pill(member["status"]["label"], member["status"]["bg"], member["status"]["fg"]), unsafe_allow_html=True)
            c4.markdown(f"<div class='ts-mono ts-nowrap' style='text-align:right;'>{lib.fmt_hours(member['total'])}/{lib.fmt_hours(member['target'])}h</div>", unsafe_allow_html=True)
            submitted_label = lib.format_submitted_at(member["submitted_at"])
            c5.markdown(f"<div class='ts-muted' style='text-align:right;'>{submitted_label}</div>", unsafe_allow_html=True)
            if viewer_is_admin:
                if member["status_key"] == "submitted":
                    c6.button(
                        "Approve", key=f"approve_{member['id']}", type="primary", use_container_width=True,
                        on_click=approve_member_week, args=(member["week_ref"],),
                    )
                elif member["status_key"] == "approved":
                    c6.button(
                        "Release", key=f"release_{member['id']}", use_container_width=True,
                        on_click=release_member_week, args=(member["week_ref"],),
                    )
            if member["rows"]:
                with st.expander("Breakdown"):
                    for r in member["rows"]:
                        rt = lib.row_total(r)
                        if rt <= 0:
                            continue
                        breakdown_str = " · ".join(
                            f"{d['label']} {lib.fmt_hours(r['hours'][d['key']])}"
                            for d in days if d["active"] and r["hours"][d["key"]] > 0
                        ) or "No hours logged"
                        st.markdown(f"**{lib.esc(r['name'])}** &nbsp; <span class='ts-muted'>{breakdown_str}</span> &nbsp; <span class='ts-mono' style='font-weight:700;'>{lib.fmt_hours(rt)}h</span>", unsafe_allow_html=True)

    st.write("")
    with st.container(key="team_by_project_card"):
        tc1, tc2 = st.columns([1, 3])
        tc1.markdown("<div class='ts-section-label' style='padding-top:6px;'>Team time by project</div>", unsafe_allow_html=True)
        with tc2:
            p1, p2 = st.columns(2)
            team_period = p1.segmented_control("Team period", lib.PERIOD_OPTIONS, default="8 weeks", key="team_period", label_visibility="collapsed")
            team_scope = p2.segmented_control("Team scope", lib.SCOPE_OPTIONS, default="All entries", key="team_scope", label_visibility="collapsed")
        team_period = team_period or "8 weeks"
        team_scope = team_scope or "All entries"

        team_week_starts = lib.period_week_starts(team_period, data, include_team=True)
        team_weeks_iter = []
        for ws in team_week_starts:
            if ws in data["weeks"]:
                team_weeks_iter.append(data["weeks"][ws])
            for m in data["team"]:
                if ws in m.get("weeks", {}):
                    team_weeks_iter.append(m["weeks"][ws])
        team_breakdown, _ = lib.build_breakdown(team_weeks_iter, team_scope)
        if team_breakdown:
            for b in team_breakdown:
                st.markdown(bar(b["name"], lib.fmt_hours(b["hours"]), b["pct"], b["kind"]), unsafe_allow_html=True)
        else:
            st.caption("No hours logged in this period.")

# ---------------------------------------------------------------------------
# Backup (Community Cloud storage is ephemeral across redeploys/reboots)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Backup")
    st.caption("This app's storage doesn't reliably survive a redeploy. Download a backup now and then, and restore it after one.")
    st.download_button(
        "Download backup (JSON)",
        data=json.dumps(data, indent=2),
        file_name="timesheet_backup.json",
        mime="application/json",
        use_container_width=True,
    )
    uploaded = st.file_uploader("Restore from backup", type="json", label_visibility="collapsed")
    if uploaded is not None:
        try:
            restored = json.loads(uploaded.getvalue())
            if not isinstance(restored, dict) or not {"weeks", "working_week", "team"} <= restored.keys():
                raise ValueError("missing expected top-level keys")
            st.session_state.data = lib._backfill_fields(restored)
            lib.persist(st.session_state.data)
            st.success("Restored. Reloading…")
            st.rerun()
        except ValueError:
            st.error("That doesn't look like a valid backup file.")
