# T1 Tool-Change Impact: Full Reconciliation Plan

**Date:** 2026-03-13
**Context:** Bug discovered: `detect_nozzle_heat_toggle()` heats T1 but never selects it as the active tool.
All 7 calibration runs have heat_halo data from T1-at-wrong-position. Impact audit across all affected files.

---

## The Critical Design Insight

After adding the tool-change fix, T0 and T1 are BOTH positioned at world `(wx, wy)`.
The H matrix maps that world point to the same pixel regardless of which nozzle is active.
So T0 and T1 thermal halos appear at the **same pixel** — T0-T1 distance ≈ 0px.

Current guard: `< 15px → discard`. This guard fires for EVERY correctly-measured point.

**Guard semantics must flip:**
- `< 15px` BEFORE fix = "T1 never moved" → discard (correct then)
- `< 15px` AFTER fix = "both nozzles at same point, confirmed" → ACCEPT (correct after fix)

New thresholds:
| T0-T1 pixel distance | After fix meaning | Action |
|---------------------|------------------|--------|
| `< 30px` | Both nozzles confirmed at same world point | ACCEPT T0 result, boost conf x1.2 |
| `≥ 30px` | One detection on an artifact | DISCARD, fall through to pre-halo best |

Without this guard flip, heat_halo STILL produces zero results even after the tool-change fix.

---

## Impact Inventory

### I1 — `vision_notes.md` (session file) — Two False Claims

**False claim A (line 84):**
`[VERIFIED: empirical — heat_halo T0/T1 detections]` for ~50mm X separation.
T1 was NEVER active during heat_halo. This comes from H2D hardware specs, not empirical measurement.

Fix: Change to `[PROVISIONAL: ~50mm X from H2D hardware specs; re-verify empirically via nozzle_compare.py after Task C]`

**False claim B (T0-T1 Thermal Separation table, lines 146-158):**
All values (29px, 39px, etc.) measured with T1 NOT at the calibration point.
Values represent T0-at-(wx,wy) vs T1-heater-at-offset, not both nozzles at same world point.

Fix: Mark table `[INVALID — all values measured with T1 never active; discard; re-measure after Task C]`

**Valid data that remains good:**
- B005 heat_halo T0 result (conf=1.0) — T0 detection is unaffected
- G28 homing data (3 trials, 46.5-46.9s)
- SHELL/SYNBOT corner pixel coordinates
- All other non-T1 calibration facts

---

### I2 — `corner_calibration.py` — 4 Issues

**Issue A (PRIMARY BUG): T1 never active in `detect_nozzle_heat_toggle()`**
Lines 627-670: sends `M104 T1 S180` but never calls toggle_active_tool.
Fix: Task B — full restructure with proper toggle API calls.

**Issue B (BLOCKER): T0-T1 guard semantics inverted after fix**
Lines 1117-1130: `if t0_t1_dist < 15.0: discard` becomes wrong after tool-change fix.
Fix: New guard — accept if < 30px (confirmed), discard if >= 30px (artifact).

**Issue C (data integrity): FIXED_POINTS comments from buggy runs**
```python
FIXED_POINTS = {
    "R175": (527, 427),  # T0-T1=13px - measured with T1 never active (INVALID reason)
    "F345": (511, 513),  # T0-T1=10px - measured with T1 never active (INVALID reason)
}
```
After fix + guard flip, the old FIXED_POINTS guard never fires anyway (< 15px now ACCEPTS).
Remove R175 and F345 from FIXED_POINTS — allow heat_halo T0 to run normally here.

**Issue D (minor): `__heat_halo_T1` storage label misleading**
Line 1155: stored T1 pixels were never valid. After fix, T1 data will be valid — update comment.

---

### I3 — Task B: `corner_calibration.py` Full Spec

New constants:
```python
TOOL_CHANGE_SETTLE_SECONDS = 5   # settle after toggle_active_tool before G0
```

New helper:
```python
def toggle_active_tool():
    r = requests.patch(f"{API_BASE}/toggle_active_tool?{PRINTER_PARAM}",
                       headers=_auth_headers(), verify=False, timeout=10)
    r.raise_for_status()
    time.sleep(TOOL_CHANGE_SETTLE_SECONDS)
```

`detect_nozzle_heat_toggle()` signature change:
```python
def detect_nozzle_heat_toggle(expected_px, name, output_dir, world_xy: tuple) -> dict:
```

Sequence (T0 active on entry and exit):
1. Capture idle baseline at T0 position
2. Test T0: T0 already active, heat, detect, cool
3. `toggle_active_tool()` (T0 -> T1)
4. `G0 X{wx} Y{wy}` (T1 tip now at wx, wy)
5. Re-capture idle at T1 position (MANDATORY - scene shifted)
6. Test T1: heat, detect, cool
7. `toggle_active_tool()` (T1 -> T0)
8. `G0 X{wx} Y{wy}` (T0 back at wx, wy)

Guard redesign:
```python
if t0_t1_dist < 30.0:
    t0["conf"] = min(t0["conf"] * 1.2, 1.0)
    # confirmed: both nozzles independently detected same pixel
else:
    t0 = {}  # diverged: artifact contamination, fall through
```

Call site (~line 1112) - add world_xy param:
```python
heat_results = detect_nozzle_heat_toggle(
    expected_px=expected, name=name, output_dir=OUTPUT_DIR,
    world_xy=(wx, wy),
)
```

---

### I4 — `nozzle_compare.py` — Task C

Issues:
1. `send_gcode("T0")` line 183, `send_gcode("T1")` line 203 — raw gcode vs API
2. No idle baseline re-capture after tool change
3. Potentially no explicit G0 re-position after T1 toggle

Note: `send_gcode("T1")` IS functionally correct on H2D firmware (Marlin T1 = tool select).
This is an architectural issue, not a functional bug. However fix it anyway per MCP API standards.

Fix:
- Replace both raw gcode calls with `requests.patch(f"{API_BASE}/toggle_active_tool?{PRINTER_PARAM}")`
- Add `G0 X{CENTER_X} Y{CENTER_Y}` re-position after each toggle
- Add per-tool idle re-capture before heating each nozzle

---

### I5 — `plate_corner_repeatability.py` (session file) — Low Priority

Line 795: `send_gcode("T1")` - raw gcode.
Functionally correct (Marlin T1 = tool select). Update when file is next touched.

---

### I6 — `behavioral_rules_camera_calibration.py` — Task A (Already Specified)

No new items. The dual-extruder section in plan.md fully captures:
- T0/T1 physical separation
- Active tool determines which tip is at commanded XY
- Heating != moving the carriage
- Per-nozzle idle baseline mandatory
- State invariant (T0 on entry and exit)
- expected_px same for both nozzles

---

## Consolidated Task Order

| # | Task | File | Priority |
|---|------|------|----------|
| N1 | Correct false [VERIFIED] + invalidate T0-T1 table | `vision_notes.md` | 1 (fast, no commit) |
| A | Add dual-extruder section to knowledge module | `behavioral_rules_camera_calibration.py` | 2 |
| B | Tool-change fix + guard flip + FIXED_POINTS removal | `corner_calibration.py` | 3 |
| C | Raw gcode -> API, re-position, idle re-capture | `nozzle_compare.py` | 4 |
| R8 | Run 8 calibration | printer (physical) | 5 (blocked on B) |
| R9 | Run nozzle_compare.py, get valid T0-T1 data | printer (physical) | 6 (blocked on C) |
| N1b | Update vision_notes.md with real [VERIFIED] T0-T1 data | `vision_notes.md` | 7 (blocked on R9) |

### Critical Path
```
N1 (data hygiene)
  -> Task A (knowledge, no code impact)
    -> Task B (corner_calibration: tool-change + guard flip + FIXED_POINTS)
      -> Run 8 (first run with correct T1 behavior — heat_halo can contribute)
        -> Task C (nozzle_compare.py fix)
          -> Run nozzle_compare.py -> valid T0-T1 pixel/world offset
            -> N1b: update vision_notes.md with real data
```

---

## Prior H Matrices — Status

All 7 H matrices: **not corrupted by T1 bug.** heat_halo was discarded (via T0-T1 guard)
before any T1 pixel could enter the DLT solve. Matrices are wrong only for previously documented reasons.

Run 6 result (reproj=8.62px, 5 inliers) in `~/.bambu-mcp/calibration/H2D.json` remains the
`expected_px` seed for run 8.

Run 8 will be the FIRST run where heat_halo can contribute to the H solve.

---

## Open Questions

1. **TOOL_CHANGE_SETTLE_SECONDS = 5s** — conservative. Can reduce if firmware responds faster.
2. **T0-T1 divergence threshold 30px** — at X=345 right column, 50mm world compresses to ~50px.
   30px threshold allows ~30mm world divergence before discarding. Reasonable starting point.
3. **FIXED_POINTS full removal vs keep-as-fallback** — proposal is full removal (allow heat_halo T0).
   If heat_halo T0 fails in front-right compressed zone, the existing cascade (sparse_bright, top_pct)
   handles it. FIXED_POINTS hardcodes stale pixel values and should not be used.
