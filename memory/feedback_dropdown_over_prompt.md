---
name: For picking from saved items, use dropdown selectors not browser prompt()
description: Whenever the UI lists user-saved items (drafts, presets, history), select via a dropdown with formatted labels; never `prompt('輸入編號')`
type: feedback
originSessionId: c372e2b8-f409-4b52-80ce-9e11c8ea6566
---
When the UI presents a list of items the user previously saved (drafts, presets, named snapshots, etc.), the picker must be a `<select>` dropdown showing formatted labels, not a JavaScript `prompt()` that asks the user to type an index number.

**Why:** The user explicitly said 「草稿用下拉選單選取 我喜歡」 after seeing the key 班 draft bar's dropdown style, then asked for the 排班 path to match. Browser `prompt()`-based pickers feel clunky, hide info behind plain text indices, and break the visual flow.

**How to apply:**
- Pattern: `<input id="name" placeholder="名稱">` + `<select id="select">` with one `<option value="">— 選 —</option>` placeholder + one option per saved item.
- Option label should pack the key facts: `{name} ({year}/{month} · {extra} · {saved_at})` so user can scan without expanding.
- Refresh the `<select>` after every save / delete, preserving the current selection.
- When user picks from dropdown, auto-copy the value into the name input so load / delete operate on a single target.
- Apply to all save/load UIs in this codebase (排班 ✓, key 班 ✓). Apply the same pattern to any future save/load feature.
- Status pill (green ✓ / red ✗ / gray …) goes in the same bar for inline feedback — no `alert()`.
