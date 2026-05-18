---
name: Show explicit math components in projections, not just net delta
description: When a UI cell displays a computed value with multiple input components, render each component so the user can visually verify; never reduce to a single delta
type: feedback
originSessionId: c372e2b8-f409-4b52-80ce-9e11c8ea6566
---
For any UI element that projects a future state via a multi-component formula (e.g. `projected = baseline − prev_contribution + new_contribution`), the rendering MUST surface all input components alongside the result, not just a single net delta.

**Why:** The user reported a "bug" in the 預估值班總數統計 projection. The server math (`baseline − prev + new`) was actually correct, but the cell only showed the projected value plus a net `(new − prev)` delta. With only the net delta visible, the user couldn't tell whether `+new` was actually being added — they suspected only `−prev` was applied. Verification by trust ≠ verification by inspection.

**How to apply:**
- For projection / preview tables that combine 2+ source values into one cell, render the components inline. Example layout: projected value on top (bold), `base −prev +new` underneath in small gray with red/green color coding.
- Return all components from the backend as explicit fields (`baseline`, `prev_contribution`, `new_contribution`) — don't pre-collapse to a delta server-side.
- Add a top-of-table banner that spells out the formula in plain Chinese ("預估 = 累計 − 舊版本月 + 新版本月").
- Reuse this pattern whenever a re-write / re-run scenario subtracts an old contribution and adds a new one.
- A pure-numeric delta hides bugs; explicit per-component display surfaces them visually.
