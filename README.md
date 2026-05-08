# CV_APP — 心臟內科值班排班整合 App

Desktop app that wraps the 心臟內科 monthly call-schedule workflow in a single login-protected UI.

## Status (Phase 1)

- ✅ 排班 path — interactive 5-step solver UI (cv_solver) → writes to Google Sheets
- ⏳ key 班 path — placeholder; integration with `Key-In-The-CVSchedule` planned for Phase 2

## Run (dev)

```powershell
pip install -r requirements.txt
python main.py
```

Backend runs on `127.0.0.1:8765`; PyQt5 `QWebEngineView` opens automatically.

Web-only mode (no PyQt shell):

```powershell
uvicorn app:app --host 127.0.0.1 --port 8765
```

## Setup

1. Drop the Google service-account credential at `.gsa.json` (gitignored). Same file as the original `排班/.gsa.json` works — service account `admission-bot@sigma-sector-492215-d2.iam.gserviceaccount.com` needs Editor access on the target sheet.
2. First run creates `users.json` empty + `.secret_key`. Register the first admin via `python manage_users.py add <username>` (CLI writes a legacy entry that auto-upgrades to admin on first read), or use `/register` in the UI and then promote via direct edit of `users.json`.

## Architecture

- **`main.py`** — PyQt5 desktop launcher; spawns uvicorn in a background thread, embeds the FastAPI UI in `QWebEngineView`.
- **`app.py`** — FastAPI routes (`/login`, `/register`, `/admin`, `/`, `/sched`, `/api/sched/*`).
- **`auth.py`** — bcrypt + JWT cookie auth; `users.json` schema documented inline.
- **`audit.py`** — append-only JSONL action log.
- **`cv_solver.py`** — pure backtracking solver (no I/O). Inputs: `year, month, X, fixed, avoid, baseline`. Outputs: `(schedule_dict, stats_rows)`.
- **`gsheet_io.py`** — Google Sheets I/O (calendar grid + monthly stats + cumulative). Single source of truth for `TAIWAN_HOLIDAYS`.
- **`templates/`** — Jinja2 HTML.
- **`static/`** — CSS/JS assets.

## Scheduling rules (summary)

See the 5-step interactive flow encoded in `templates/schedule_gen.html` and the rules in `cv_solver.py`. Detail rules live in `CLAUDE.md`.

## License

Internal use; no public license.
