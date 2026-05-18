---
name: Google Sheet is the cross-machine source of truth
description: When working from a fresh clone or new machine, always read state from the master Google Sheet, never trust local files
type: feedback
originSessionId: 8546fe78-7a61-4643-897f-4214a681daea
---
For CV_APP (排班 Key班APP) the master Google Sheet at https://docs.google.com/spreadsheets/d/10ilVOmJrr8jjfnMMbtj60tAIIAe1YX3ZRU1RLgn6Elk/edit is the ONLY source of truth across machines. SHEET_ID `10ilVOmJrr8jjfnMMbtj60tAIIAe1YX3ZRU1RLgn6Elk` is constant across all clones.

**Why:** User works on multiple machines. Local artifacts get out of sync — `schedule_history/*.json` snapshots can be stale, `users.json` only exists on the machine that registered, `.gsa.json` is gitignored so a fresh clone has none. The Google Sheet is what's actually consulted at runtime by `gsheet_io.load_cumulative_stats` (baseline) and `gsheet_io.read_monthly_stats` (rewrite-safe subtraction). If a memory or local file disagrees with the sheet, trust the sheet.

**How to apply:**
- Fresh clone setup: copy `.gsa.json` from the parent project at `C:\Users\dr\Downloads\Y\排班\.gsa.json` (same `admission-bot@…iam.gserviceaccount.com` service account). Without it, baseline reads fail and the UI shows "⚠️ 無法連 Google Sheet（baseline 全 0 帶入）".
- Never reason about cumulative totals from `schedule_history/*.json` — that file is for human traceability only and is NOT consulted by `update_cumulative_stats`.
- When debugging baseline / cumulative mismatches, open the actual sheet (`值班總數統計` tab) before believing any local read.
- Every machine's app reads from and writes to the same sheet, so cross-machine coordination needs nothing extra beyond the credential.
