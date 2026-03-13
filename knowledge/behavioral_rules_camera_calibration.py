"""
behavioral_rules_camera_calibration.py — H2D plate boundary calibration knowledge.

Sub-topic of behavioral_rules/camera. Access via
get_knowledge_topic('behavioral_rules/camera_calibration').
"""

from __future__ import annotations

BEHAVIORAL_RULES_CAMERA_CALIBRATION_TEXT: str = """
# H2D Camera Calibration — Plate Boundary Reference

---

## Purpose

The calibration system establishes a canonical PLATE_BOUNDARY in raw camera pixel
coordinates for the Bambu Lab H2D printer. This boundary is the authoritative reference
for all downstream vision work: spaghetti detection, object-in-frame checks, first-layer
inspection, and build plate overlays.

Source of truth: `camera/coord_transform.py` — PLATE_BOUNDARY, PLATE_POLY,
is_on_plate(), normalize_to_plate().

---

## Corner naming convention

  F = Far  = back of printer  = top / small-Y in camera image
  N = Near = front of printer = bottom / large-Y in camera image
  L = Left  (viewed from front of printer)
  R = Right (viewed from front of printer)

Four named corners:
  FL  Far-Left   — back-left corner of the plate  — upper-left in camera image
  FR  Far-Right  — back-right corner              — upper-right (behind toolhead at home)
  NL  Near-Left  — front-left corner              — ORIGIN; below camera frame at Z≈2
  NR  Near-Right — front-right corner             — lower-right; partially off-frame

---

## Camera location (H2D — empirical)

**Camera optical center: approximately (X=0, Y=5, Z=+75) in H2D world coordinates.**
  X=0 = left edge of machine frame
  Y=5 = near front edge of printer
  Z=+75 = 75mm above bed surface (empirical; user-verified 2026-03-13)

All prior documentation claiming "back/top" or "back-right" mounting is **wrong**. The
camera is at the **front-left**, which explains the image geometry:

- Back row (FL/FR, Y=315) is FAR from camera → appears near top of frame, wide pixel spread
- NL corner (5, 5) is nearly DIRECTLY BELOW camera → off-frame at Z≈2 (confirmed)
- Left-column blind zone (x=5, y<315): steep sideways angle from X=0 camera → carriage arm
  occludes nozzle tip. NOT "left frame wall" as previously documented.
- F005 (5, 40) blind zone: nearly directly below camera → steep downward angle, carriage body
  blocks nozzle tip from above. NOT a generic "camera blind zone" for unknown reasons.
- B345@Y=315: gantry Y-beam crosses line-of-sight from (0,5,75) to (345,315) — valid exclusion.
- "Closest to camera" = **small X, small Y** (front-left quadrant), not back-right.

The DLT H matrix, all blind zone exclusion decisions, and all empirically-set thresholds are
SAFE regardless of this correction — they were derived from pixel measurements, not from
camera position calculations.

---

## Reference frame — Z position and light

Calibration MUST be performed at the print-start Z height (bed raised to first-layer
position, approximately Z=2mm from nozzle / bed near the top of its travel).

Why this is the canonical frame:
- CoreXY kinematics: toolhead moves in XY; bed moves in Z only.
- During a print, the bed moves DOWN by one layer height (0.1–0.3 mm) per layer.
- Z-lift between moves is ±0.2–0.5 mm — negligible camera shift.
- Therefore: the camera viewport changes by < 1 pixel per layer throughout the print.
- One calibration at print-start Z covers the entire print with acceptable accuracy.

To capture the reference frame:
  1. Ensure printer is IDLE (gcode_state = IDLE).
  2. Send: G28 (home all axes), then G0 Z2 F600 (raise to first-layer height).
  3. Turn on chamber light: set_chamber_light(name, on=True).
  4. Wait ~30 s for bed to fully settle (vibration damping + camera exposure stabilise).
  5. Capture: get_snapshot(name, resolution="native", quality=95).
  6. Save the raw PNG as the calibration base image.

Do NOT use the parked / idle image (bed at Z=0, toolhead obscuring top of frame).
The parked image shows the bed far from the nozzle — corners are at incorrect pixel
positions and the near edge of the plate is completely outside the camera frame.

---

## Camera geometry — what is and is not visible

Camera frame: 1680 × 1080 pixels.

At Z≈2 (print-start height):
  - FL corner:  visible — upper-left area of frame
  - FR corner:  partially visible — upper-right, often behind toolhead at home position
  - NR corner:  partially visible — right side, near bottom of frame or slightly below
  - NL corner:  NOT visible — below camera frame (y ≈ 1900+)

The near (front) edge of the H2D plate extends below the camera's viewing angle
at all useful print heights. NL must be extrapolated / measured by other means
(GCode calibration sequence, physical measurement) and treated as an off-frame anchor.

---

## Shell vs synbot corner sets

Two corner sets are tracked:

  SHELL  — user-confirmed ground truth coordinates.
            These are the authoritative PLATE_BOUNDARY.
            Updated only when the user explicitly provides corrections.

  SYNBOT — agent-estimated coordinates from automated analysis.
            Known to have a right-side degeneracy: NR and FR are only ~7 px apart in X
            because both were measured against the printer right wall, not bed corners.
            SYNBOT corners are unreliable for right-side detection.

The homography H (synbot→shell) maps synbot pixel space to shell pixel space.
Residuals at all 4 control points: 0.0000 px (exact, by construction).

WARNING: H extrapolates badly for interior points because synbot's right-side corners
are degenerate. Use SHELL (= PLATE_BOUNDARY) directly for all vision gating.
Only use H when you specifically need to remap synbot-space detections to shell space.

---

## PLATE_BOUNDARY — current values

These are stored in camera/coord_transform.py and are the authoritative reference.
Update this module whenever the user provides corner corrections.

  FL  Far-Left   raw camera px
  FR  Far-Right  raw camera px
  NR  Near-Right raw camera px
  NL  Near-Left  raw camera px  ← ORIGIN (0,0) in plate-relative space

Convex hull winding order (clockwise from FL): FL → FR → NR → NL.

PLATE_POLY is the np.float32 array form for use with cv2.pointPolygonTest and
matplotlib.path.Path.

---

## Vision pipeline integration

  is_on_plate(pt, margin_px=0)
    → Returns True if camera pixel pt=(x,y) is inside PLATE_BOUNDARY.
    → Use as gate for all bounding-box detections (YOLO, spaghetti strands, etc.).
    → margin_px > 0 erodes the boundary inward (conservative); < 0 expands it.

  normalize_to_plate(pt)
    → Maps camera pixel to plate-relative (u,v) in [0,1]×[0,1].
    → u=0,v=0 → FL  /  u=1,v=0 → FR  /  u=0,v=1 → NL  /  u=1,v=1 → NR
    → Uses bilinear inverse (Newton, 10 iterations). Accurate inside the quad.
    → Extrapolates outside the quad — clamp u,v to [0,1] for boundary checks.

  synbot_to_shell(pt), shell_to_synbot(pt)
    → Project between the two calibration frames via homography.
    → Only needed when consuming synbot-space coordinates from automated analysis.

---

## Plate color

Sampled from pixels inside the shell polygon at Z≈2, lit:
  Approximate plate color: RGB(73, 98, 103) — steel blue-gray teal.
  Boosted overlay tint:    RGB(73, 110, 117)

The textured PEI plate on the H2D appears as a muted cyan/teal at the calibration Z.
Use this color for plate overlay blends and boundary visualisations.

---

## Calibration files

  camera/coord_transform.py    — PLATE_BOUNDARY, H matrices, is_on_plate,
                                  normalize_to_plate, synbot_to_shell
  camera/corner_calibration.py — GCode calibration sequence generator, piecewise
                                  affine coefficients, Phase 1 calibration pipeline

---

## GCode calibration sequence (Phase 1 — not yet run)

corner_calibration.py can generate a GCode sequence to drive the nozzle to each
corner position and capture a camera frame for automated corner detection.

Prerequisites (Printer Write Protection gate — mandatory):
  1. Confirm printer is IDLE.
  2. User must explicitly authorize GCode execution.
  3. User must confirm bed is empty (no clips, objects, tools).

Safety: all moves use Z_CLEARANCE=10mm between positions, Z_CAPTURE=2mm at corner.
See GCode Calibration Motion Safety rules in global copilot-instructions.md.

---

## H2D Dual-Extruder Calibration Mechanics

The H2D has two physically separate nozzles mounted on a single carriage:
  T0 = right extruder (default active at power-on and after G28)
  T1 = left extruder

Inter-nozzle offset: T1's tip is physically offset from T0's tip in X (approximately 18mm
based on H2D hardware). The firmware applies this offset automatically when the active tool
is selected — when T1 is active, `G0 X175 Y160` positions T1's tip at (175, 160), carriage
shifted accordingly.

### Critical rule: active tool selection determines which tip is at the commanded XY

Heating a nozzle (M104 Tn S180 or set_nozzle_temp()) does NOT move the carriage.
Only active tool selection (swap_tool() / PATCH /api/toggle_active_tool) moves the
carriage so that the selected nozzle is at the commanded world position.

Consequence for calibration:
  WRONG: heat T1 → capture frame → detect T1 halo at commanded XY
    → T1's halo appears near T0's pixel (T1 is physically offset when T0 is active at WXY)
    → recorded pixel is near T0's position, not T1's true projected position
  RIGHT: swap_tool() to T1 → move to (wx,wy) → T1 is now at (wx,wy) → heat T1 → detect

### Tool switching — always use toggle_active_tool() or PATCH /api/toggle_active_tool

Never use raw gcode T0 / T1 for tool switching in calibration. Always use:
  - HTTP route: PATCH /api/toggle_active_tool?printer=H2D

The HTTP route reads the current active tool from printer state and toggles to the other.
It calls bpm's set_active_tool() which handles H2D-specific firmware requirements correctly.
Since the calibration sequence is always T0→T1→T0, exactly two toggle calls suffice.

### Per-nozzle idle baseline is mandatory after every tool change + re-position

After PATCH /api/toggle_active_tool + G0 to (wx,wy), the camera scene shifts (carriage
shifted to position the new nozzle at WXY). The idle baseline captured at T0's position is
invalid as a thermal reference for T1 measurements. Must re-capture idle baseline after each
tool change + re-position before heating the new nozzle.

### State invariant: T0 active on entry and exit of heat_halo

The calibration loop expects T0 to be the active tool at the start of each point and
when the loop resumes after heat_halo completes. The heat_halo routine must:
  1. Enter with T0 active (caller's invariant)
  2. Test T0 (already active — no toggle needed)
  3. Toggle T0→T1 via PATCH /api/toggle_active_tool
  4. wait_for_tool_change_complete() — visual settle detection
  5. Move to world_xy (carriage shifts so T1 tip is at wx,wy)
  6. Re-capture idle baseline at T1 position
  7. Test T1
  8. Toggle T1→T0 via PATCH /api/toggle_active_tool
  9. wait_for_tool_change_complete()
  10. Move to world_xy (carriage shifts so T0 tip is back at wx,wy)
  11. Exit with T0 active (restores caller's invariant)

### T0-T1 guard semantics after tool-change fix

With the tool-change fix applied, T0 and T1 are BOTH moved to (wx,wy) for their respective
tests. The H matrix maps (wx,wy) to the same pixel regardless of which nozzle is active.
Therefore T0 and T1 halos appear at approximately the same pixel — distance ≈ 0–15px.

  CORRECT guard: T0-T1 distance < 30px → ACCEPT (conf × 1.2); both confirmed at same world point
  DISCARD guard: T0-T1 distance ≥ 30px → one detection is on artifact; discard heat_halo

### expected_px applies to whichever nozzle is active at (wx,wy)

The H matrix maps world (wx,wy) to a pixel. That pixel is correct for whichever nozzle
is physically at (wx,wy). If T1 is active at (wx,wy), the expected pixel is the same as
for T0 — both nozzles produce the same world→pixel projection when each is at (wx,wy).

### On the H2D, calibration references "nozzles" (plural), not "the nozzle" (singular)

Each calibration point must specify which nozzle is being measured. T0 is the default.
To measure T1's pixel position at a world point, T1 must be the active tool before the move.
See "H2D Dual-Extruder Calibration Mechanics" above.

---

## G28 Homing Duration (H2D — Verified Empirical)

[VERIFIED: empirical — 3 trials, 2026-03-12]

| Fact | Value | Notes |
|------|-------|-------|
| G28 completion time | 46.5–46.9s (mean 46.7s) | 3 trials, visual frame-diff |
| HOME_TIMEOUT_SECONDS | 65s | max(46.9s) + 18s safety margin |
| HOME_NOISE_FLOOR_PX | 2.2px | stationary-frame avg abs-diff (pre-G28 baseline) |
| Stability threshold | 3.3px (floor × 1.5) | 4 consecutive frames at/below = homing done |
| Completion signal | Visual frame-diff only | gcode_state stays IDLE throughout G28 |

Two-phase homing profile:
- Phase 1 (0–23s): primary XY homing. Brief ~1-frame apparent pause at ~23s — this is NOT
  done; the toolhead is between phases. Do not declare complete on this pause.
- Phase 2 (27–42s): Z probe / bed touch sequence.
- Confirmed stable: t≈46.5–46.9s (4-frame criterion).

Trial raw data (2026-03-12, idle H2D, 20°C ambient):
  Trial 1: t_done=46.5s, noise_floor=2.12px, threshold=3.17px
  Trial 2: t_done=46.6s, noise_floor=2.16px, threshold=3.23px
  Trial 3: t_done=46.9s, noise_floor=2.12px, threshold=3.18px

Measurement process (re-run if H2D is serviced or replaced):
  1. Establish noise floor: 3 baseline snapshots at rest → compute avg abs-diff per pair.
  2. Send G28, record t=0.
  3. Poll snapshot every 2s; compute mean abs diff vs prior frame.
  4. Declare done when 4 consecutive diffs ≤ noise_floor × 1.5; record t_done.
  5. Run 3 trials; update HOME_TIMEOUT_SECONDS = max(t_done) + 18s.
  6. Update HOME_NOISE_FLOOR_PX to the measured mean noise floor.

Prior value (retired): HOME_WAIT_SECONDS = 90 (anecdotal "60–90s" comment). Replaced by
  HOME_TIMEOUT_SECONDS = 65, which is authoritative from 2026-03-12 forward.

Code references:
  camera/corner_calibration.py line ~172 — HOME_TIMEOUT_SECONDS, HOME_NOISE_FLOOR_PX constants
  camera/corner_calibration.py line ~240 — wait_for_home_complete() implementation

---

## Tool-Change Settle Duration (H2D — Calibration Required)

[PROVISIONAL — constants not yet measured empirically. Run camera/calibrate_tool_change_settle.py
 before treating TOOL_CHANGE_NOISE_FLOOR_PX or TOOL_CHANGE_TIMEOUT_S as authoritative.]

Visual detection method (analogous to G28 homing detection, tuned for shorter event):
- Resolution: 480p (better signal than 360p at Z=2mm capture position; calibration position
  is front-left quadrant (80,80) — closest visible area to camera at (0,5,75))
- Poll interval: 0.3s (vs 2.0s for homing — finer resolution of a shorter event)
- Stable criterion: 3 consecutive frames with avg-diff ≤ TOOL_CHANGE_NOISE_FLOOR_PX × 1.5
- Hard timeout: TOOL_CHANGE_TIMEOUT_S = 15s (conservative until measured)

| Constant | Value | Status |
|----------|-------|--------|
| TOOL_CHANGE_POLL_S | 0.3s | Fixed (design choice) |
| TOOL_CHANGE_SNAPSHOT_RES | "480p" | Fixed (design choice) |
| TOOL_CHANGE_STABLE_N | 3 | Fixed (design choice) |
| TOOL_CHANGE_NOISE_MULT | 1.5 | Fixed (same as homing) |
| TOOL_CHANGE_NOISE_FLOOR_PX | [PROVISIONAL 1.5px] | MUST be measured at 480p |
| TOOL_CHANGE_TIMEOUT_S | 15.0s | Conservative until measured |

IMPORTANT: TOOL_CHANGE_NOISE_FLOOR_PX is NOT the same as HOME_NOISE_FLOOR_PX (2.2px).
HOME_NOISE_FLOOR_PX is measured at 720p. 480p noise floor differs in absolute px — the
different resolution and different Z capture height (Z=2mm vs homing at full range) both
affect the noise floor. Do NOT substitute 2.2px. Use calibrate_tool_change_settle.py to measure.

Measurement process (run if H2D is serviced, replaced, or noise floor value is suspect):
  1. Home + move to (80, 80) at Z_CLEARANCE; descend to Z_CAPTURE=2mm.
  2. Establish 480p noise floor: 5 baseline frame pairs at rest → mean avg-abs-diff.
  3. Toggle T0→T1 via PATCH /api/toggle_active_tool; record t=0.
  4. Poll 0.3s / 480p; compute avg-abs-diff vs prior frame.
  5. Declare done when 3 consecutive diffs ≤ noise_floor × 1.5; record t_settle.
  6. Toggle T1→T0; repeat steps 3–5.
  7. Run 3 trials for each direction (T0→T1 and T1→T0 may differ in carriage travel).
  8. Update: TOOL_CHANGE_NOISE_FLOOR_PX = mean(all noise_floor measurements).
             TOOL_CHANGE_TIMEOUT_S = max(all t_settle across both directions) + 5s.

Code references:
  camera/corner_calibration.py — TOOL_CHANGE_* constants, wait_for_tool_change_complete()
  camera/calibrate_tool_change_settle.py — standalone calibration script
"""
