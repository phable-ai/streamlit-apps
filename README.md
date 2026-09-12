# streamlit-apps

Streamlit apps, deployed on [Streamlit Community Cloud](https://share.streamlit.io).
Each app lives in its own top-level folder with its own `requirements.txt`,
so a single Community Cloud project can point at `<folder>/app.py` as its
main file path without the apps interfering with each other.

## Apps

- [`timesheets/`](timesheets/) — Weekly Timesheet: log hours against
  projects/categories, submit for approval, view history and a team rollup.
