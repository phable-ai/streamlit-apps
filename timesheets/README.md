# Weekly Timesheet

A Streamlit port of the "Weekly Timesheet" Claude Design handoff: log hours
against projects or leave categories, submit a week for approval, and see
history/team rollups.

## What's real vs. mocked

- **Single-user app, no login.** The "My Team" tab shows a fake roster
  (seeded with demo data) so the team view has something to display — it
  isn't backed by real accounts.
- **Storage** is a local JSON file (`timesheet_data.json`) next to `app.py`,
  the equivalent of the original design's browser `localStorage`. On
  Streamlit Community Cloud this filesystem does **not** reliably survive a
  redeploy or a period of inactivity — use the **Backup** panel in the
  sidebar (download/restore JSON) to avoid losing data.
- **Manage Connections** (Azure DevOps + storage provider) is a UI
  placeholder exactly as designed: it records what you type and shows a
  "Connected" pill, but it never calls a real API. Wiring it up to actual
  Azure DevOps / Azure Table / SharePoint / SQL storage is future work.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy to Streamlit Community Cloud

1. Push this repo to GitHub (already done if you're reading this from the
   repo).
2. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with
   GitHub.
3. Click **New app**, choose the `streamlit-apps` repository, and set:
   - **Branch:** `main`
   - **Main file path:** `timesheets/app.py`
4. Deploy. Streamlit Cloud will pick up `timesheets/requirements.txt`
   automatically since it lives next to the main file.

Because the app has no login, anyone with the deployed URL can view and edit
the same data — treat the link accordingly, or take this as the starting
point for adding real auth before sharing it widely.
