---
name: CR holiday balance rule (3-3-2 with cumulative-aware tie-break)
description: When CR holiday total > 6 in a month, distribute as evenly as possible across the 3 CRs; the highest cumulative 假日 CR gets the smallest count
type: feedback
originSessionId: 8546fe78-7a61-4643-897f-4214a681daea
---
When the month has more than 6 CR-eligible holiday slots, distribute them across the 3 CRs (麒翔/見賢/常胤) as evenly as possible. Example: 8 holidays → 3-3-2. The CR who takes the smaller share (e.g. 2 in a 3-3-2 split) is the one with the HIGHEST cumulative 假日 count in `值班總數統計`. The CRs who take the larger share (the 3s) are the ones with LOWER cumulative 假日.

**Why:** Load-balance across the year, not just the month. A CR who already has many holiday shifts cumulatively should get spared this month so the others catch up.

**How to apply:**
- Constraint scope: total CR holidays = `is_taiwan_holiday(d)` slots NOT pre-assigned to VS/中級. Excludes 平日 / 週五 — those follow per-category targets already.
- Target per CR = floor(total / 3) for everyone, plus +1 for the (total % 3) CRs with the LOWEST cumulative `假日`.
- Same shape as the existing `_category_target(stat_label, cum_key)` logic in `cv_solver.py`, but keyed on `is_taiwan_holiday` rather than a single stat_type.
- Per-category 週六/週日 targets stay; the new 假日 total target is an additional hard cap on `cr_h[name]`.
- When ≤ 6 CR holidays, the existing 2-2-2 default is fine; rule activates at > 6.
