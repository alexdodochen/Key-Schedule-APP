# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project purpose

**CV_APP** — desktop app integrating the cardiology monthly call-schedule
workflow into a single login-protected UI.
- Phase 1: **排班 path** — generates the monthly schedule using a backtracking
  solver and writes it to Google Sheets.
- Phase 2: **key 班 path** — vendored from `Key-In-The-CVSchedule`, mounted
  at `/keyin`. Drives the NCKUH EDR system via Playwright. The post-solve
  panel of the 排班 step has a "下一步：到 key 班" button that posts the
  cached schedule into `keyin_routes.prefill_cache` then redirects to
  `/keyin`, where the form auto-hydrates.

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
`gspread`, `google-auth`, `playwright` (`python -m playwright install chromium`
once after pip install), `openpyxl`, `xlrd`.

## Architecture

### Process topology

`main.py` is the desktop entry point. uvicorn runs in a background
thread; the Qt window is the only UI. There is no multi-user server — the
QWebEngineProfile cookie store is wiped on launch to force re-login.

### Request flow (`app.py`)

**Login is currently bypassed.** Both `app.py:_get_user` and
`keyin_routes.py:_get_user` are hard-coded to return `TokenData("local",
"admin")` — every request is treated as the synthetic local admin. `/login`
GET redirects to `/`, `/logout` redirects to `/`, the websocket skips token
verification, and the home/sched/keyin templates have had their 登出 / 後台 /
Admin badge UI elements removed. To restore login, revert the two
`_get_user` helpers to their cookie/JWT versions, restore `/login` and
`/logout` handlers, and re-add the UI buttons. `auth.py` / `audit.py` and
the `register_user` / `approve_user` endpoints are still in the codebase
but are no longer reachable through the UI.

Original design (preserved for reference) — cookie-based JWT auth, same
scheme as `Key-In-The-CVSchedule`:
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
- `POST /api/sched/handoff-to-keyin` — bridge to Phase 2: splits the cached
  schedule into `vs_schedule` / `cr_schedule` by doctor pool, attaches
  `tw_holidays` for the month, stashes into `keyin_routes.prefill_cache`,
  returns `{ok, redirect:"/keyin"}`. The cv_solver `_solve_cache` is the
  source of truth — call only after `/api/sched/solve` has run.

`keyin_routes.py` mounts under `/keyin` via `app.include_router(...,
prefix="/keyin")`. Endpoints (mirror upstream `Key-In-The-CVSchedule`):
- `GET /keyin` — `keyin_index.html` (5-section form)
- `GET /keyin/api/prefill` — one-shot pop of the prefill payload
- `POST /keyin/api/upload-schedule` — Excel parse (`keyin_excel_parser`)
- `POST /keyin/api/preview` — build the day-by-day shift list
- `POST /keyin/api/start | continue | cancel`
- `GET /keyin/api/status`
- `WS /keyin/ws`

Login/register/admin live in `app.py` and are shared — `keyin_routes.py`
does not redefine them. `auth.py` and `audit.py` were verified
byte-identical to the keyin upstream copies; `keyin_routes.py` imports
them directly.

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

## Phase 2 implementation notes

- The `keyin_scheduler.SchedulerSession` keeps **one** session at module
  scope in `keyin_routes.py` (mirrors upstream); a second `/keyin/api/start`
  while a session is in `starting` / `waiting_login` / `running` is rejected
  rather than queued. Do not change this without redesigning `manager` /
  `session` to be keyed.
- The Playwright runner imports `playwright.async_api` lazily inside
  `_run` — uvicorn boots fine without playwright installed; the import
  fails only when a user clicks ▶ 開始排班.
- `keyin_routes.prefill_cache[username]` is one-shot: `GET /keyin/api/prefill`
  pops it. Refreshing the keyin page after consuming the prefill returns
  an empty form, which is intentional — the user has already hydrated.
- When the doctor roster changes, update `cv_solver.CRS / VS_LIST /
  INTER_MID`. The keyin form's `DOCTORS_VS` / `DOCTORS_R` constants in
  `templates/keyin_index.html` are independent (line ~270 area) — they
  populate the autocomplete combos for the white-day rotation lists. They
  do not need to match `cv_solver` exactly (white-day rotations include
  doctors outside the night-shift pool), but if you add a new CR/VS who
  may take night shifts via the solver, also add them to the keyin combos.

Schedule handoff payload (frozen contract — see
`/api/sched/handoff-to-keyin` and `loadPrefill()` in `keyin_index.html`):
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
- Re-running a month is **safe** — `/api/sched/write` reads the existing
  `{YYYYMM} 班數統計` tab via `gsheet_io.read_monthly_stats` to recover the
  previous run's per-doctor contribution, subtracts it from the cumulative
  baseline, then adds the fresh `monthly_stats_map`. The 班數統計 tab and
  the 值班總數統計 tab are written in the same `/api/sched/write` call, so
  the two are always consistent; cross-machine coordination needs nothing
  extra because both clients read/write the same Google Sheet.
- A human-readable JSON copy of every successful write is dropped into
  `schedule_history/{YYYYMM}.json` for traceability — **not** consulted by
  the subtract logic, so it can drift without affecting cumulative
  correctness. `git commit && git push` it manually if you want a versioned
  history outside the Sheet.
