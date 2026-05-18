---
name: 202508-202603 統計 — historical cumulative snapshot tab in master Google Sheet
description: Frozen pre-202604 cumulative totals; recomputed 2026-05-18 from CV班表.xlsx so 假日 == 週六+週日
type: reference
originSessionId: c372e2b8-f409-4b52-80ce-9e11c8ea6566
---
The master Google Sheet (SHEET_ID `10ilVOmJrr8jjfnMMbtj60tAIIAe1YX3ZRU1RLgn6Elk`) has a frozen tab `202508-202603 統計` holding cumulative totals for 202508→202603, alongside live `值班總數統計`, per-month calendar tabs (`{YYYYMM}`), per-month stats tabs (`{YYYYMM} 班數統計`), and an older variant `值班總數統計_至2026/06`.

Header schema: `['姓名', '平日班(一至四)', '週五班', '週六班', '週日班', '假日班(含六日及國定假日)', '總計']`. Use prefix-match (`startswith('平日班')`, `startswith('假日班')`) when reading.

**2026-05-18 recompute (important):** the old frozen snapshot used the legacy definition `假日 = 實際週六 + 實際週日 + 國定假日(國定另計)`, so `假日 ≠ 週六+週日` (CRs were +2). It was recomputed from `CV班表.xlsx` (the hand-maintained master calendar, 8 month sheets 202508→2026032) using the **current** `make_stat_type_fn`, which folds 國定假日 into 週六/週日 by 連假 position. Now `假日 == 週六+週日` for every doctor, both in this tab and in the rebuilt `值班總數統計`. Per-doctor 總計 (平日+週五+假日) was conserved exactly vs the old tab — pure reclassification, no shifts added/dropped.

**Live state (2026-05-18):** `值班總數統計` rebuilt = new 202508-202603 統計 + 202604 + 202605 + 202606 班數統計. `202606 班數統計` was generated from the existing `202606` calendar tab. All tabs now satisfy 假日==週六+週日. `值班總數統計_至2026/06` (old-definition variant) left untouched.

**How to apply / re-run:**
- `recompute_hist.py` (project root) rebuilds the 202508-202603 frozen snapshot from CV班表.xlsx + relays into 值班總數統計. `gen_202606_stats.py` generates one month's 班數統計 from its `{YYYYMM}` calendar tab and rebuilds 值班總數統計 = snapshot + all `{YYYYMM} 班數統計` ≥ 202604 (copy/edit per month, like the parent project's per-month generate_schedule_*.py). Both: dry-run by default, `--apply` backs up to gitignored `cumulative_backup_*.json` first.
- **Sandbox blocks network**: gspread scripts fail with `getaddrinfo failed` for sheets.googleapis.com under the Bash sandbox. Run gsheet-touching scripts with `dangerouslyDisableSandbox: true` (or the user runs via `! ` prefix).
- `CV班表.xlsx` had a year typo: sheet `202602` cells for 2/9–2/15 were typed `2025` instead of `2026`. The tool auto-corrects "month matches sheet but year wrong" and logs each fix; edge-spill dates (different month, e.g. 202601 sheet's 2025-12-29) are left as real prior-month dates. `過年班` sheet is a stale duplicate — ignore it; the `{YYYYMM}` month sheet is authoritative.
- Reconstruction formula still holds: `new_cumulative[name][col] = snapshot[name][col] + Σ(monthly_tab[name][col] for each 班數統計 tab from 202604 on)`. Monthly 班數統計 tabs already have 假日=六+日.
- The classifier blocks Bash writes to the shared Sheet even after AskUserQuestion approval — user must re-authorize explicitly in a follow-up message (or run via `! ` prefix).
- See [[feedback_gsheet_authoritative.md]] — Sheet is cross-machine source of truth; always back up before overwriting frozen history.
