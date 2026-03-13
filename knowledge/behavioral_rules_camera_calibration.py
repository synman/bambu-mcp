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
"""
