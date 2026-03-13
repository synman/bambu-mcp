#!/usr/bin/env python3
"""
H2D Perimeter Calibration — GCode nozzle→point, diff-frame nozzle detection, DLT solve.

Issue #10, Track 3: Camera plate-boundary geometric calibration.

Prerequisites:
- H2D printer IDLE (gcode_state != RUNNING/PREPARE)
- Bed empty (user confirmed)
- bambu-mcp server running on localhost
- ~/.bambu-mcp/calibration/H2D.json present (prior 4-corner calibration)

Sequence per point:
  1. Move nozzle to world XY at Z_CLEARANCE (10mm)
  2. Capture reference frame (nozzle far from bed)
  3. Descend to Z_CAPTURE (2mm) — nozzle tip visible above bed
  4. Capture nozzle frame
  5. Diff → detect nozzle centroid in pixel space
  6. Ascend back to Z_CLEARANCE

After all points: overdetermined DLT solve (11+ pts × 2eq = 22eq >> 8 DOF) → new H.
Per-point reprojection residuals reported to identify outliers.

Safety: all GCode follows the mandatory safe motion pattern from global rules
(GCode Calibration Motion Safety):
  - No XY travel at Z=0
  - Z transitions only when fully stationary (M400)
  - Every movement path clear (user confirmed bed empty)

Sampling rationale:
  H2D camera is mounted at front-left (empirical: X=0, Y=5, Z=+75), viewing bed obliquely.
  Back row (Y=260)
  spans ~936px in frame; right column (X=345) at mid-Y spans center-right of frame.
  Point set covers: back row, left column, right column (mid-Y), front endpoints.
    Back row (5 pts): B005/B090/B175/B260/B345 — wide pixel spread
    Left col (1 pt): L243 (L108/L175 hard-excluded: steep-angle carriage occlusion confirmed)
    Right col (1 pt): R175 (R243 hard-excluded: near-degenerate with B345)
    Front endpts (1): F345 (F005 hard-excluded: nearly below camera — blind zone)
  B345 re-enabled at Y=260 (was excluded at Y=315 — gantry Y-beam crosses line-of-sight
  from front-left camera to far back-right corner at Y=315).
  F005 (5,40) is a permanent hard-exclude: nearly directly below camera (0,5,75).

Usage:
  python3 corner_calibration.py                # full run (home + probe all points)
  python3 corner_calibration.py --supplement   # probe only NEW points not in existing results
"""

import numpy as np
from PIL import Image
import urllib.request
import urllib.parse
import http.client
import base64
import io
import json
import time
import sys
import os
import re
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PRINTER_NAME = "H2D"


def _find_api_base() -> str:
    """Discover the bambu-mcp HTTP API port by scanning the ephemeral pool."""
    import urllib.error
    for port in range(49152, 49252):
        try:
            url = f"http://localhost:{port}/api/server_info"
            with urllib.request.urlopen(url, timeout=0.5) as r:
                info = json.loads(r.read())
                if "api_port" in info:
                    return f"http://localhost:{info['api_port']}/api"
        except Exception:
            continue
    return "http://localhost:49152/api"  # fallback


API_BASE = _find_api_base()

Z_CLEARANCE = 10    # mm — safe travel height
Z_CAPTURE = 2       # mm — nozzle visible above bed surface
Z_RESCUE  = 1.0     # mm — fallback capture height for low-confidence zones
F_XY = 3000         # mm/min — conservative XY travel speed
F_Z = 600           # mm/min — conservative Z speed

# Detection confidence thresholds
CONF_ACCEPT          = 0.7    # stop technique cascade within a single probe (technique level)
CONF_RESCUE          = 0.4    # trigger rescue probe at Z_RESCUE if best conf stays below this
CONF_HEAT            = 1.01   # always trigger heat_halo for every point (> any real conf value)
                               # Root cause: sparse_bright detects nozzle shaft/body centroid at Y≈250,
                               # while heat_halo finds the thermal footprint (nozzle tip) at Y≈320.
                               # Z-offset between Z=10mm (heat_halo) and Z=2mm (close-approach) is ~6px
                               # — negligible, not the 80-110px previously believed. Use heat_halo only.

# Repeatability guard: if winning positions vary > this px across probes → conf × 0.5 penalty.
# Catches B260-class drifting artifacts (top_pct latches thermal gradient that moves 10px/probe).
REPROB_STDDEV_THRESH = 8.0    # px — positional std_dev threshold for repeatability penalty

# Back-row Y consistency guard: back-row points (world Y = BACK_ROW_WORLD_Y) should all
# project to similar pixel Y. Deviations signal carriage-body false detection (B175-class).
BACK_ROW_WORLD_Y     = 230    # mm — world Y for all back-row perimeter points
BACK_ROW_Y_TOL       = 20.0   # px — max allowed deviation from running back-row pixel-Y mean

# PREFER_CLOSE_APPROACH disabled (was 0.25): the 80-110px Z-offset claim was wrong.
# Empirical: B005 heat_halo=(119,324) vs old-H predict=(118,318) — only 6px delta.
# Sparse_bright was detecting nozzle shaft/carriage body (~70px too high in image),
# not a Z-height difference in correct detections. Heat_halo wins unconditionally.
PREFER_CLOSE_APPROACH_CONF = 0.0

# Thermal toggle (heat_halo) — last-resort detection
T_HEAT               = 180    # °C — nozzle target for visible thermal glow
T_HEAT_ESCALATE      = 220    # °C — escalated temp if T0 halo is still weak (dmax < threshold)
HEAT_WAIT            = 45     # seconds — wait for stable thermal halo to appear in frame
T_IDLE               = 38     # °C — idle/standby nozzle target (firmware default)

# H2D printable area: 345mm × 320mm (X × Y) [VERIFIED: BambuStudio fdm_bbl_3dp_002_common.json
# printable_area + extruder_printable_area. T0 (right) printable X: 25-350; T1 (left): 0-325.
# Shared printable: 25-325 (300mm), exclusive: T1 only 0-25, T0 only 325-350.
# Physical machine X travel: 0-350mm; inter-nozzle separation: 25mm.]
# 11-point sample set.
# Format: (name, world_x_mm, world_y_mm)
# Ordering: back-to-front traversal (homing reference at back; minimize travel).
PERIMETER_POINTS = [
    ("B005",  5, 230),   # Back-Left  (Y=230: heater-block parallax at Y=260 confirmed; Y=243 works)
    ("B090", 90, 230),   # Back row
    ("B175",175, 230),   # Back row
    ("B260",260, 230),   # Back row
    ("B345",345, 230),   # Back-Right — extends back row; better H conditioning for right side
    ("R243",345, 243),   # Right col — back-side anchor for right coverage
    ("R175",345, 175),   # Right col — mid anchor
    ("L243",  5, 243),   # Left col
    ("L175",  5, 175),   # Left col
    ("L108",  5, 108),   # Left col — front-side anchor
    ("F005",  5,  40),   # Front-Left endpoint
    ("F345",345,  40),   # Front-Right endpoint
]

# Points permanently excluded due to known camera blindspots / false detections.
# These are skipped regardless of run mode.
HARD_EXCLUDE = {
    # (5,40) nearly directly below camera (0,5,75) — steep angle occludes nozzle tip; dmax<10
    "F005",
    # (5,108) and (5,175): left-column blind zone — steep-angle carriage occlusion confirmed;
    # RANSAC outliers by 285-292px from 7-inlier H in all prior runs.
    "L108",
    "L175",
    # B345 re-enabled at Y=260 (previously excluded at Y=315 for gantry-beam occlusion).
    # L243 re-enabled: improved cascade (repeatability penalty + back-row guard + heat_halo
    # escalation) expected to catch false detections before they reach DLT.
    # R243 excluded: near-degenerate with B345 at this camera geometry. B345(345,260) and
    # R243(345,243) are only 13px apart in image space (17mm world separation maps to 0.8px
    # vertical movement at world X=345). Near-duplicate DLT constraint rows destabilize the
    # SVD solve. R175(345,175) provides right-column coverage without the degeneracy.
    "R243",
}

# FIXED_POINTS intentionally empty after tool-change fix + guard flip (2026-03-13).
# R175 (527,427) and F345 (511,513) were hardcoded from buggy runs where T1 was never
# made the active tool. After fix: heat_halo with guard < 30px ACCEPT now handles these.
FIXED_POINTS: dict = {}

CAL_JSON_PATH = os.path.expanduser("~/.bambu-mcp/calibration/H2D.json")

SNAPSHOT_RESOLUTION = "720p"   # native ~1s/snap; 720p is faster
SNAPSHOT_QUALITY = 75
SETTLE_SECONDS_Z = 3.0         # after Z descent/ascent (short move, vibration damping)
SETTLE_SECONDS_XY = 12.0       # after XY corner move (up to 490mm at F3000 ≈ 10s + buffer)
# [VERIFIED: empirical] H2D G28 (standalone) completes in 46.5–46.9s (mean 46.7s)
# Evidence: 3 trials, visual frame-diff (720p, 2s interval), 2026-03-12
# Homing profile: two-phase — Phase 1 (0-23s XY), brief pause, Phase 2 (27-42s Z probe).
# 4-consecutive-stable-frame criterion (noise×1.5 threshold) confirmed each trial.
# Timeout = max(46.9s) + 18s safety margin = 65s.
HOME_TIMEOUT_SECONDS = 65
PROBES_PER_POINT = 3           # capture attempts per point; keep highest-confidence result

# Tool-change settle detection constants (analogous to homing constants, tuned for ~7-11s event)
# [VERIFIED: empirical] calibrate_tool_change_settle.py, 3 trials each direction, 2026-03-13.
# 480p noise floor ≠ 720p noise floor (HOME_NOISE_FLOOR_PX=2.2px) — do not substitute.
# Asymmetry: T0→T1 settles in 7.22–7.27s; T1→T0 settles in 11.04–11.19s (two-phase motion).
# Actual poll rate ~2s/frame (snapshot latency dominates 0.3s POLL_S setting).
TOOL_CHANGE_POLL_S = 0.3          # nominal; actual effective rate ~2s due to snapshot latency
TOOL_CHANGE_SNAPSHOT_RES = "480p" # 480p — better signal at Z=2mm; noise floor differs from 720p
TOOL_CHANGE_STABLE_N = 3          # 3 consecutive stable frames
TOOL_CHANGE_NOISE_MULT = 1.5      # same multiplier as wait_for_home_complete
TOOL_CHANGE_TIMEOUT_S = 16.2      # [VERIFIED: empirical] max(T1→T0=11.19s) + 5s margin
TOOL_CHANGE_NOISE_FLOOR_PX = 2.70 # [VERIFIED: empirical] 480p/(80,80)/Z=2; mean of 6 measurements

# Idle nozzle heat timeout — firmware silently resets nozzle target to T_IDLE (38°C)
# after this duration when gcode_state is IDLE, FINISH, or FAILED.
# [PROVISIONAL ~170s] — update with [VERIFIED: empirical YYYY-MM-DD] after calibration run.
# Run: python3 camera/calibrate_idle_nozzle_timeout.py
IDLE_NOZZLE_HEAT_TIMEOUT_S  = 170.0   # [PROVISIONAL] measured via calibrate_idle_nozzle_timeout.py
IDLE_HEAT_KEEPALIVE_S       = IDLE_NOZZLE_HEAT_TIMEOUT_S * 0.75   # proactive re-assert before reset fires
IDLE_HEAT_POLL_INTERVAL_S   = 10.0    # reactive poll interval — verify target not drifted

OUTPUT_DIR = "/tmp/h2d_corner_calibration"

# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def send_gcode(gcode: str) -> dict:
    """Send GCode to H2D via MCP HTTP API (Tier 2 in camera script context).

    Only valid when no dedicated HTTP route covers the operation.
    Legitimate: G28 (home), G0/G1 (motion), G90/G91 (mode), M400 (wait for stop).
    NOT valid for temperature — use set_nozzle_temp() which calls PATCH /api/set_tool_target_temp.
    """
    data = json.dumps({"printer": PRINTER_NAME, "gcode": gcode}).encode()
    req = urllib.request.Request(
        f"{API_BASE}/send_gcode",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def set_nozzle_temp(temp: int, extruder: int = 0) -> dict:
    """Set nozzle temperature via PATCH /api/set_tool_target_temp (Tier 1 — dedicated route).

    NEVER use send_gcode(M104) for temperature — a dedicated HTTP API route exists.
    M104 via send_gcode is a Tier 1 escalation violation in camera script context.
    extruder: 0=right (T0), 1=left (T1).
    """
    data = json.dumps({"printer": PRINTER_NAME, "temp": temp, "extruder": extruder}).encode()
    req = urllib.request.Request(
        f"{API_BASE}/set_tool_target_temp",
        data=data,
        headers={"Content-Type": "application/json"},
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _get_nozzle_targets() -> tuple[float, float]:
    """Read current nozzle temperature targets for T0 and T1 via GET /api/printer."""
    url = f"{API_BASE}/printer?printer={PRINTER_NAME}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        state = json.loads(resp.read())
    nozzles = state.get("_printer_state", {}).get("nozzle_temps", [])
    t0 = float(nozzles[0]["target"]) if len(nozzles) > 0 else -1.0
    t1 = float(nozzles[1]["target"]) if len(nozzles) > 1 else -1.0
    return t0, t1


def heat_and_wait(t0: int, t1: int, duration_s: float) -> None:
    """Wait duration_s while defending against firmware idle nozzle heat timeout.

    H2D firmware silently resets nozzle targets to T_IDLE (38°C) after
    IDLE_NOZZLE_HEAT_TIMEOUT_S when gcode_state is IDLE, FINISH, or FAILED.

    Two concurrent independent checks in a 0.5s inner loop:
      Proactive timer:  elapsed >= IDLE_HEAT_KEEPALIVE_S since last set_nozzle_temp →
                        re-assert before firmware timeout fires (proactive; no drift needed).
      Reactive poll:    every IDLE_HEAT_POLL_INTERVAL_S → read targets via GET /api/printer →
                        re-assert only if target drifted (firmware already reset it; log WARN).
    Both checks share last_assert. set_nozzle_temp() is never called speculatively on every tick.
    All temperature commands use PATCH /api/set_tool_target_temp (Tier 1) — never raw gcode/M104.
    """
    set_nozzle_temp(t0, extruder=0)
    set_nozzle_temp(t1, extruder=1)
    last_assert = time.time()
    next_poll   = time.time() + IDLE_HEAT_POLL_INTERVAL_S
    deadline    = time.time() + duration_s

    while time.time() < deadline:
        now = time.time()

        # Proactive: re-assert at 75% of measured timeout — prevent firmware reset before it fires
        if now - last_assert >= IDLE_HEAT_KEEPALIVE_S:
            set_nozzle_temp(t0, extruder=0)
            set_nozzle_temp(t1, extruder=1)
            last_assert = now

        # Reactive: verify targets haven't drifted (catches any edge-case proactive miss)
        if now >= next_poll:
            actual_t0, actual_t1 = _get_nozzle_targets()
            if actual_t0 != t0 or actual_t1 != t1:
                print(f"  [heat_and_wait] WARN: target drifted "
                      f"(T0:{actual_t0} T1:{actual_t1} expected T0:{t0} T1:{t1}) — re-asserting")
                set_nozzle_temp(t0, extruder=0)
                set_nozzle_temp(t1, extruder=1)
                last_assert = now
            next_poll = now + IDLE_HEAT_POLL_INTERVAL_S

        time.sleep(0.5)

def _snapshot_at_res(resolution: str) -> Image.Image:
    """Capture snapshot at specified resolution (e.g. '360p', '720p'), return PIL Image.

    Retries up to 3 times on transient HTTP errors (BadStatusLine, ConnectionResetError).
    """
    params = urllib.parse.urlencode({
        "printer": PRINTER_NAME,
        "resolution": resolution,
        "quality": SNAPSHOT_QUALITY,
    })
    url = f"{API_BASE}/snapshot?{params}"
    last_exc: Exception = RuntimeError("_snapshot_at_res: no attempts made")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url) as resp:
                data = json.loads(resp.read())
            data_uri = data["data_uri"]
            b64_data = data_uri.split(",", 1)[1]
            img_bytes = base64.b64decode(b64_data)
            return Image.open(io.BytesIO(img_bytes)).convert("RGB")
        except (http.client.BadStatusLine, ConnectionResetError, OSError) as e:
            last_exc = e
            if attempt < 2:
                time.sleep(2.0)
    raise last_exc


def get_snapshot() -> Image.Image:
    """Capture a snapshot from H2D camera at calibration resolution, return as PIL Image."""
    return _snapshot_at_res(SNAPSHOT_RESOLUTION)

def get_printer_state() -> str:
    """Get current gcode_state."""
    params = urllib.parse.urlencode({"printer": PRINTER_NAME})
    url = f"{API_BASE}/printer?{params}"
    with urllib.request.urlopen(url) as resp:
        data = json.loads(resp.read())
        ps = data.get("_printer_state", {})
        return ps.get("gcode_state", "UNKNOWN")


# [VERIFIED: empirical] H2D camera noise floor at rest: 2.09–2.16px avg (3 runs, 2026-03-12)
# Used as stability threshold base in wait_for_home_complete() — measured before motion starts.
HOME_NOISE_FLOOR_PX = 2.2


def wait_for_home_complete(timeout: float = HOME_TIMEOUT_SECONDS,
                           noise_floor: float = HOME_NOISE_FLOOR_PX) -> None:
    """Block until G28 homing completes, detected by visual frame stability.

    Must be called AFTER send_gcode(G28). Uses an empirical noise floor constant
    (measured from stationary frames before G28 in 3 trials, 2026-03-12) to avoid
    contaminating the baseline with motion already in progress.

    [VERIFIED: empirical] H2D G28 always completes within 46.9s (3 trials, 2026-03-12).
    Homing is two-phase: XY (0-23s) then Z probe (27-42s). A single stable frame at ~23s
    is NOT completion — the 4-consecutive-stable-frame criterion handles this correctly.

    Args:
        timeout: Hard timeout in seconds. Default HOME_TIMEOUT_SECONDS (65s).
        noise_floor: Stability baseline in px avg diff. Default HOME_NOISE_FLOOR_PX (2.2px).
    """
    POLL_S = 2.0
    STABLE_N = 4
    NOISE_MULT = 1.5

    threshold = noise_floor * NOISE_MULT
    prev = np.array(get_snapshot().convert("L"), dtype=np.float32)
    stable_count = 0
    t_start = time.time()

    while True:
        elapsed = time.time() - t_start
        if elapsed > timeout:
            raise TimeoutError(
                f"wait_for_home_complete: homing not detected within {timeout:.0f}s"
            )
        time.sleep(POLL_S)
        cur = np.array(get_snapshot().convert("L"), dtype=np.float32)
        diff = float(np.mean(np.abs(cur - prev)))
        prev = cur
        if diff <= threshold:
            stable_count += 1
            if stable_count >= STABLE_N:
                t_done = elapsed - (STABLE_N - 1) * POLL_S
                print(f"    Homing complete at t≈{t_done:.1f}s "
                      f"(thresh={threshold:.2f}px, diff={diff:.2f}px)")
                return
        else:
            stable_count = 0


def toggle_active_tool() -> None:
    """Switch active tool via MCP API (T0→T1 or T1→T0).

    Uses PATCH /api/toggle_active_tool which calls bpm set_active_tool() — handles
    H2D firmware requirements correctly. Do NOT use raw G-code T0/T1 for tool switching.
    The route reads current active tool from printer state and toggles to the other,
    so two calls always produce T0→T1→T0 without needing to track current tool state.
    """
    params = urllib.parse.urlencode({"printer": PRINTER_NAME})
    req = urllib.request.Request(
        f"{API_BASE}/toggle_active_tool?{params}",
        method="PATCH",
    )
    with urllib.request.urlopen(req) as resp:
        resp.read()


def wait_for_tool_change_complete(
    timeout: float = TOOL_CHANGE_TIMEOUT_S,
    noise_floor: float = TOOL_CHANGE_NOISE_FLOOR_PX,
) -> None:
    """Block until tool-change carriage motion settles, detected by visual frame stability.

    Analogous to wait_for_home_complete() but uses 480p at ~2s effective poll rate — tool
    change is a 7–11s event (T0→T1 ~7.2s, T1→T0 ~11.1s) vs ~47s for G28.

    [VERIFIED: empirical] TOOL_CHANGE_NOISE_FLOOR_PX = 2.70px, TOOL_CHANGE_TIMEOUT_S = 16.2s
    measured by calibrate_tool_change_settle.py at (80,80,Z=2), 480p, 3 trials, 2026-03-13.

    Args:
        timeout: Hard timeout in seconds. Default TOOL_CHANGE_TIMEOUT_S (16.2s).
        noise_floor: Stability baseline in px avg diff. Default TOOL_CHANGE_NOISE_FLOOR_PX.
    """
    threshold = noise_floor * TOOL_CHANGE_NOISE_MULT
    prev = np.array(_snapshot_at_res(TOOL_CHANGE_SNAPSHOT_RES).convert("L"), dtype=np.float32)
    stable_count = 0
    t_start = time.time()

    while True:
        elapsed = time.time() - t_start
        if elapsed > timeout:
            raise TimeoutError(
                f"wait_for_tool_change_complete: carriage did not settle within {timeout:.0f}s"
            )
        time.sleep(TOOL_CHANGE_POLL_S)
        cur = np.array(_snapshot_at_res(TOOL_CHANGE_SNAPSHOT_RES).convert("L"), dtype=np.float32)
        diff = float(np.mean(np.abs(cur - prev)))
        prev = cur
        if diff <= threshold:
            stable_count += 1
            if stable_count >= TOOL_CHANGE_STABLE_N:
                t_done = elapsed - (TOOL_CHANGE_STABLE_N - 1) * TOOL_CHANGE_POLL_S
                print(f"    Tool-change settled at t≈{t_done:.2f}s "
                      f"(thresh={threshold:.2f}px, diff={diff:.2f}px)")
                return
        else:
            stable_count = 0


# ---------------------------------------------------------------------------
# GCode sequences
# ---------------------------------------------------------------------------

def gcode_home_and_clearance() -> str:
    """Home all axes and move to clearance height."""
    return "\n".join([
        "G28",                          # home all axes
        "G90",                          # absolute positioning
        f"G0 Z{Z_CLEARANCE} F{F_Z}",   # move to clearance height
        "M400",                         # full stop
    ])

def gcode_move_to_corner(x: float, y: float) -> str:
    """Move to corner XY at clearance height (already at Z_CLEARANCE)."""
    return "\n".join([
        f"G0 X{x} Y{y} F{F_XY}",      # travel to corner at clearance
        "M400",                          # full stop before Z change
    ])

def gcode_descend_to_z(z: float) -> str:
    """Descend from current height to z (mm)."""
    return f"G0 Z{z} F{F_Z}\nM400"

def gcode_descend_to_capture() -> str:
    """Descend from clearance to standard capture height."""
    return gcode_descend_to_z(Z_CAPTURE)

def gcode_ascend_to_clearance() -> str:

# ---------------------------------------------------------------------------
# Nozzle detection — technique cascade
# ---------------------------------------------------------------------------
# Four techniques are applied to the SAME frame pair (no extra GCode latency).
# Each technique uses a pre-computed local-crop diff.
#
# Technique selection rationale:
#   centroid     — baseline; works well for clean, isolated nozzle tips
#   top_pct      — centroid of top-5% brightest pixels; cuts through large
#                  diffuse blobs (L243/L175 toolhead contamination)
#   weighted     — intensity-weighted centroid; bright tip dominates diffuse body
#   sparse_bright— 85th-pct threshold in inner half of crop; finds sharpest
#                  bright spot, ignores border noise
#
# CONF_ACCEPT threshold stops the cascade early; all four run only when needed.
# ---------------------------------------------------------------------------

def _crop_and_diff(ref_img: Image.Image, nozzle_img: Image.Image,
                   expected_px: tuple, search_radius: int):
    """
    Compute local-crop diff around expected_px.
    Returns (local_diff, x_lo, y_lo, x_hi, y_hi, use_full_frame).
    """
    ref = np.array(ref_img.convert('L'), dtype=np.float32)
    noz = np.array(nozzle_img.convert('L'), dtype=np.float32)
    h, w = ref.shape
    ex, ey = int(expected_px[0]), int(expected_px[1])

    y_lo = max(0, ey - search_radius)
    y_hi = min(h, ey + search_radius)
    x_lo = max(0, ex - search_radius)
    x_hi = min(w, ex + search_radius)

    use_full_frame = (x_hi - x_lo < 20) or (y_hi - y_lo < 20)
    if use_full_frame:
        x_lo, x_hi, y_lo, y_hi = 0, w, 0, h

    local_diff = np.abs(noz[y_lo:y_hi, x_lo:x_hi] - ref[y_lo:y_hi, x_lo:x_hi])
    return local_diff, x_lo, y_lo, x_hi, y_hi, use_full_frame


def _technique_centroid(diff, x_lo, y_lo):
    """Uniform centroid with adaptive threshold cascade (15→25→40)."""
    cx, cy, count = 0.0, 0.0, 0
    for thresh in [15, 25, 40]:
        mask = diff > thresh
        n = int(mask.sum())
        if n < 5:
            break
        ys, xs = np.where(mask)
        cx = float(np.mean(xs)) + x_lo
        cy = float(np.mean(ys)) + y_lo
        count = n
    return cx, cy, count, f"centroid(thresh_cascade count={count})"


def _technique_top_pct(diff, x_lo, y_lo, thresh_fraction=0.70):
    """Centroid of pixels >= 70% of dmax. Selects nozzle tip cluster over large carriage blobs."""
    dmax = float(diff.max())
    thresh = max(5.0, dmax * thresh_fraction)
    mask = diff >= thresh
    count = int(mask.sum())
    if count < 3:
        return 0.0, 0.0, 0, f"top_pct: count={count}"
    ys, xs = np.where(mask)
    cx = float(np.mean(xs)) + x_lo
    cy = float(np.mean(ys)) + y_lo
    return cx, cy, count, f"top_pct(thresh={thresh:.0f} count={count})"


def _technique_weighted(diff, x_lo, y_lo):
    """Intensity-weighted centroid — bright pixels dominate diffuse background."""
    mask = diff > 10
    count = int(mask.sum())
    if count < 5:
        return 0.0, 0.0, 0, "weighted: no pixels > 10"
    weights = diff * mask.astype(np.float32)
    total_w = float(weights.sum())
    if total_w < 1.0:
        return 0.0, 0.0, 0, "weighted: zero weight"
    row_idx = np.arange(diff.shape[0], dtype=np.float32).reshape(-1, 1)
    col_idx = np.arange(diff.shape[1], dtype=np.float32).reshape(1, -1)
    cx = float((col_idx * weights).sum() / total_w) + x_lo
    cy = float((row_idx * weights).sum() / total_w) + y_lo
    return cx, cy, count, f"weighted(total_w={total_w:.0f} count={count})"


def _technique_sparse_bright(diff, x_lo, y_lo):
    """
    High-threshold centroid in inner quarter of crop.
    Finds the sharpest bright cluster — ignores border noise and diffuse halos.
    """
    ch, cw = diff.shape
    # inner quarter: [ch/4 : 3ch/4, cw/4 : 3cw/4]
    r0, r1 = ch // 4, 3 * ch // 4
    c0, c1 = cw // 4, 3 * cw // 4
    inner = diff[r0:r1, c0:c1]
    flat = inner.ravel()
    active = flat[flat > 5]
    if len(active) < 10:
        return 0.0, 0.0, 0, "sparse_bright: insufficient inner signal"
    thresh = np.percentile(active, 85)
    mask = inner >= thresh
    count = int(mask.sum())
    if count < 3:
        return 0.0, 0.0, 0, f"sparse_bright: count={count}"
    ys, xs = np.where(mask)
    cx = float(np.mean(xs)) + x_lo + c0
    cy = float(np.mean(ys)) + y_lo + r0
    return cx, cy, count, f"sparse_bright(inner85pct thresh={thresh:.0f} count={count})"


def _technique_bottom_pct(diff, x_lo, y_lo, thresh_fraction=0.40, bottom_frac=0.25):
    """
    Centroid of the bottom (highest Y) portion of significant thermal pixels.

    Specifically targets the nozzle TIP — the lowest visible thermal feature in
    the heat_halo differential frame. The heater block body (Z≈25-30mm) appears
    near the TOP of the thermal region (lower Y pixel) due to camera parallax.
    The nozzle tip (Z≈2mm) is at the BOTTOM of the carriage (higher Y pixel).

    Taking the bottom 25% of pixels above 40% dmax isolates the tip rather
    than the heater block body that top_pct finds.

    Use only in heat_halo mode (thermal diff frames). Not appropriate for
    Z-descent frames where there is no persistent thermal gradient by height.
    """
    dmax = float(diff.max())
    thresh = max(5.0, dmax * thresh_fraction)
    mask = diff >= thresh
    ys, xs = np.where(mask)
    count = int(mask.sum())
    if count < 5:
        return 0.0, 0.0, 0, f"bottom_pct: count={count}"
    # Take highest-Y values (nozzle tip = lowest on carriage = highest Y in image)
    y_cutoff = np.percentile(ys, (1.0 - bottom_frac) * 100)
    tip_mask = ys >= y_cutoff
    by = ys[tip_mask]
    bx = xs[tip_mask]
    if len(bx) < 3:
        return 0.0, 0.0, 0, f"bottom_pct: tip count={len(bx)}"
    cx = float(np.mean(bx)) + x_lo
    cy = float(np.mean(by)) + y_lo
    return cx, cy, len(bx), (
        f"bottom_pct(thresh={thresh:.0f} bot{int(bottom_frac*100)}pct count={len(bx)})")


def _score_result(cx, cy, count, dmax, crop_area, use_full_frame, expected_px, search_radius):
    """Compute confidence for a (cx, cy, count) detection result."""
    if count == 0:
        return 0.0
    ex, ey = expected_px
    # Signal quality: how strong is the peak diff? Good signal ≥ 64 counts.
    signal_strength = min(1.0, dmax / 64.0)
    # Blob quality: nozzle tip is a small focused spot (≤500px).
    # Large blobs (>500px) indicate carriage body movement, not nozzle tip.
    blob_score = min(1.0, 500.0 / max(count, 500))
    confidence = min(1.0, signal_strength * blob_score)
    if use_full_frame:
        confidence = min(confidence, 0.5)
    offset = np.sqrt((cx - ex) ** 2 + (cy - ey) ** 2)
    if not use_full_frame and offset > search_radius * 0.8:
        confidence *= 0.3   # was 0.5 — stronger penalty keeps top_pct below sparse_bright (0.32+)
    return confidence


def detect_nozzle_best(ref_img: Image.Image, nozzle_img: Image.Image,
                       expected_px: tuple,
                       search_radius: int = 100,
                       heat_halo_mode: bool = False) -> tuple:
    """
    Detect nozzle centroid using a technique cascade.

    Standard mode (heat_halo_mode=False):
        Tries top_pct → weighted → centroid → sparse_bright on the same
        pre-computed local-crop diff. No extra GCode or captures.
        Stops as soon as confidence reaches CONF_ACCEPT.

    heat_halo_mode=True:
        Uses bottom_pct → top_pct → sparse_bright → centroid cascade instead.
        bottom_pct targets the nozzle TIP (lowest thermal feature = highest Y)
        rather than the heater block body (highest brightness = lower Y).
        Use when the diff is a thermal halo frame (T0-hot minus idle).

    Args:
        ref_img:         frame at Z_CLEARANCE or idle thermal baseline
        nozzle_img:      frame at capture height or T0/T1 hot
        expected_px:     (x, y) approximate nozzle pixel location (REQUIRED)
        search_radius:   half-width of local crop (default 100px)
        heat_halo_mode:  if True, use bottom_pct-first cascade for thermal frames

    Returns:
        (cx, cy, confidence, technique_name) — centroid in full-frame pixel
        coordinates, 0-1 confidence, and name of winning technique.
    """
    if expected_px is None:
        print("  WARNING: expected_px required for detection — skipping")
        return (0, 0, 0.0, "none")

    diff, x_lo, y_lo, x_hi, y_hi, use_full_frame = _crop_and_diff(
        ref_img, nozzle_img, expected_px, search_radius)

    dmax = float(diff.max())
    crop_area = (y_hi - y_lo) * (x_hi - x_lo)

    if dmax < 5.0:
        label = "full-frame" if use_full_frame else f"crop[{y_lo}:{y_hi},{x_lo}:{x_hi}]"
        print(f"  {label}: diff_max={dmax:.1f} — no signal")
        return (0, 0, 0.0, "none")

    if use_full_frame:
        print(f"  WARNING: expected_px {expected_px} near edge — full-frame fallback")

    if heat_halo_mode:
        # bottom_pct first: finds nozzle tip (highest Y = lowest Z feature) rather
        # than the heater block body (highest brightness = higher Z, lower Y pixel).
        techniques = [
            ("bottom_pct",    lambda: _technique_bottom_pct(diff, x_lo, y_lo)),
            ("top_pct",       lambda: _technique_top_pct(diff, x_lo, y_lo)),
            ("sparse_bright", lambda: _technique_sparse_bright(diff, x_lo, y_lo)),
            ("centroid",      lambda: _technique_centroid(diff, x_lo, y_lo)),
        ]
    else:
        techniques = [
            ("top_pct",       lambda: _technique_top_pct(diff, x_lo, y_lo)),
            ("weighted",      lambda: _technique_weighted(diff, x_lo, y_lo)),
            ("centroid",      lambda: _technique_centroid(diff, x_lo, y_lo)),
            ("sparse_bright", lambda: _technique_sparse_bright(diff, x_lo, y_lo)),
        ]

    best_cx, best_cy, best_conf, best_name = 0.0, 0.0, 0.0, "none"
    ex, ey = int(expected_px[0]), int(expected_px[1])

    for tech_name, tech_fn in techniques:
        cx, cy, count, info = tech_fn()
        conf = _score_result(cx, cy, count, dmax, crop_area,
                             use_full_frame, expected_px, search_radius)
        offset = np.sqrt((cx - ex)**2 + (cy - ey)**2) if count > 0 else 0
        label = "full" if use_full_frame else f"crop[{y_lo}:{y_hi},{x_lo}:{x_hi}]"
        print(f"  [{tech_name:14s}] {label}: dmax={dmax:.1f} {info}"
              f" → ({cx:.1f},{cy:.1f}) off={offset:.1f}px conf={conf:.3f}")
        if conf > best_conf:
            best_cx, best_cy, best_conf, best_name = cx, cy, conf, tech_name
        if best_conf >= CONF_ACCEPT:
            print(f"  ✓ Cascade stopped at '{tech_name}' (conf={conf:.3f} ≥ {CONF_ACCEPT})")
            break

    return (best_cx, best_cy, best_conf, best_name)


# ---------------------------------------------------------------------------
# Thermal toggle detection (heat_halo) — last-resort technique
# ---------------------------------------------------------------------------
# When all Z-movement-based techniques fail (e.g. F005 where the camera
# cannot see the nozzle move at the far front-left), we use the nozzle's
# thermal signature instead.
#
# Protocol per world point:
#   1. Capture baseline frame at current Z with both nozzles at idle (38°C)
#   2. Heat T0 only → wait HEAT_WAIT → capture T0-hot frame → detect T0 centroid
#   3. Cool T0, heat T1 only → wait HEAT_WAIT → capture T1-hot frame → detect T1 centroid
#   4. Cool both to idle
#
# Returns detections for BOTH nozzles at a single world coordinate, enabling
# separate calibration H matrices (or per-nozzle offset vectors).
#
# Note: The detected pixel is the centroid of the thermal halo, not the
# geometric nozzle tip. NL_FROM_HOTSPOT_OFFSET correction is tracked
# separately (nl-hotspot-offset-recalib todo). For initial calibration,
# the halo centroid is used directly.
# ---------------------------------------------------------------------------

def detect_nozzle_heat_toggle(expected_px: tuple,
                               name: str,
                               output_dir: str,
                               world_xy: tuple) -> dict:
    """
    Last-resort nozzle detection via thermal toggle between T0 and T1.

    Heats each nozzle in turn while the other stays at idle. T0 is tested
    first (already active on entry — no toggle needed). T1 requires an explicit
    tool change: toggle_active_tool() moves carriage so T1 tip is at world_xy,
    then idle baseline is re-captured (camera scene shifts), then T1 is heated.
    After T1 test, toggle back to T0 and re-position to restore state invariant.

    State invariant: T0 is the active tool on entry and on exit.
    Per-nozzle idle baseline is mandatory after each tool change + re-position:
    the camera scene shifts when the active tool changes (carriage shifts).

    Args:
        expected_px: (x, y) approximate nozzle pixel position (used for crop)
        name:        point name (e.g. "F005") — used for image filenames
        output_dir:  directory for saved frames
        world_xy:    (wx, wy) world coordinates of this calibration point in mm

    Returns:
        dict with keys "T0" and "T1", each containing:
          {"cx": float, "cy": float, "conf": float, "technique": str}
        or {"cx": 0, "cy": 0, "conf": 0.0, "technique": "none"} if failed
    """
    wx, wy = world_xy
    results = {}

    # --- T0 test (T0 is already active on entry — no toggle needed) ---
    print(f"  [heat_halo] Capturing T0 idle baseline (both nozzles at {T_IDLE}°C)...")
    set_nozzle_temp(T_IDLE, extruder=0)
    set_nozzle_temp(T_IDLE, extruder=1)
    time.sleep(2)  # brief settle after any prior temp change
    t0_idle_frame = get_snapshot()
    t0_idle_frame.save(os.path.join(output_dir, f"{name}_heat_t0_idle.png"))

    print(f"  [heat_halo] Heating T0 to {T_HEAT}°C (T1 at {T_IDLE}°C)...")
    print(f"  [heat_halo] Waiting {HEAT_WAIT}s for stable thermal halo...")
    heat_and_wait(T_HEAT, T_IDLE, HEAT_WAIT)

    t0_hot = get_snapshot()
    t0_hot.save(os.path.join(output_dir, f"{name}_heat_t0.png"))

    cx, cy, conf, tech = detect_nozzle_best(
        t0_idle_frame, t0_hot, expected_px=expected_px, search_radius=150,
        heat_halo_mode=True)
    print(f"  [heat_halo] T0: ({cx:.1f},{cy:.1f}) conf={conf:.3f} [{tech}]")
    results["T0"] = {"cx": cx, "cy": cy, "conf": conf, "technique": tech}

    # Temperature escalation: if T0 halo is still weak, retry at T_HEAT_ESCALATE.
    # Fixes weak-zone points (B260, R243, F345 region) where dmax < 40 at 180°C.
    if conf < CONF_RESCUE and T_HEAT_ESCALATE > T_HEAT:
        print(f"  [heat_halo] T0 weak (conf={conf:.3f} < {CONF_RESCUE}); "
              f"escalating to {T_HEAT_ESCALATE}°C...")
        heat_and_wait(T_HEAT_ESCALATE, T_IDLE, HEAT_WAIT)
        hot2 = get_snapshot()
        hot2.save(os.path.join(output_dir, f"{name}_heat_t0_escalated.png"))
        cx2, cy2, conf2, tech2 = detect_nozzle_best(
            t0_idle_frame, hot2, expected_px=expected_px, search_radius=150,
            heat_halo_mode=True)
        print(f"  [heat_halo] T0 escalated: ({cx2:.1f},{cy2:.1f}) conf={conf2:.3f} [{tech2}]")
        if conf2 > conf:
            results["T0"] = {"cx": cx2, "cy": cy2, "conf": conf2,
                             "technique": tech2 + "_escalated"}
            print(f"  [heat_halo] ✓ Escalated T0 accepted (conf {conf:.3f} → {conf2:.3f})")

    # Cool T0 before tool change
    print(f"  [heat_halo] Cooling T0 back to {T_IDLE}°C...")
    set_nozzle_temp(T_IDLE, extruder=0)
    set_nozzle_temp(T_IDLE, extruder=1)
    time.sleep(5)  # brief cool-down gap before tool change

    # --- T1 test: toggle T0→T1, move to world_xy (T1 tip now at wx,wy), re-capture idle ---
    # T1 must be physically at (wx,wy) before heating — firmware applies extruder offset
    # automatically when T1 is active (carriage shifts so T1 tip is at commanded position).
    print(f"  [heat_halo] Toggling T0→T1; T1 tip will position at ({wx},{wy})...")
    toggle_active_tool()
    wait_for_tool_change_complete()
    send_gcode(f"G0 X{wx} Y{wy} F{F_XY}")
    time.sleep(SETTLE_SECONDS_XY)

    # Mandatory re-capture: camera scene shifted (carriage shifted to T1 position).
    print(f"  [heat_halo] Capturing T1 idle baseline at T1 position (both at {T_IDLE}°C)...")
    t1_idle_frame = get_snapshot()
    t1_idle_frame.save(os.path.join(output_dir, f"{name}_heat_t1_idle.png"))

    print(f"  [heat_halo] Heating T1 to {T_HEAT}°C (T0 at {T_IDLE}°C)...")
    print(f"  [heat_halo] Waiting {HEAT_WAIT}s for stable thermal halo...")
    heat_and_wait(T_IDLE, T_HEAT, HEAT_WAIT)

    t1_hot = get_snapshot()
    t1_hot.save(os.path.join(output_dir, f"{name}_heat_t1.png"))

    cx, cy, conf, tech = detect_nozzle_best(
        t1_idle_frame, t1_hot, expected_px=expected_px, search_radius=150,
        heat_halo_mode=True)
    print(f"  [heat_halo] T1: ({cx:.1f},{cy:.1f}) conf={conf:.3f} [{tech}]")
    results["T1"] = {"cx": cx, "cy": cy, "conf": conf, "technique": tech}

    # Cool T1 and restore T0 as active tool + restore carriage position
    print(f"  [heat_halo] Cooling T1 back to {T_IDLE}°C...")
    set_nozzle_temp(T_IDLE, extruder=0)
    set_nozzle_temp(T_IDLE, extruder=1)
    time.sleep(5)

    print(f"  [heat_halo] Toggling T1→T0; restoring T0 tip to ({wx},{wy})...")
    toggle_active_tool()
    wait_for_tool_change_complete()
    send_gcode(f"G0 X{wx} Y{wy} F{F_XY}")
    time.sleep(SETTLE_SECONDS_XY)

    return results


# ---------------------------------------------------------------------------
# DLT solve (3 correspondences + constraints)
# ---------------------------------------------------------------------------

def solve_projection_3point(world_pts: np.ndarray, pixel_pts: np.ndarray,
                             outlier_thresh_px: float = 30.0,
                             min_inlier_pts: int = 4) -> dict:
    """
    Solve for homography H from N world↔pixel correspondences (N ≥ 4).

    Uses Hartley-normalized DLT with iterative outlier rejection: after each
    solve the worst-residual point is dropped if it exceeds outlier_thresh_px,
    then H is re-solved from the remaining inliers. Stops when no outlier
    exceeds the threshold or min_inlier_pts is reached.

    The returned H and reproj_err reflect the inlier set only.  dropped_outlier_indices
    lists which original-array rows were rejected (worst-first order).
    """
    n = len(world_pts)
    assert n >= 4, "Need at least 4 correspondences"

    # Hartley normalization (mandatory — raw DLT condition numbers reach 7M+ without it)
    def _norm_T(pts):
        c = pts.mean(axis=0)
        d = np.linalg.norm(pts - c, axis=1).mean()
        s = np.sqrt(2) / max(d, 1e-9)
        return np.array([[s, 0, -s*c[0]], [0, s, -s*c[1]], [0, 0, 1.]])

    def _solve_dlt(w_pts, p_pts):
        m = len(w_pts)
        Tw = _norm_T(w_pts); Tp = _norm_T(p_pts)
        wn = (Tw @ np.column_stack([w_pts, np.ones(m)]).T).T[:, :2]
        pn = (Tp @ np.column_stack([p_pts, np.ones(m)]).T).T[:, :2]
        A = []
        for i in range(m):
            X, Y = wn[i]; u, v = pn[i]
            A.append([X, Y, 1, 0, 0, 0, -u*X, -u*Y, -u])
            A.append([0, 0, 0, X, Y, 1, -v*X, -v*Y, -v])
        _, S, Vt = np.linalg.svd(np.array(A))
        h = Vt[-1].reshape(3, 3); h /= h[2, 2]
        H = np.linalg.inv(Tp) @ h @ Tw; H /= H[2, 2]
        return H, S

    def _reproj_errors(H, w_pts, p_pts):
        errs = []
        for i in range(len(w_pts)):
            X, Y = w_pts[i]
            pt_h = H @ np.array([X, Y, 1.0]); pt_h /= pt_h[2]
            errs.append(np.sqrt((pt_h[0]-p_pts[i][0])**2 + (pt_h[1]-p_pts[i][1])**2))
        return np.array(errs)

    # Initial solve with all points
    H, S = _solve_dlt(world_pts, pixel_pts)

    # Iterative outlier rejection
    inlier_mask = np.ones(n, dtype=bool)
    dropped_indices = []

    while np.sum(inlier_mask) > min_inlier_pts:
        w_in = world_pts[inlier_mask]; p_in = pixel_pts[inlier_mask]
        errs = _reproj_errors(H, w_in, p_in)
        worst_local = int(np.argmax(errs))
        if errs[worst_local] <= outlier_thresh_px:
            break
        worst_global = int(np.where(inlier_mask)[0][worst_local])
        dropped_indices.append(worst_global)
        inlier_mask[worst_global] = False
        H, S = _solve_dlt(world_pts[inlier_mask], pixel_pts[inlier_mask])

    # Final inlier-only reproj error
    inlier_errs = _reproj_errors(H, world_pts[inlier_mask], pixel_pts[inlier_mask])
    reproj_err = float(np.mean(inlier_errs))

    # Zc estimate from inlier pixel span
    px_in = pixel_pts[inlier_mask]
    px_diag = np.sqrt((px_in[0][0] - px_in[-1][0])**2 + (px_in[0][1] - px_in[-1][1])**2)
    bed_diag_mm = np.sqrt(350**2 + 320**2)  # ~474mm
    f_est = 1200  # rough H2D focal length estimate (pixels)
    Zc_est = f_est * bed_diag_mm / max(px_diag, 1.0)

    return {
        "H": H.tolist(),
        "Zc_estimate_mm": round(float(Zc_est), 1),
        "reprojection_error_px": round(reproj_err, 2),
        "n_inliers": int(np.sum(inlier_mask)),
        "n_total": n,
        "dropped_outlier_indices": dropped_indices,
        "success": bool(reproj_err < 30.0 and 200 < Zc_est < 3000),
        "singular_values": S.tolist(),
    }

# ---------------------------------------------------------------------------
# coord_transform.py auto-update
# ---------------------------------------------------------------------------

# World-space coordinates of the 4 canonical plate corners (mm).
# These match the FL/FR/NR/NL keys used in coord_transform.py SHELL dict.
#   FL = far-left  = back-left  = (5,   315)
#   FR = far-right = back-right = (345, 315)  ← corner of the PLATE (camera is front-left)
#   NR = near-right= front-right= (345, 5)
#   NL = near-left = front-left = (5,   5)   ← typically below camera frame
_CORNER_WORLD = {
    "FL": (5,   315),
    "FR": (345, 315),
    "NR": (345, 5),
    "NL": (5,   5),
}

def _project(H: list, wx: float, wy: float) -> tuple:
    """Project world point (wx, wy) through H to pixel (px, py)."""
    h = H
    v0 = h[0][0]*wx + h[0][1]*wy + h[0][2]
    v1 = h[1][0]*wx + h[1][1]*wy + h[1][2]
    v2 = h[2][0]*wx + h[2][1]*wy + h[2][2]
    return (round(v0 / v2), round(v1 / v2))


def update_coord_transform(dlt_result: dict) -> bool:
    """
    Project the 4 canonical plate corners through the new H matrix and patch
    the SHELL dict + docstring header in camera/coord_transform.py.

    Returns True on success, False if the file could not be updated (e.g.
    pattern not found — does not raise so calibration can still be saved).
    """
    coord_path = os.path.join(os.path.dirname(__file__), "coord_transform.py")
    if not os.path.exists(coord_path):
        # Fall back to production location when running from session-state dir
        coord_path = os.path.expanduser("~/bambu-mcp/camera/coord_transform.py")
    if not os.path.exists(coord_path):
        print(f"⚠️  update_coord_transform: coord_transform.py not found, skipping")
        return False

    H = dlt_result.get("H")
    if H is None:
        print("⚠️  update_coord_transform: no H matrix in dlt_result, skipping")
        return False

    # Project each corner through H
    new_shell = {k: _project(H, *xy) for k, xy in _CORNER_WORLD.items()}

    # Build the new SHELL line
    shell_line = (
        f'SHELL  = {{"FL": {new_shell["FL"]},   '
        f'"NL": {new_shell["NL"]}, '
        f'"NR": {new_shell["NR"]},  '
        f'"FR": {new_shell["FR"]}}}'
    )

    # Build the new docstring calibration line
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reproj   = dlt_result.get("reprojection_error_px", "?")
    n_pts    = dlt_result.get("n_points", "?")
    cal_line = (
        f"SHELL corners calibrated {date_str} at Z=2, "
        f"chamber light OFF, N={n_pts} points, reproj={reproj}px."
    )

    content = open(coord_path).read()

    # Replace SHELL = {...} — use re.DOTALL so '.' spans newlines if dict ever
    # becomes multi-line.  [^}]* would also work but explicit DOTALL is clearer.
    new_content = re.sub(
        r'SHELL\s*=\s*\{[^}]*\}',
        shell_line,
        content,
        count=1,
        flags=re.DOTALL,
    )
    if new_content == content:
        print("⚠️  update_coord_transform: SHELL pattern not matched — coord_transform.py unchanged")
        return False

    # Replace the calibration datestamp comment in the docstring
    new_content = re.sub(
        r'SHELL corners calibrated [^\n]+',
        cal_line,
        new_content,
        count=1,
    )

    # Update the ORIGIN comment to reflect the new NL pixel
    new_content = re.sub(
        r'(ORIGIN = SHELL\["NL"\]\s+#\s*)\([^)]+\)',
        lambda m: m.group(1) + str(new_shell["NL"]),
        new_content,
    )

    open(coord_path, "w").write(new_content)
    print(f"✅ coord_transform.py updated: SHELL={new_shell}")
    print(f"   Path: {coord_path}")
    return True


# ---------------------------------------------------------------------------
# Main calibration sequence
# ---------------------------------------------------------------------------

def run_calibration(supplement: bool = False):
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=" * 60)
    print("H2D Perimeter Calibration — Issue #10 Track 3")
    if supplement:
        print("MODE: --supplement (probe only missing/new points)")
    print("=" * 60)

    # Pre-flight: verify printer is idle
    state = get_printer_state()
    print(f"\nPrinter state: {state}")
    if state.upper() in ("RUNNING", "PREPARE"):
        print("ERROR: Printer is actively printing. Cannot run calibration.")
        print("Wait for job to finish or pause first.")
        sys.exit(1)

    # In supplement mode, load existing results so we can skip already-good points.
    results = {}
    json_path = os.path.join(OUTPUT_DIR, "calibration_result.json")
    if supplement and os.path.exists(json_path):
        with open(json_path) as f:
            prev = json.load(f)
        raw_corners = prev.get("corners", {})
        # Only carry forward non-excluded points with real confidence.
        for k, v in raw_corners.items():
            if not k.startswith("__") and k not in HARD_EXCLUDE and v.get("confidence", 0) > 0.01:
                results[k] = v
        print(f"\n  Loaded {len(results)} existing good results: {sorted(results.keys())}")
    elif supplement:
        print(f"\n  --supplement requested but no prior JSON at {json_path}; running full probe.")
        supplement = False

    # Determine which points still need probing.
    active_points = [
        (name, wx, wy) for name, wx, wy in PERIMETER_POINTS
        if name not in HARD_EXCLUDE and name not in results
    ]
    skipped_exclude = [n for n, _, _ in PERIMETER_POINTS if n in HARD_EXCLUDE]
    skipped_existing = [n for n, _, _ in PERIMETER_POINTS
                        if n not in HARD_EXCLUDE and n in results]

    print(f"\nPoints to probe:    {len(active_points)} → {[n for n,_,_ in active_points]}")
    if skipped_existing:
        print(f"Points skipped (already have): {skipped_existing}")
    print(f"Points hard-excluded: {skipped_exclude}")
    print(f"Z_CLEARANCE={Z_CLEARANCE}mm, Z_CAPTURE={Z_CAPTURE}mm")
    print(f"XY speed: F{F_XY} ({F_XY/60:.0f} mm/s), Z speed: F{F_Z} ({F_Z/60:.0f} mm/s)")
    print()

    # Step 1: Home and move to clearance (skip in supplement mode)
    if not supplement:
        print(">>> Homing all axes...")
        result = send_gcode(gcode_home_and_clearance())
        print(f"    Home result: {result}")
        wait_for_home_complete()
    else:
        print(">>> Supplement mode: skipping home, moving to clearance Z only...")
        send_gcode(f"G0 Z{Z_CLEARANCE} F{F_Z}\nM400")
        time.sleep(3)

    # Capture a "home" reference frame
    print(">>> Capturing reference frame...")
    home_frame = get_snapshot()
    home_frame.save(os.path.join(OUTPUT_DIR, "home_reference.png"))
    frame_w, frame_h = home_frame.size
    print(f"    Saved home reference ({frame_w}x{frame_h})")

    # Load prior calibration H and project all points to get expected pixels.
    # The expected pixels guide local-crop detection (search_radius applies around them).
    # If no prior calibration, fall back to linear interpolation from bed corners.
    NATIVE_W, NATIVE_H = 1280, 720   # prior H was fitted at 720p (1280×720)
    scale_x, scale_y = frame_w / NATIVE_W, frame_h / NATIVE_H
    expected_pixels = {}  # name → (scaled_px, scaled_py)

    if os.path.exists(CAL_JSON_PATH):
        with open(CAL_JSON_PATH) as f:
            cal_data = json.load(f)
        H_prior = np.array(cal_data["dlt"]["H"])
        print(f"\n    Loaded prior calibration: {CAL_JSON_PATH}")
        print(f"    Projecting {len(active_points)} probe points through prior H:")
        for name, wx, wy in active_points:
            v = H_prior @ np.array([wx, wy, 1.0])
            native_px, native_py = v[0] / v[2], v[1] / v[2]
            scaled_px = int(native_px * scale_x)
            scaled_py = int(native_py * scale_y)
            expected_pixels[name] = (scaled_px, scaled_py)
            print(f"      {name}: world=({wx},{wy}) → native=({native_px:.0f},{native_py:.0f})"
                  f" → scaled=({scaled_px},{scaled_py})")
    else:
        print(f"\n    ⚠️  No prior calibration at {CAL_JSON_PATH}")
        print("    Falling back to rough linear interpolation for expected pixels.")
        # Rough affine: back row maps to ~y=200, front row ~y=450; X maps linearly
        # This is a very rough approximation — detection may fall back to full frame
        for name, wx, wy in active_points:
            t_x = (wx - 5) / 340.0
            t_y = (wy - 40) / 275.0
            raw_px = 54 + t_x * 624              # B005.x=54, B345.x=678 (720p)
            raw_py = 452 - t_y * (452 - 197)     # F005.y=452, B005.y=197 (720p, inverted)
            expected_pixels[name] = (int(raw_px), int(raw_py))
            print(f"      {name}: world=({wx},{wy}) → rough=({int(raw_px)},{int(raw_py)})")

    # Step 2: Visit each perimeter point (active_points only; results pre-loaded in supplement mode)
    back_row_detected_y: list = []  # running pixel-Y values for back-row points (wy==BACK_ROW_WORLD_Y)
    for name, wx, wy in active_points:
        print(f"\n{'─' * 50}")
        print(f"Point {name}: world=({wx}, {wy})")
        print(f"{'─' * 50}")

        expected = expected_pixels.get(name)

        # FIXED_POINTS dict is empty (see comment at definition). This block is a no-op
        # but retained as a safety net in case entries are re-added in future.
        if name in FIXED_POINTS:
            fpx, fpy = FIXED_POINTS[name]
            print(f"  *** FIXED_POINT {name}: authoritative ({fpx},{fpy})")
            results[name] = {
                "world_xy": [wx, wy],
                "pixel_xy": [float(fpx), float(fpy)],
                "confidence": 0.80,
                "expected_pixel": list(expected) if expected else None,
                "technique": "fixed_point_authoritative",
                "T1_pixel_xy": None,
            }
            continue

        print(f"  >>> Moving to X{wx} Y{wy} at Z{Z_CLEARANCE}...")
        send_gcode(gcode_move_to_corner(wx, wy))
        time.sleep(SETTLE_SECONDS_XY)

        # Multi-probe: capture PROBES_PER_POINT times, keep highest-confidence result.
        # Reference frame is captured once per point at Z_CLEARANCE; each probe
        # descends to Z_CAPTURE, captures, ascends, and detects.  The nozzle
        # re-enters the same pixel position each time so all probes share the
        # same reference.
        print(f"  >>> Capturing reference frame at Z{Z_CLEARANCE}...")
        ref_frame = get_snapshot()
        ref_path = os.path.join(OUTPUT_DIR, f"{name}_ref_Z{Z_CLEARANCE}.png")
        ref_frame.save(ref_path)
        print(f"      Saved: {ref_path}")

        best_cx, best_cy, best_conf = 0.0, 0.0, -1.0
        best_probe_idx = 0
        best_technique = "none"
        probe_results: list = []  # all probe (cx, cy, conf, tech) — for repeatability check
        for probe in range(1, PROBES_PER_POINT + 1):
            print(f"  >>> Probe {probe}/{PROBES_PER_POINT}: descending to Z{Z_CAPTURE}...")
            send_gcode(gcode_descend_to_z(Z_CAPTURE))
            time.sleep(SETTLE_SECONDS_Z)

            nozzle_frame = get_snapshot()
            noz_path = os.path.join(OUTPUT_DIR, f"{name}_nozzle_Z{Z_CAPTURE}_p{probe}.png")
            nozzle_frame.save(noz_path)

            cx_px, cy_px, conf, tech = detect_nozzle_best(ref_frame, nozzle_frame,
                                                           expected_px=expected)
            print(f"      [{probe}] Best: ({cx_px:.1f}, {cy_px:.1f}) conf={conf:.3f} [{tech}]")
            probe_results.append((cx_px, cy_px, conf, tech))

            if conf > best_conf:
                best_cx, best_cy, best_conf, best_technique = cx_px, cy_px, conf, tech
                best_probe_idx = probe
                nozzle_frame.save(os.path.join(OUTPUT_DIR, f"{name}_nozzle_Z{Z_CAPTURE}.png"))

            print(f"  >>> Ascending to Z{Z_CLEARANCE}...")
            send_gcode(gcode_ascend_to_clearance())
            time.sleep(SETTLE_SECONDS_Z)

        # Repeatability penalty: positions varying > REPROB_STDDEV_THRESH px across probes
        # indicate a drifting artifact (e.g. B260 top_pct drifting 10px/probe).
        # Penalty halves best_conf so a more stable lower-conf technique can win.
        # NOTE: early-stop removed — all probes always run for accurate std_dev measurement.
        valid_probe_pos = [(cx, cy) for cx, cy, conf, tech in probe_results if conf > 0.01]
        if len(valid_probe_pos) >= 2:
            xs = [p[0] for p in valid_probe_pos]
            ys = [p[1] for p in valid_probe_pos]
            pos_stddev = (np.std(xs) ** 2 + np.std(ys) ** 2) ** 0.5
            if pos_stddev > REPROB_STDDEV_THRESH:
                penalized = best_conf * 0.5
                print(f"  ⚠️  Repeatability: pos_stddev={pos_stddev:.1f}px > {REPROB_STDDEV_THRESH}px "
                      f"→ conf {best_conf:.3f} × 0.5 = {penalized:.3f}")
                best_conf = penalized
            else:
                print(f"      Repeatability: pos_stddev={pos_stddev:.1f}px ✓")

        # Back-row Y consistency guard: detection should land within BACK_ROW_Y_TOL px of the
        # prior-H expected Y for this specific world point.  Per-point comparison is correct
        # because perspective means expected Y increases monotonically left→right across the
        # back row (e.g. B005→204, B090→215, B260→251, B345→283 at 720p).  The old running-mean
        # approach fired false positives on right-side points whose expected Y is legitimately
        # higher than the left-side mean.
        force_heat_halo = False
        if wy == BACK_ROW_WORLD_Y and best_conf > 0.01 and expected is not None:
            expected_back_y = expected[1]  # prior H projected Y for this specific world point
            y_deviation = abs(best_cy - expected_back_y)
            if y_deviation > BACK_ROW_Y_TOL:
                print(f"  ⚠️  Back-row Y consistency: Y={best_cy:.1f}px deviates {y_deviation:.1f}px "
                      f"from expected={expected_back_y:.1f}px (>{BACK_ROW_Y_TOL}px) "
                      f"— likely carriage artifact; forcing heat_halo (pre-guard best kept as fallback)")
                force_heat_halo = True  # run heat_halo; pre-guard result stays as fallback

        # Rescue probe: if standard probes all yielded conf < CONF_RESCUE, descend
        # further to Z_RESCUE (1.0mm) for a stronger diff signal.  This helps
        # front-row and other far-from-camera zones where the nozzle is small in frame.
        if best_conf < CONF_RESCUE:
            print(f"  *** Rescue probe (conf={best_conf:.3f} < {CONF_RESCUE}): "
                  f"descending to Z{Z_RESCUE} for stronger signal...")
            # Re-capture reference at clearance before rescue descent
            print(f"  >>> Re-capturing reference at Z{Z_CLEARANCE}...")
            rescue_ref = get_snapshot()
            rescue_ref.save(os.path.join(OUTPUT_DIR, f"{name}_ref_rescue.png"))

            send_gcode(gcode_descend_to_z(Z_RESCUE))
            time.sleep(SETTLE_SECONDS_Z + 1.0)  # extra settle — larger Z delta

            rescue_frame = get_snapshot()
            rescue_frame.save(os.path.join(OUTPUT_DIR, f"{name}_nozzle_Z{Z_RESCUE}_rescue.png"))

            cx_px, cy_px, conf, tech = detect_nozzle_best(rescue_ref, rescue_frame,
                                                           expected_px=expected)
            print(f"      [rescue] Best: ({cx_px:.1f}, {cy_px:.1f}) conf={conf:.3f} [{tech}]")

            if conf > best_conf:
                best_cx, best_cy, best_conf, best_technique = cx_px, cy_px, conf, tech
                best_probe_idx = "rescue"
                rescue_frame.save(os.path.join(OUTPUT_DIR, f"{name}_nozzle_Z{Z_CAPTURE}.png"))

            send_gcode(gcode_ascend_to_clearance())
            time.sleep(SETTLE_SECONDS_Z)

        # Snapshot of best close-approach result before heat_halo may overwrite it.
        # Used by PREFER_CLOSE_APPROACH to restore Z-consistency after heat_halo runs.
        pre_heat_cx, pre_heat_cy = best_cx, best_cy
        pre_heat_conf, pre_heat_technique = best_conf, best_technique
        pre_heat_probe_idx = best_probe_idx

        # Heat-halo: if still below CONF_HEAT after rescue probe, OR guard flagged this point
        if best_conf < CONF_HEAT or force_heat_halo:
            print(f"  *** heat_halo triggered (conf={best_conf:.3f} < {CONF_HEAT}): "
                  f"thermal toggle T0/T1...")
            heat_results = detect_nozzle_heat_toggle(
                expected_px=expected,
                name=name,
                output_dir=OUTPUT_DIR,
                world_xy=(wx, wy),
            )
            # Consistency check: after tool-change fix, T0 and T1 are both positioned at
            # (wx,wy). The H matrix maps (wx,wy) to the same pixel for both nozzles.
            # Expected: T0-T1 distance < 30px (both at same world point — ACCEPT).
            # Unexpected: T0-T1 distance ≥ 30px → one detection is on an artifact (DISCARD).
            t0 = heat_results.get("T0", {})
            t1_check = heat_results.get("T1", {})
            t0_t1_dist = 0.0
            if t0.get("conf", 0) > 0.01 and t1_check.get("conf", 0) > 0.01:
                dx = t0["cx"] - t1_check["cx"]
                dy = t0["cy"] - t1_check["cy"]
                t0_t1_dist = (dx*dx + dy*dy) ** 0.5
                print(f"      [heat_halo] T0-T1 offset distance: {t0_t1_dist:.1f}px")
                if t0_t1_dist < 30.0:
                    # Both confirmed at same world point — boost confidence
                    t0["conf"] = min(t0["conf"] * 1.2, 1.0)
                    print(f"      [heat_halo] ✓ T0≈T1 ({t0_t1_dist:.1f}px < 30px) — "
                          f"both nozzles confirmed same world point; T0 conf boosted "
                          f"→ {t0['conf']:.3f}")
                else:
                    print(f"      [heat_halo] ⚠️  T0-T1 diverged ({t0_t1_dist:.1f}px ≥ 30px) — "
                          f"one detection on artifact; discarding heat_halo")
                    t0 = {}  # invalidate — fall through to keep frame-diff result

            # Use T0 result if it improves conf — or unconditionally when Y guard fired.
            # When force_heat_halo=True (back-row Y consistency guard triggered), the
            # pre-guard best result is a known carriage artifact.  heat_halo with valid
            # T0-T1 agreement (< 30px — already checked above) is authoritative
            # regardless of its absolute confidence score.
            t0_conf = t0.get("conf", 0)
            heat_halo_wins = (t0_conf > best_conf) or (force_heat_halo and t0_conf > 0.1)
            if heat_halo_wins:
                best_cx  = t0["cx"]
                best_cy  = t0["cy"]
                best_conf = t0_conf
                best_technique = f"heat_halo_T0/{t0['technique']}"
                best_probe_idx = "heat_halo"
                reason = "Y guard override" if force_heat_halo and not (t0_conf > best_conf) else "conf improved"
                print(f"      [heat_halo] T0 result accepted ({reason}): "
                      f"({best_cx:.1f},{best_cy:.1f}) conf={best_conf:.3f}")
            else:
                print(f"      [heat_halo] T0 conf={t0_conf:.3f} "
                      f"did not improve on best={best_conf:.3f}"
                      + (" (force_heat_halo=True but conf<0.1 — keeping pre-guard)" if force_heat_halo else ""))

            # Always store T1 result in results for per-nozzle calibration
            t1 = heat_results.get("T1", {})
            results.setdefault("__heat_halo_T1", {})[name] = t1


        cx_px, cy_px, conf = best_cx, best_cy, best_conf
        print(f"  >>> Best result: probe={best_probe_idx} tech={best_technique}"
              f" → ({cx_px:.1f}, {cy_px:.1f}) conf={conf:.3f}")

        # Update back-row pixel-Y reference for consistency checking of subsequent back-row points.
        # Only record if confidence is sufficient (> 0.3) to avoid poisoning with a bad detection.
        # heat_halo detections are included: Z-offset between Z=10mm and Z=2mm is ~6px — negligible.
        if wy == BACK_ROW_WORLD_Y and conf > 0.3:
            back_row_detected_y.append(cy_px)

        if conf < 0.01:
            print(f"      ⚠️  LOW CONFIDENCE — nozzle may not be visible at this point")

        # Save diff visualization of winning frame
        noz_best = Image.open(os.path.join(OUTPUT_DIR, f"{name}_nozzle_Z{Z_CAPTURE}.png"))
        ref_np = np.array(ref_frame, dtype=np.float32)
        noz_np = np.array(noz_best, dtype=np.float32)
        diff = np.abs(noz_np - ref_np)
        diff_gray = np.sqrt(np.sum(diff ** 2, axis=2))
        diff_norm = diff_gray / max(diff_gray.max(), 1.0)
        diff_img = Image.fromarray((diff_norm * 255).astype(np.uint8))
        diff_path = os.path.join(OUTPUT_DIR, f"{name}_diff.png")
        diff_img.save(diff_path)
        print(f"      Diff saved: {diff_path}")

        heat_t1 = results.get("__heat_halo_T1", {}).get(name)
        results[name] = {
            "world_xy": [wx, wy],
            "pixel_xy": [round(cx_px, 1), round(cy_px, 1)],
            "confidence": round(conf, 4),
            "expected_pixel": list(expected) if expected else None,
            "technique": best_technique,
            "T1_pixel_xy": ([round(heat_t1["cx"], 1), round(heat_t1["cy"], 1)]
                             if heat_t1 and heat_t1.get("conf", 0) > 0.01 else None),
        }

    # Step 3: Return nozzle to safe position
    print(f"\n>>> Returning to center at Z{Z_CLEARANCE}...")
    send_gcode(f"G0 X175 Y160 F{F_XY}\nM400")
    time.sleep(1)

    # Step 4: DLT solve
    # Filter to points with sufficient confidence
    good_pts = {k: v for k, v in results.items()
                if not k.startswith("__") and v["confidence"] > 0.01}

    print(f"\n{'=' * 60}")
    print(f"DLT Solve — {len(good_pts)}-Point Overdetermined Homography")
    print(f"{'=' * 60}")
    print(f"\nUsable points: {list(good_pts.keys())} ({len(good_pts)} total)")

    dlt_result = None
    if len(good_pts) >= 5:
        world_pts = np.array([v["world_xy"] for v in good_pts.values()])
        pixel_pts = np.array([v["pixel_xy"] for v in good_pts.values()])

        dlt_result = solve_projection_3point(world_pts, pixel_pts)
        pt_names = list(good_pts.keys())
        dropped = dlt_result.get("dropped_outlier_indices", [])
        n_in = dlt_result["n_inliers"]; n_tot = dlt_result["n_total"]
        print(f"\nHomography H (fitted to {n_in}/{n_tot} inliers):")
        if dropped:
            print(f"  Dropped outliers: {[pt_names[i] for i in dropped]} "
                  f"(indices {dropped})")
        H_new = np.array(dlt_result["H"])
        for row in H_new:
            print(f"  [{row[0]:10.4f} {row[1]:10.4f} {row[2]:10.4f}]")
        print(f"\nZc estimate: {dlt_result['Zc_estimate_mm']} mm")
        print(f"Reprojection error: {dlt_result['reprojection_error_px']} px  "
              f"({n_in} inliers)")
        print(f"Success: {dlt_result['success']}")

        # Per-point residuals (all points including dropped outliers)
        print(f"\nPer-point residuals (sorted worst→best):")
        per_pt_errors = []
        for i, (k, v) in enumerate(good_pts.items()):
            wx, wy = v["world_xy"]
            pt_h = H_new @ np.array([wx, wy, 1.0])
            pt_h /= pt_h[2]
            err = np.sqrt((pt_h[0] - v["pixel_xy"][0])**2 + (pt_h[1] - v["pixel_xy"][1])**2)
            is_outlier = i in dropped
            per_pt_errors.append((err, k, v["world_xy"], v["pixel_xy"],
                                   [round(pt_h[0], 1), round(pt_h[1], 1)], is_outlier))
        per_pt_errors.sort(reverse=True)
        for err, k, world, det, reproj, is_out in per_pt_errors:
            flag = "❌" if is_out else ("⚠️ " if err > 5.0 else "✅")
            print(f"  {flag} {k:5s}: world={world}  det=({det[0]},{det[1]})"
                  f"  reproj=({reproj[0]},{reproj[1]})  err={err:.2f}px"
                  + (" [DROPPED]" if is_out else ""))

        dlt_result["per_point_errors"] = [
            {"name": k, "world_xy": w, "detected_pixel": d,
             "reprojected_pixel": r, "residual_px": round(e, 3),
             "is_outlier": io}
            for e, k, w, d, r, io in sorted(per_pt_errors, key=lambda x: x[1])
        ]

        if dlt_result["success"]:
            print("\n✅ Calibration PLAUSIBLE — Zc in expected range")
        else:
            print("\n⚠️  Calibration INCONCLUSIVE — Zc outside expected range or high error")
    elif len(good_pts) >= 3:
        print(f"\n⚠️  Only {len(good_pts)} usable points — solving but not overdetermined")
        world_pts = np.array([v["world_xy"] for v in good_pts.values()])
        pixel_pts = np.array([v["pixel_xy"] for v in good_pts.values()])
        dlt_result = solve_projection_3point(world_pts, pixel_pts)
    else:
        print("\n❌ INSUFFICIENT points for DLT solve (need 5+, got {})".format(len(good_pts)))

    # Step 5: Save results
    output = {
        "printer": PRINTER_NAME,
        "bed_dimensions_mm": [350, 320],
        "z_clearance_mm": Z_CLEARANCE,
        "z_capture_mm": Z_CAPTURE,
        "corners": results,
        "dlt": dlt_result,
    }

    json_path = os.path.join(OUTPUT_DIR, "calibration_result.json")
    with open(json_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nResults saved: {json_path}")

    # Persist if DLT succeeded
    if dlt_result and dlt_result.get("success"):
        cal_dir = os.path.expanduser("~/.bambu-mcp/calibration")
        os.makedirs(cal_dir, exist_ok=True)
        cal_path = os.path.join(cal_dir, "H2D.json")
        with open(cal_path, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Calibration persisted: {cal_path}")
        update_coord_transform(dlt_result)

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    for name, data in results.items():
        if name.startswith("__"):
            continue
        status = "✅" if data["confidence"] > 0.01 else "⚠️"
        print(f"  {status} {name:5s}: world=({data['world_xy'][0]:3d},{data['world_xy'][1]:3d})"
              f" → pixel=({data['pixel_xy'][0]},{data['pixel_xy'][1]})"
              f" conf={data['confidence']:.4f}")
    if dlt_result:
        print(f"\n  DLT: Zc={dlt_result['Zc_estimate_mm']}mm, "
              f"reproj_err={dlt_result['reprojection_error_px']}px, "
              f"success={dlt_result['success']}")
    print(f"\n  All outputs in: {OUTPUT_DIR}/")

    return output

if __name__ == "__main__":
    supplement = "--supplement" in sys.argv
    run_calibration(supplement=supplement)
