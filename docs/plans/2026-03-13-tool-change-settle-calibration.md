# Tool-Change Settle Calibration Plan

**Date:** 2026-03-13  
**Context:** Adding to T1 tool-change impact reconciliation plan.

## Background

`TOOL_CHANGE_SETTLE_SECONDS = 5` was a placeholder constant — a fixed sleep after
`toggle_active_tool()` before issuing a `G0` move. Fixed sleeps are imprecise: too short
and the carriage is still moving when the snapshot fires; too long and calibration is slower
than needed.

Precedent: `HOME_WAIT_SECONDS = 90` (anecdotal) was replaced by `wait_for_home_complete()` —
empirically calibrated visual frame-diff stability at 720p/2s. The same pattern applies here.

## Design: `wait_for_tool_change_complete()`

Analogous to `wait_for_home_complete()` but tuned for a shorter (1–3s) event:

| Parameter | Homing | Tool-Change | Rationale |
|-----------|--------|-------------|-----------|
| Resolution | 720p | **360p** | Tool change is short; 360p is faster to capture |
| Poll interval | 2.0s | **0.3s** | Must resolve a ~1s event; 2s is too coarse |
| Stable frames | 4 | **3** | Shorter event; fewer frames needed |
| Noise multiplier | 1.5× | **1.5×** | Same criterion |
| Timeout | 65s | **15s** | Tool change never takes >5s normally |
| Noise floor | 2.2px (720p) | **[PROVISIONAL 1.5px]** | 360p ≠ 720p noise floor — must measure |

**Critical:** `TOOL_CHANGE_NOISE_FLOOR_PX` is **not** the same value as `HOME_NOISE_FLOOR_PX`.
Lower resolution compresses vibration-induced pixel differences → lower absolute avg-diff.
Do not substitute 2.2px. Measure at 360p using the calibration script.

## New Tasks

### TC-1: `camera/calibrate_tool_change_settle.py`

Standalone calibration script (runs once, outputs constants to bake in):

1. Home + move to B005 corner at Z_CLEARANCE
2. Measure 360p noise floor: 5 stationary frame pairs → mean avg-abs-diff
3. `PATCH /api/toggle_active_tool` (T0→T1); t=0; poll 0.3s/360p until stable (3 frames ≤ noise×1.5)
4. `PATCH /api/toggle_active_tool` (T1→T0); repeat
5. Run 3 full trials
6. Print summary: noise floors, T0→T1 t_settle per trial, T1→T0 t_settle per trial
7. Print: `TOOL_CHANGE_NOISE_FLOOR_PX = {mean}`, `TOOL_CHANGE_TIMEOUT_S = {max + 5s}`

**Prerequisites:** Printer idle + user explicit authorization + bed-clear confirmation.

### TC-2: `wait_for_tool_change_complete()` (part of Task B)

New constants + function in `corner_calibration.py`. Replaces `time.sleep(TOOL_CHANGE_SETTLE_SECONDS)`.

Extract `_snapshot_at_res(resolution)` helper shared by both wait functions.
`get_snapshot()` becomes: `return _snapshot_at_res(SNAPSHOT_RESOLUTION)`.

### TC-3: Knowledge module section (part of Task A)

New section **"Tool-Change Settle Duration (H2D)"** in `behavioral_rules_camera_calibration.py`
after the G28 Homing Duration section. Documents constants, PROVISIONAL status, measurement
procedure, and the 360p ≠ 720p noise floor distinction.

### TC-4: bambu-mcp project rules

Add "Tool-Change Settle Detection" subsection to Camera Calibration section in
`~/bambu-mcp/.github/copilot-instructions.md`. Documents PROVISIONAL status, recalibration
trigger conditions, and that `TOOL_CHANGE_SETTLE_SECONDS` (fixed sleep) is retired.

## Ordering

| Step | Task | Unblocked when |
|------|------|----------------|
| 1 | N1 — vision_notes corrections | Ready now |
| 2 | TC-1 — write calibrate script | Ready now |
| 3 | A + TC-3 — knowledge module | Ready now |
| 4 | B + TC-2 — corner_calibration.py | Ready now (TC-1 written first; no run needed) |
| 5 | TC-4 — project rules | After B+TC-2 |
| 6 | Run calibrate_tool_change_settle.py | Printer idle + user auth + bed clear |
| 7 | TC-bake — update constants from run | After step 6 |
| 8 | Run 8 calibration | After B + printer idle + user auth |
| 9 | C — nozzle_compare.py | After B |
| 10 | Run nozzle_compare.py | After C + printer idle |
| 11 | N1b — update vision_notes with real T0-T1 data | After step 10 |

## Per-File Change Table

| File | Change | Why |
|------|--------|-----|
| `camera/calibrate_tool_change_settle.py` | **NEW** — standalone calibration script | Measures 360p noise floor + t_settle for both toggle directions |
| `camera/corner_calibration.py` | `_snapshot_at_res()` helper; `wait_for_tool_change_complete()` function; new constants; `get_snapshot()` refactor; remove `TOOL_CHANGE_SETTLE_SECONDS` | Dynamic settle detection replaces fixed sleep |
| `knowledge/behavioral_rules_camera_calibration.py` | Add Tool-Change Settle Duration section | MCP agents must know how settle detection works and the PROVISIONAL status |
| `bambu-mcp/.github/copilot-instructions.md` | Add Tool-Change Settle Detection subsection | Rules: use function not sleep; 360p≠720p noise; recalibrate on service |

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| PROVISIONAL noise floor too low → false early-settle | Medium | Timeout = 15s hard cap; worst case: occasional missed settle caught by SETTLE_SECONDS_XY |
| PROVISIONAL noise floor too high → never settles | Low | Timeout raises TimeoutError with message; run calibration script |
| 360p snapshot API slower than expected | Low | 0.3s poll still gives ~3 frames/s; adequate for 1-3s event |
| T0→T1 and T1→T0 settle times differ significantly | Low | Calibration script measures both; max is used for timeout |
