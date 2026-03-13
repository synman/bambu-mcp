# Tool-Change Fix + Knowledge Update Plan

**Date:** 2026-03-13  
**Scope:** bambu-mcp — `corner_calibration.py` + `behavioral_rules_camera_calibration.py`  
**Goal:** Fix the T1-never-activated bug in `detect_nozzle_heat_toggle()` and write the
dual-extruder calibration mechanics into permanent MCP knowledge before the code ships.

---

## Background

Runs 1–7 of the nozzle-walk DLT calibration have all shared a silent bug:
`detect_nozzle_heat_toggle()` heats T1 (`M104 T1 S180`) but never selects T1 as the active
tool. On the H2D, active tool selection — not heater state — determines which nozzle tip is
physically at the commanded XY position. T1's thermal halo appeared near T0's pixel because
T1 was physically near T0 (the carriage never moved). The T0-T1 distance guard (< 15px →
discard) fired correctly, but the root cause was never addressed.

**Why it wasn't caught earlier:**  
Pattern matching trap — "heat T1 → detect T1 halo" felt complete. The physical consequence
(T1 is offset from the calibration point unless T1 is active) was never verified.

**Why the MCP API was not used:**  
Raw gcode fallback (`send_gcode("T1")`) was used instead of checking what API routes exist.
`PATCH /api/toggle_active_tool` has been in `api_server.py` since before this work began.

---

## What Changes

### Task A — Knowledge update (ships first)

**File:** `~/bambu-mcp/knowledge/behavioral_rules_camera_calibration.py`

Add new section **"H2D Dual-Extruder Calibration Mechanics"** documenting:

| Rule | Content |
|------|---------|
| Physical layout | T0 = right, T1 = left; physically offset in X |
| Active tool rule | Heating Tn does NOT move carriage. Only `swap_tool()` / `toggle_active_tool` positions the selected nozzle at commanded XY |
| Required API | Always use `swap_tool(name)` MCP tool or `PATCH /api/toggle_active_tool` — never raw gcode T0/T1 |
| Per-nozzle idle baseline | Must re-capture after each tool change + move (scene shifts with carriage) |
| State invariant | T0 active on entry AND exit of `detect_nozzle_heat_toggle()` |
| expected_px | Same for both nozzles — both are at the same world point when each is active |

Also fix the "GCode calibration sequence" section — it references "the nozzle" as singular.
Add a note that each point must specify which nozzle is being measured on the H2D.

**Commit:** standalone commit in bambu-mcp. Sync to bambu-rules.

---

### Task B — Code fix (after knowledge)

**File:** `~/bambu-mcp/camera/corner_calibration.py`

**Change 1 — new helper function:**
```python
def toggle_active_tool() -> None:
    """Switch active tool via MCP API (T0→T1 or T1→T0). Uses bpm set_active_tool()."""
    requests.patch(f"{API_BASE}/toggle_active_tool?{PRINTER_PARAM}",
                   headers=_auth_headers(), verify=False, timeout=10)
```

**Change 2 — `detect_nozzle_heat_toggle()` signature:**
```python
def detect_nozzle_heat_toggle(expected_px, name, output_dir, world_xy: tuple) -> dict:
```

**Change 3 — loop restructure (T0 active on entry, invariant):**
```
T0 phase:
  (T0 already active — no toggle)
  heat T0 → detect → cool

T1 phase:
  toggle T0→T1 via toggle_active_tool()
  move to world_xy  (T1 tip now at WXY, carriage shifted)
  time.sleep(SETTLE_SECONDS_XY)
  descend to Z_CAPTURE
  re-capture idle baseline  (MANDATORY — scene shifted)
  heat T1 → detect → cool
  toggle T1→T0
  move to world_xy  (T0 tip now at WXY)
  time.sleep(SETTLE_SECONDS_XY)
```

**Change 4 — call site update (~line 1112 in `run_calibration()`):**
```python
# Before:
detect_nozzle_heat_toggle(expected_px=expected, name=name, output_dir=OUTPUT_DIR)
# After:
detect_nozzle_heat_toggle(expected_px=expected, name=name, output_dir=OUTPUT_DIR, world_xy=(wx, wy))
```

**Commit:** single commit in bambu-mcp (has commit grant).

---

## Risk Assessment

| Change | Risk | Mitigation |
|--------|------|------------|
| Knowledge: new section | Low — additive only | Read module after edit to verify |
| Knowledge: fix singular "nozzle" | Low — doc change | Check surrounding context is not broken |
| `toggle_active_tool()` helper | Low — single API call | Verify route exists in api_server.py (confirmed: line 1001) |
| Signature change (add `world_xy`) | Low — one call site only | Update call site in same commit |
| Loop restructure | Medium — re-ordering heat/cool + toggle steps | Follow exact sequence in plan; verify idle re-capture is present |
| Per-nozzle idle baseline | None — required correctness fix | Confirm `idle_frame` captured after tool change in code |

---

## Open Questions

None — the approach is fully determined. `PATCH /api/toggle_active_tool` toggles 0↔1 from
printer state; since sequence is always T0→T1→T0, two calls are sufficient without tracking
current tool state in the script.

---

## Lateral Impact Assessment

**Rules changed:** None — knowledge module only, not global/project rules files.  
**Behavior changed:** `detect_nozzle_heat_toggle()` — T1 now measured at correct pixel.  
**Downstream:** `run_calibration()` call site updated. No other callers of `detect_nozzle_heat_toggle`.  
**coord_transform.py:** Not touched — updated after successful DLT run as before.

---

## After This Work

Run 8 can proceed. Prerequisites:
1. Printer IDLE confirmed
2. User explicit authorization
3. Bed-clear confirmation
