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

- **Desktop launch:** `CV_APP 心臟內科排班整合.bat` → `python main.py`. Spawns uvicorn on
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
- `GET /api/update/check` — multi-machine sync. Runs `git fetch origin <branch>`
  then computes ahead/behind counts vs `origin/<branch>`. Also surfaces local
  uncommitted changes via `git status --porcelain` so the UI can warn before
  attempting a pull. Returns `{ok, branch, current, remote, behind, ahead,
  behind_commits, ahead_commits, dirty, dirty_files}`. Auto-invoked when
  `home.html` loads; powers the 更新 button (latest / N behind / error states).
  No-git / not-a-repo / network failure are all surfaced cleanly via `error`.
- `POST /api/update/pull` — refuses if the working tree is dirty (to protect
  in-progress work). Otherwise runs `git pull --rebase origin <branch>` and
  reports the new commits. Templates auto-reload via Jinja, but Python code
  changes (`app.py`, `cv_solver.py`, `gsheet_io.py`…) need a manual app
  restart — the UI prompts for this whenever `restart_required` is true.
- `GET /` → `home.html` (post-login menu — 排班 active, key 班 disabled)
- `GET /sched` → `schedule_gen.html` (5-step UI)
- `POST /api/sched/init` — load month context (H, W, baseline, calendar)
- `POST /api/sched/compute` — given X (and optional `vs_holiday_exempt:
  [name, ...]`), return VS/建寬 counts + CR target totals. Exempt VS names are
  excluded from the holiday round-robin; their `vs_per_doctor[name].holiday`
  is forced to 0 and the shortfall is absorbed into `cr_holiday_total`.
- `POST /api/sched/solve` — run backtracking solver, cache result, return
  preview (calendar + stats + QOD violations + targets + `projected_cumulative`).
  Body also accepts `vs_holiday_exempt: [name, ...]` (passed through to the
  fast-fail `compute_initial_targets`). `projected_cumulative` is a list of
  per-doctor rows showing the cumulative tab AFTER writing this month. Each
  row carries four explicit fields so the math is verifiable end-to-end:
  `baseline[col]` (current cumulative), `prev_contribution[col]` (old version
  of this month's contribution, read from `{YYYYMM} 班數統計` — 0 if first
  write), `new_contribution[col]` (this run's solver output), and the per-col
  projected value `row[col] = baseline - prev_contribution + new_contribution`.
  `had_prev_monthly` is True when `{YYYYMM} 班數統計` already exists; the UI
  uses it to switch the banner text between first-write vs rewrite. The Step 5
  table renders the math explicitly per cell (`projected` on top, `base
  −prev +new` underneath) so the user can spot any off-by-prev bug visually.
- `POST /api/sched/write` — write cached schedule to Google Sheet (calendar
  tab + monthly stats tab + cumulative stats tab)
- `POST /api/sched/handoff-to-keyin` — bridge to Phase 2: splits the cached
  schedule into `vs_schedule` / `cr_schedule` by doctor pool, attaches
  `tw_holidays` for the month, stashes into `keyin_routes.prefill_cache`,
  returns `{ok, redirect:"/keyin"}`. The cv_solver `_solve_cache` is the
  source of truth — call only after `/api/sched/solve` has run.
- `POST /api/sched/save-draft` — writes the current UI state (any of step 1-5)
  to `sched_drafts/{name}.json`. Default `name = {YYYYMM}`. Body:
  `{name, step, state}`. The state blob holds `year/month/calendar/baseline/
  prevTail/H/W/X/targets/prefs/lastSolve/prevYear/prevMonth`. One draft per
  name; saving overwrites.
- `GET /api/sched/list-drafts` — lists drafts (newest first). Each entry:
  `{name, saved_at, saved_by, step, year, month}`.
- `POST /api/sched/load-draft` — `{name}` → returns the saved blob; UI rehydrates
  state and jumps to the saved step. The fresh-clone case (calendar / baseline
  embedded in the draft) means no Google Sheet round-trip is required.
- `POST /api/sched/delete-draft` — `{name}` → unlinks the file.

`keyin_routes.py` mounts under `/keyin` via `app.include_router(...,
prefix="/keyin")`. Endpoints (mirror upstream `Key-In-The-CVSchedule`):
- `GET /keyin` — `keyin_index.html` (5-section form)
- `GET /keyin/api/prefill` — one-shot pop of the prefill payload
- `POST /keyin/api/upload-schedule` — Excel parse (`keyin_excel_parser`)
- `POST /keyin/api/preview` — build the day-by-day shift list
- `POST /keyin/api/start | continue | cancel`
- `GET /keyin/api/status`
- `WS /keyin/ws`
- `POST /keyin/api/save-draft` — writes the current form state to
  `keyin_drafts/{name}.json` (default name = `{YYYYMM}`, payload =
  `collectConfig()` output + parsed-but-not-yet-applied Excel snapshot
  in `parsed_vs` / `parsed_cr`). One file per name; saves overwrite.
- `GET /keyin/api/list-drafts` — newest first; each entry has
  `{name, saved_at, saved_by, year, month}`.
- `POST /keyin/api/load-draft` — `{name}` → returns the saved blob; the
  UI rehydrates year/month → re-runs `generateCalendar()` → re-fills
  per-day VS/CR/holiday → rotation lists → upload-preview table.
- `POST /keyin/api/delete-draft` — `{name}` → unlinks the file.

Login/register/admin live in `app.py` and are shared — `keyin_routes.py`
does not redefine them. `auth.py` and `audit.py` were verified
byte-identical to the keyin upstream copies; `keyin_routes.py` imports
them directly.

### Solver (`cv_solver.py`)

**Pure functions; no I/O.** `solve_month(year, month, X, fixed, avoid,
baseline, jk_target=None, seed=None, prev_tail=None)` returns a dict with:
- `schedule`: `{date: name}` complete monthly assignment
- `stats_rows`: per-doctor counts (平日/週五/假日/週六/週日/QOD次數)
- `monthly_stats_map`: same data keyed by name (for `update_cumulative_stats`)
- `qod_violations`: list of `(date, name)` — minimised QOD pairs (each entry
  is the EARLIER date of a pair; UI expands to highlight both ends)
- `qod_relaxed`: bool — `True` means strict (`max_qod=0`) was infeasible
  and the solver had to relax. The number of violations is still minimised.
- `max_qod`: int — the minimum budget that yielded a feasible schedule.
- `targets`: per-CR 週五/週六/週日 targets (for the result page)

**Constraint design** (see `memory/project_solver_design.md`):
- **CR 假日總數 hard cap (`_holiday_target`)** — when total CR-eligible
  holidays > 6, distribute across 3 CRs as evenly as possible (e.g. 8 → 3-3-2).
  The CR with the HIGHEST cumulative `假日` in baseline gets the smallest
  share; surplus goes to the LOWEST cumulative CRs. Same shape as
  `_category_target`, but keyed on `is_taiwan_holiday` rather than a single
  stat_type. Enforced as a hard cap in the candidate filter alongside
  `cr_fri_target` / `cr_sat_target` / `cr_sun_target`. With consistent
  baseline ordering across 假日 vs 週六 vs 週日, the four caps are
  satisfiable simultaneously (sat+sun = 假日 by construction).
- **QOD 豁免名單** `QOD_EXEMPT_NAMES = set(VS_LIST) | {"展瀚", "建寬"}` —
  these names bypass the QOD-pair / back-to-back hard rules in 5 places
  (`qod_score`, candidate back-to-back filter, `fixed_pairs` precount,
  `_scan_qod`, `_compute_stats`). Only CRs (麒翔/見賢/常胤) are constrained.
- **最少 QOD 放寬** — `for max_qod in range(QOD_RELAX_CAP + 1)` from 0
  upward; first feasible budget wins. `qod_used` budget pruning inside
  backtracking. No more "strict / relaxed" binary — `qod_violations` count
  is always the minimum given other hard rules.
- **跨月約束** — `prev_tail: dict[date, str]` (last 2 days of previous month)
  is passed in; `neighbor_doctor(target_idx)` falls back to `prev_tail` for
  `target_idx < 0` so back-to-back / QOD checks span the boundary.
  Pre-counted cross-month QOD pairs in `fixed_pairs`. Read via
  `gsheet_io.read_calendar_tail(sheet, year, month, n=2)`.
- **重跑必差異** — three layers of randomness so re-running `solve_month`
  produces visibly different feasible schedules: (a) `rng.shuffle(open_days)`,
  (b) `rng.uniform(0, 1.49)` jitter on balance term in `sort_key`, (c)
  `rng.random()` final tiebreak. Jitter is intentionally capped at ±1.5 —
  larger ranges (tested ±3) blow up the search space on edge cases.

Hard caps (still enforced):
- CR total ≤ 7/month
- Per-category 週五/週六/週日 hard cap from balanced targets
  (`_category_target` accounts for fixed VS/中級 pre-pinned days)
- 建寬 ≤ 3 weekday
- VS slots are **always** pre-pinned via `fixed`; solver never assigns VS

Soft holiday cap (≤ 2 per CR) is **not** enforced explicitly — the
per-category caps + balanced ordering achieve the same effect, and dropping
the explicit check keeps the relaxation logic out of the hot path.

### Google Sheets (`gsheet_io.py`)

Originally copied verbatim from `C:\Users\dr\Downloads\Y\排班\gsheet_io.py`,
now diverged with CV_APP-only additions. Same SHEET_ID, same
`TAIWAN_HOLIDAYS` (2025+2026, including 下半年). When adding a new year's
holidays, update **both** the parent project's copy and this one — plus
`TAIWAN_HOLIDAY_NAMES` (CV_APP-only, used to label `端午節` etc. in the
preview calendar).

CV_APP-only helpers (don't backport without the same need):
- `taiwan_holiday_name(d) -> str` — returns the holiday's Chinese name
  (empty for weekends or non-holidays).
- `previous_year_month(year, month) -> (year, month)` — handles the
  January boundary.
- `read_calendar_tail(sheet, year, month, n=2) -> dict[date, str]` —
  parses a `{YYYYMM}` calendar tab and returns the last `n` filled days.
  Used by `/api/sched/init` to feed the solver's cross-month constraint.
  Returns `{}` if the tab doesn't exist (first time using app).

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
  - **Step 2** surfaces a rose-coloured info box if `prev_tail` is non-empty,
    listing the previous month's last days and the auto-derived exclusions
    (`6/1 不可排 X` etc.). Holiday names (端午節 …) are shown beneath each
    day in the preview calendar.
  - **Step 4** shows a real-time `已選 vs 應選` chip next to each
    VS/展瀚/建寬 row (green when matching, red bold when not). `validatePrefs()`
    runs on solve-click; mismatches trigger a confirm dialog with override.
  - **Selected day-button styling** uses `linear-gradient(rgba(...))` overlay
    so the underlying yellow (holiday) / white (weekday) bg stays visible.
  - **QOD highlight**: `.cal-cell.qod` class (NOT `.cal-cell .qod` — that
    descendant selector was a bug) gives a 3 px solid red border. Frontend
    expands `qod_violations` to mark BOTH ends of each pair (`addDays(date, 2)`).
  - **「重新跑 solver」按鈕** clears the result calendar / stats / target
    table before triggering a fresh `/api/sched/solve` call, so the user
    visibly sees a "clear → refill" rather than wondering if anything changed.
  - **Step 5 stats tables grouping** — both `#result-stats` (班數統計) and
    `#projected-cum-table` (預估累計) render through `renderGroupedRows(...)`
    keyed off `DOCTOR_GROUPS = [CR: [常胤, 見賢, 麒翔], 中級: [展瀚, 建寬],
    VS: DOCTORS_VS]`. CR order is **常胤 → 見賢 → 麒翔** (user preference, not
    cv_solver.CRS order). Group header rows use `bg-gray-50` and `colspan` the
    full width. Font is `text-sm` (14px), not `text-xs`.
  - **Step 3 VS 假日豁免 checkbox** — VS 表多一「不值假日」欄。Toggle 觸發
    `onVsExemptChange` → rebuild `state.vsHolidayExempt` → call
    `recomputeTargets()` → server returns updated `vs_per_doctor` (exempt names
    forced to 0 holiday) → re-render. `state.vsHolidayExempt` is persisted in
    drafts. Step 4 「應選」 chips automatically reflect the override because they
    read off `state.targets.vs_per_doctor`.
  - **Step 5 預估累計表** — `#projected-cum-table`. After solve, server returns
    `projected_cumulative` rows; UI renders `baseline_col(+monthly_contribution)`
    per cell, highlights touched rows with `bg-amber-50`, and surfaces a
    `projected-note` banner if `had_prev_monthly=True` (re-write case).
  - **Step 3 「CR 預估值班總數」面板** — populated by `compute_initial_targets`
    output: `cr_holiday_total / cr_weekday_total / cr_total / cr_per_avg /
    cr_per_doctor`. Shows the post-VS leftover (so user can see "after VS,
    展瀚, 建寬, CRs need to cover N shifts"). Per-CR holiday split uses the
    same balance rule as `_holiday_target` so the projection matches what
    the solver will produce (modulo fixed assignments).
  - **草稿橫條（draft-bar）** at the top of the page — 💾 存目前進度 / 📂
    讀草稿 / 🗑 刪草稿. Saves the entire UI state (any of step 1-5) to
    `sched_drafts/{name}.json` and lets the user resume next time.
    `_currentStep` is tracked via `showStep()` so the saved draft jumps
    back to the same step on load. Calendar / baseline / prevTail are
    serialized in the draft itself so loading does not require a Google
    Sheet fetch (works fully offline once saved).

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
- **Cross-machine setup** — `.gsa.json` is gitignored; a fresh clone has
  none. Copy it from the parent project at `C:\Users\dr\Downloads\Y\排班\
  .gsa.json` (same `admission-bot@…iam.gserviceaccount.com` Service Account).
  Without it, `/api/sched/init` falls into the exception branch and the UI
  shows "⚠️ 無法連 Google Sheet（baseline 全 0 帶入）". The Google Sheet
  itself (SHEET_ID `10ilVOmJrr8jjfnMMbtj60tAIIAe1YX3ZRU1RLgn6Elk`) is the
  cross-machine source of truth — local `schedule_history/` snapshots can
  drift, the sheet's `值班總數統計` and `{YYYYMM} 班數統計` tabs are
  authoritative.
