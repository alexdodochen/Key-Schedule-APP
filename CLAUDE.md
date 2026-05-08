# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project purpose

**CV_APP** — desktop app integrating the cardiology monthly call-schedule
workflow into a single login-protected UI. Phase 1 covers the **排班 path**
(generates the monthly schedule using a backtracking solver and writes it
to Google Sheets). Phase 2 will add a **key 班 path** that takes the solved
schedule and keys it into the NCKUH EDR system via Playwright (planned to
share auth + state with `Key-In-The-CVSchedule`).

The original schedule generator lives at `C:\Users\dr\Downloads\Y\排班\`
(scripts + Google-Sheets I/O). CV_APP wraps the same `gsheet_io.py` and
re-implements the solver as a pure module (`cv_solver.py`) so it can be
called from a FastAPI endpoint.

## Running and developing

- **Desktop launch:** `啟動.bat` → `python main.py`. Spawns uvicorn on
  `127.0.0.1:8765` in a daemon thread, then opens `QWebEngineView`. Idle >
  30 min auto-closes the window.
- **Web-only dev:** `uvicorn app:app --host 127.0.0.1 --port 8765`. Open
  `http://127.0.0.1:8765/login` in any browser. Useful when iterating on
  templates without restarting the PyQt shell.
- **First-run setup:**
  - Drop the Google service-account credential at `.gsa.json` (gitignored).
    Same `admission-bot@…iam.gserviceaccount.com` as the parent project.
  - Register the first admin: `python manage_users.py add <username>` →
    legacy string-hash schema is auto-upgraded to admin role on first
    `auth.load_users()` call. Subsequent users go via `/register` → admin
    approves in `/admin`.

Runtime deps in `requirements.txt`: `fastapi`, `uvicorn`, `jinja2`,
`python-multipart`, `bcrypt`, `python-jose`, `PyQt5`, `PyQtWebEngine`,
`gspread`, `google-auth`. **Phase 2 will add** `playwright` for keyin.

## Architecture

### Process topology

`main.py` is the desktop entry point. uvicorn runs in a background
thread; the Qt window is the only UI. There is no multi-user server — the
QWebEngineProfile cookie store is wiped on launch to force re-login.

### Request flow (`app.py`)

Cookie-based JWT auth — same scheme as `Key-In-The-CVSchedule`:
- `auth.py` (copied verbatim) → bcrypt + HS256 JWT signed by `.secret_key`.
- `audit.py` (copied verbatim) → JSONL append-only log; every meaningful
  endpoint calls `audit.log(...)`.
- `users.json` schema documented inline in `auth.py`.

Routes:
- `GET /login`, `POST /login`, `GET /logout`, `GET /register`, `POST /register`
- `GET /admin`, `GET /api/admin/users`, `POST /api/admin/approve/{u}`,
  `POST /api/admin/delete/{u}`, `GET /api/admin/logs`
- `GET /` → `home.html` (post-login menu — 排班 active, key 班 disabled)
- `GET /sched` → `schedule_gen.html` (5-step UI)
- `POST /api/sched/init` — load month context (H, W, baseline, calendar)
- `POST /api/sched/compute` — given X, return VS/建寬 counts + CR target totals
- `POST /api/sched/solve` — run backtracking solver, cache result, return
  preview (calendar + stats + QOD violations + targets)
- `POST /api/sched/write` — write cached schedule to Google Sheet (calendar
  tab + monthly stats tab + cumulative stats tab)

### Solver (`cv_solver.py`)

**Pure functions; no I/O.** `solve_month(year, month, X, fixed, avoid,
baseline, jk_target=None)` returns a dict with:
- `schedule`: `{date: name}` complete monthly assignment
- `stats_rows`: per-doctor counts (平日/週五/假日/週六/週日/QOD次數)
- `monthly_stats_map`: same data keyed by name (for `update_cumulative_stats`)
- `qod_violations`: list of `(date, name)` if QOD relaxation was needed
- `qod_relaxed`: bool — `True` means strict QOD failed, surfaced with
  red-bordered cells in the UI
- `targets`: per-CR 週五/週六/週日 targets (for the result page)

Two passes: strict QOD first; if no feasible schedule, retry with QOD
relaxed and surface every violation in `qod_violations`. Hard caps:
- CR total ≤ 7/month
- Per-category 週五/週六/週日 hard cap from balanced targets
  (`_category_target` accounts for fixed VS/中級 pre-pinned days)
- 建寬 ≤ 3 weekday
- VS slots are **always** pre-pinned via `fixed`; solver never assigns VS

Soft holiday cap (≤ 2 per CR) is **not** enforced explicitly — the
per-category caps + balanced ordering achieve the same effect, and dropping
the explicit check keeps the relaxation logic out of the hot path.

### Google Sheets (`gsheet_io.py`)

Copied verbatim from `C:\Users\dr\Downloads\Y\排班\gsheet_io.py`. Same
SHEET_ID, same `TAIWAN_HOLIDAYS` (2025+2026, including 下半年). When adding
a new year's holidays, update **both** copies — the parent project (which
has its own scripts) and this app. Long-term goal: make CV_APP the single
source of truth and have the parent scripts import from here, but for
Phase 1 they live independently.

### Templates (`templates/`)

- `login.html`, `register.html`, `admin.html` — copied verbatim from
  `Key-In-The-CVSchedule` for visual consistency. Tailwind CDN.
- `home.html` — post-login menu, 2 cards. The key 班 card is disabled with
  a "即將推出" badge until Phase 2.
- `schedule_gen.html` — the 5-step form. State is held in a global `state`
  object on the client side; sections reveal progressively as steps
  complete (`showStep(n)`). Preference selection uses clickable date
  buttons (no native date pickers — the user wanted to see all month days
  at once with holiday badges).

## Doctor roster

Defined once in `cv_solver.py`:
```python
CRS       = ["麒翔", "見賢", "常胤"]
VS_LIST   = ["廖瑀", "昭佑", "朝允", "則瑋"]
INTER_MID = ["展瀚", "建寬"]
```

When the roster changes, update `cv_solver.py` only — the UI reads the
lists from server-rendered Jinja variables (`doctors_cr`, `doctors_vs`,
`doctors_mid` in `schedule_gen.html`).

`gsheet_io.load_cumulative_stats` will return `{}` for any name not in
the 值班總數統計 sheet. The UI surfaces this as a baseline warning.

## Phase 2 plan (not yet implemented)

When integrating `Key-In-The-CVSchedule`:
1. Vendor in (or git-submodule) the keyin repo at `keyin/` and rewire its
   `app.py` routes under a `/keyin` prefix in `app.py` here.
2. Reuse `cv_solver` output to populate `vs_schedule` / `cr_schedule` for
   the keyin scheduler — VS_LIST → `vs_schedule`, CRS + INTER_MID →
   `cr_schedule`.
3. Re-enable the key 班 card in `home.html`.
4. Add a "下一步：到 key 班" button on the post-solve panel that posts the
   cached schedule into the keyin session and redirects to `/keyin`.

Soft contract for the schedule handoff:
```python
{
    "year": int, "month": int,
    "vs_schedule": {day_int: name},  # VS_LIST members only
    "cr_schedule": {day_int: name},  # CRS or INTER_MID members
    "tw_holidays": ["YYYY-MM-DD", ...],
}
```

## Things not to do

- **Do not modify `C:\Users\dr\Downloads\Y\排班\`** — that's the original
  scripts repo, kept read-only as a reference. Pull in copies; don't edit
  in place.
- **Do not modify `Key-In-The-CVSchedule`** — same reason; Phase 2 will
  vendor or submodule it without touching the upstream.
- **Do not commit `.gsa.json`, `users.json`, `.secret_key`,
  `audit_log.jsonl`** — all gitignored. Service-account credentials and
  bcrypt hashes never go to remote.
- **Do not re-run a month after `update_cumulative_stats` has applied
  it** — the cumulative tab would double-count this month into its own
  baseline. To re-run, manually subtract the month's contribution first
  or use the parent project's `rebuild_stats.py`.
