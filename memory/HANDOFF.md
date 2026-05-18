============================================
  HANDOFF — Last Updated: 2026-05-18 12:10
============================================

[What this session did]
  1. Diagnosed + fixed 累計 假日 ≠ 週六+週日: recomputed 202508-202603 統計
     from CV班表.xlsx via current stat logic (recompute_hist.py). Generated
     202606 班數統計 + rebuilt 值班總數統計 (gen_202606_stats.py). All tabs
     now 假日==六+日; pushed (057766b).
  2. NEW FEATURE — Step 5 manual schedule edit: solver output is now
     hand-editable per-day before write. cv_solver.recompute_from_schedule
     + POST /api/sched/apply-edits (overwrites cache → 寫入/交key班 use the
     edited result) + editable <select> calendar with apply/revert buttons.
     Tested (unit + TestClient e2e + /sched render). Documented in CLAUDE.md.

[Current state]
  - Branch: main. 057766b pushed. Edit-feature commit pending (not yet
    committed at handoff write — commit + push next).
  - Google Sheet: 202508-202603 統計 / 202606 班數統計 / 值班總數統計 all
    correct & live (假日==六+日). 值班總數統計_至2026/06 untouched.
  - Pre-existing uncommitted (prior sessions, NOT this session): app.py,
    gsheet_io.py, main.py, templates/home.html, sheet_viewer.html — left
    as-is. This session also modified app.py + CLAUDE.md (edit feature) +
    cv_solver.py + templates/schedule_gen.html.

[Next steps]
  - Commit the edit feature (cv_solver.py, app.py, templates/schedule_gen.html,
    CLAUDE.md) + push (needs explicit user PUSH auth — classifier blocks main).
  - Manual UI sanity check in the desktop app: solve → tweak a cell → 套用
    手調並重算 → stats/projection refresh → 寫入 uses edited version.

[Known issues / blockers]
  - Bash sandbox blocks network → gspread scripts need
    dangerouslyDisableSandbox:true.
  - Auto-mode classifier blocks shared-Sheet writes + push-to-main even
    after AskUserQuestion; needs explicit textual re-authorization.

[Don't repeat these mistakes]
  - Don't bundle prior sessions' uncommitted app.py/main.py/etc. into this
    session's commits — scope commits to this session's files only.
  - CV班表.xlsx sheet 202602 had 2025/2 year typo for 2026/2; 過年班 sheet
    is a stale duplicate (ignore).

[Relevant files]
  - cv_solver.py — recompute_from_schedule (new pure fn)
  - app.py — /api/sched/apply-edits, _build_projection helper
  - templates/schedule_gen.html — editable Step-5 calendar + buttons
  - recompute_hist.py / gen_202606_stats.py — sheet rebuild tools

[Important memory files]
  - reference_sheet_historical_snapshot_tab.md (recompute tools + sandbox note)
