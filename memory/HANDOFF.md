============================================
  HANDOFF — Last Updated: 2026-05-18 10:45
============================================

[What this session did]
  1. Diagnosed why 累計 假日 ≠ 週六+週日: old frozen 202508-202603 統計 used
     legacy def (假日 = 實六+實日+國定另計). Current solver folds 國定 into
     六/日 so 假日==六+日. Mismatch was pure definition-boundary legacy.
  2. Recomputed 202508-202603 統計 from CV班表.xlsx via current stat logic
     (recompute_hist.py). 243/243 days, per-doctor 總計 conserved vs old.
     Fixed a year typo in CV班表.xlsx sheet 202602 (2/9–2/15 typed 2025).
  3. Generated 202606 班數統計 from existing 202606 calendar tab; rebuilt
     值班總數統計 = new snapshot + 202604 + 202605 + 202606 (gen_202606_stats.py).
  4. All target tabs now satisfy 假日==週六+週日.

[Current state]
  - Branch: main, ahead of origin (was ahead 2 at session start + new work)
  - Google Sheet: 202508-202603 統計, 202606 班數統計, 值班總數統計 all
    rewritten & verified live. 值班總數統計_至2026/06 untouched (old variant).
  - Backups: cumulative_backup_20260518T102656.json (snapshot rebuild),
    cumulative_backup_20260518T103650.json (202606 rebuild) — both gitignored.
  - New tools: recompute_hist.py, gen_202606_stats.py (renamed from _ scratch).

[Next steps]
  - User to `git push origin main` (auto-mode blocks push to main; also still
    carries pending 97e2d5f + 0364c4f from prior session per old handoff).
  - For future months: copy gen_202606_stats.py → gen_{YYYYMM}_stats.py,
    edit YEAR/MONTH/tab names, dry-run, then --apply (needs user auth +
    dangerouslyDisableSandbox / `! ` prefix).

[Known issues / blockers]
  - Bash sandbox blocks network → gspread scripts need
    dangerouslyDisableSandbox:true (getaddrinfo failed otherwise).
  - Auto-mode classifier blocks shared-Sheet writes even after
    AskUserQuestion approval — needs explicit textual re-authorization.

[Don't repeat these mistakes]
  - Don't trust CV班表.xlsx dates blindly — sheet 202602 had 2025/2 typo'd
    for 2026/2; recompute_hist.py auto-corrects month-match/year-wrong only.
  - 過年班 sheet in CV班表.xlsx is a stale duplicate — ignore; the {YYYYMM}
    month sheet is authoritative.
  - Don't touch frozen history without backup + dry-run sign-off first.

[Relevant files]
  - recompute_hist.py — rebuild 202508-202603 統計 from CV班表.xlsx
  - gen_202606_stats.py — gen one month 班數統計 + rebuild cumulative
  - CV班表.xlsx — hand-maintained master calendar (historical source)

[Important memory files]
  - reference_sheet_historical_snapshot_tab.md (updated — new def, tools, sandbox note)
