#!/usr/bin/env python3
"""
calibrate_tool_change_settle.py — Empirically measure tool-change carriage settle time.

Analogous to the G28 homing calibration methodology (HOME_NOISE_FLOOR_PX, HOME_TIMEOUT_SECONDS)
but tuned for the 1–3s tool-change event using 480p snapshots at 0.3s intervals.

Calibration position: front-left quadrant (80, 80) at Z=2mm. Camera is at (0,5,75) —
front-left is closest to camera, giving the strongest 480p frame-diff signal during the
XY carriage swing. All XY travel happens at Z_CLEARANCE (10mm) per GCode motion safety
rules; measurements happen at Z_CAPTURE (2mm).

PREREQUISITES:
  - Printer must be IDLE (no active print, no homing in progress)
  - Bed must be clear of all objects, tools, and clips
  - MCP server must be running (bambu-mcp)
  - User must explicitly authorize before running (printer will move)

OUTPUT:
  Suggested constants for corner_calibration.py:
    TOOL_CHANGE_NOISE_FLOOR_PX = <measured mean>
    TOOL_CHANGE_TIMEOUT_S = <max settle time across all trials + 5s margin>

USAGE:
    cd ~/bambu-mcp
    python -u camera/calibrate_tool_change_settle.py

Mark [PROVISIONAL] → [VERIFIED: empirical] in corner_calibration.py after running.
"""

import sys
import json
import time
import base64
import io
import urllib.request
import urllib.parse

import numpy as np
from PIL import Image

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PRINTER_NAME = "H2D"

# Calibration position: front-left quadrant (80, 80) — closest visible area to camera (0,5,75).
# Z_CAPTURE brings nozzle close to bed surface so toolhead fills more of the 480p frame,
# giving a stronger frame-diff signal during the tool change swing.
CALIB_X = 80
CALIB_Y = 80
Z_CLEARANCE = 10    # mm — safe travel height; all XY moves happen at this Z
Z_CAPTURE = 2       # mm — nozzle visible in camera frame; measurements taken here
F_XY = 3000         # mm/min
F_Z = 600           # mm/min

# Measurement parameters (must match wait_for_tool_change_complete() design in corner_calibration.py)
POLL_INTERVAL_S = 0.3          # frame poll rate during settle detection
SNAPSHOT_RES = "480p"          # 480p — better signal at Z=2mm; matches TOOL_CHANGE_SNAPSHOT_RES
SNAPSHOT_QUALITY = 65          # quality tier for speed
STABLE_N = 3                   # consecutive stable frames to declare settled
NOISE_MULT = 1.5               # stability threshold multiplier (matches TOOL_CHANGE_NOISE_MULT)
TIMEOUT_S = 15.0               # hard timeout per trial
NOISE_FLOOR_FRAMES = 5         # baseline pairs for noise floor estimation
N_TRIALS = 3                   # full trials per direction


def _find_api_base() -> str:
    for port in range(49152, 49252):
        try:
            url = f"http://localhost:{port}/api/server_info"
            with urllib.request.urlopen(url, timeout=0.5) as r:
                info = json.loads(r.read())
                if "api_port" in info:
                    return f"http://localhost:{info['api_port']}/api"
        except Exception:
            continue
    return "http://localhost:49152/api"


API_BASE = _find_api_base()
print(f"MCP API: {API_BASE}")


def snapshot_at_res() -> np.ndarray:
    """Capture snapshot at SNAPSHOT_RES, return as float32 grayscale array."""
    params = urllib.parse.urlencode({
        "printer": PRINTER_NAME,
        "resolution": SNAPSHOT_RES,
        "quality": SNAPSHOT_QUALITY,
    })
    url = f"{API_BASE}/snapshot?{params}"
    with urllib.request.urlopen(url) as resp:
        data = json.loads(resp.read())
    b64 = data["data_uri"].split(",", 1)[1]
    img = Image.open(io.BytesIO(base64.b64decode(b64))).convert("L")
    return np.array(img, dtype=np.float32)


def send_gcode(gcode: str) -> dict:
    body = json.dumps({"printer": PRINTER_NAME, "gcode": gcode}).encode()
    req = urllib.request.Request(
        f"{API_BASE}/send_gcode",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def toggle_active_tool() -> None:
    """PATCH /api/toggle_active_tool — toggles T0→T1 or T1→T0."""
    params = urllib.parse.urlencode({"printer": PRINTER_NAME})
    req = urllib.request.Request(
        f"{API_BASE}/toggle_active_tool?{params}",
        method="PATCH",
    )
    with urllib.request.urlopen(req) as resp:
        resp.read()


def measure_noise_floor() -> float:
    """Capture NOISE_FLOOR_FRAMES+1 frames at rest, return mean avg-abs-diff (px)."""
    print(f"\n  Measuring noise floor (480p) ({NOISE_FLOOR_FRAMES} pairs)...")
    frames = []
    for i in range(NOISE_FLOOR_FRAMES + 1):
        frames.append(snapshot_at_res())
        if i < NOISE_FLOOR_FRAMES:
            time.sleep(POLL_INTERVAL_S)
    diffs = [float(np.mean(np.abs(frames[i+1] - frames[i]))) for i in range(NOISE_FLOOR_FRAMES)]
    mean_floor = float(np.mean(diffs))
    print(f"  Noise floor: {diffs} → mean={mean_floor:.3f}px")
    return mean_floor


def measure_settle(direction: str, noise_floor: float) -> tuple[float, list[float]]:
    """Toggle active tool, poll 480p at 0.3s, return (t_settle, per_frame_diffs).

    direction: 'T0→T1' or 'T1→T0' (informational only).
    """
    threshold = noise_floor * NOISE_MULT
    print(f"\n  Toggle {direction}  (threshold={threshold:.3f}px)...")
    toggle_active_tool()
    t_start = time.time()

    prev = snapshot_at_res()
    stable_count = 0
    diffs = []

    while True:
        elapsed = time.time() - t_start
        if elapsed > TIMEOUT_S:
            print(f"  ⚠️  TIMEOUT after {elapsed:.1f}s — carriage did not settle")
            return TIMEOUT_S, diffs

        time.sleep(POLL_INTERVAL_S)
        cur = snapshot_at_res()
        diff = float(np.mean(np.abs(cur - prev)))
        diffs.append(diff)
        prev = cur

        settled = diff <= threshold
        status = "✓" if settled else " "
        print(f"    t={elapsed + POLL_INTERVAL_S:.2f}s  diff={diff:.3f}px  {status}")

        if settled:
            stable_count += 1
            if stable_count >= STABLE_N:
                t_settled = (elapsed + POLL_INTERVAL_S) - (STABLE_N - 1) * POLL_INTERVAL_S
                print(f"  ✓ Settled at t≈{t_settled:.2f}s")
                return t_settled, diffs
        else:
            stable_count = 0


def main() -> None:
    print("=" * 70)
    print("  calibrate_tool_change_settle.py")
    print("  Measures 480p noise floor + T0→T1 / T1→T0 settle time")
    print(f"  {N_TRIALS} trials per direction | POLL={POLL_INTERVAL_S}s | RES={SNAPSHOT_RES}")
    print("=" * 70)
    print()
    print("PREREQUISITES: printer IDLE, bed CLEAR, explicit user authorization given.")
    print()
    input("Press ENTER to begin (Ctrl-C to abort)...")

    # Home and move to calibration position
    print(f"\nHoming and moving to ({CALIB_X},{CALIB_Y}) at Z{Z_CAPTURE}mm...")
    send_gcode("G28")
    # Wait for home to complete (use conservative fixed wait — this script runs standalone)
    print("  Waiting 65s for G28 to complete...")
    time.sleep(65)
    # Travel at Z_CLEARANCE, then descend to Z_CAPTURE (GCode safety pattern)
    send_gcode(f"G90\nG0 X{CALIB_X} Y{CALIB_Y} Z{Z_CLEARANCE} F{F_XY}\nM400")
    time.sleep(1)
    send_gcode(f"G0 Z{Z_CAPTURE} F{F_Z}\nM400")
    time.sleep(2)
    print(f"  At calibration position Z={Z_CAPTURE}mm.")

    noise_floors: list[float] = []
    t0_to_t1_settle: list[float] = []
    t1_to_t0_settle: list[float] = []

    for trial in range(1, N_TRIALS + 1):
        print(f"\n{'─'*60}")
        print(f"  Trial {trial}/{N_TRIALS}")
        print(f"{'─'*60}")

        # Measure noise floor at rest before this trial
        nf = measure_noise_floor()
        noise_floors.append(nf)

        # T0→T1
        t_fwd, diffs_fwd = measure_settle("T0→T1", nf)
        t0_to_t1_settle.append(t_fwd)
        print(f"  T0→T1 per-frame diffs: {[f'{d:.3f}' for d in diffs_fwd]}")

        # Brief pause between toggles
        time.sleep(1.0)

        # Re-measure noise floor (carriage now at T1 position)
        nf2 = measure_noise_floor()
        noise_floors.append(nf2)

        # T1→T0
        t_rev, diffs_rev = measure_settle("T1→T0", nf2)
        t1_to_t0_settle.append(t_rev)
        print(f"  T1→T0 per-frame diffs: {[f'{d:.3f}' for d in diffs_rev]}")

        # Restore carriage to calibration position (T0 is active after T1→T0).
        # Ascend to Z_CLEARANCE before any XY move (GCode safety rule), then descend.
        send_gcode(f"G0 Z{Z_CLEARANCE} F{F_Z}\nM400")
        send_gcode(f"G0 X{CALIB_X} Y{CALIB_Y} F{F_XY}\nM400")
        send_gcode(f"G0 Z{Z_CAPTURE} F{F_Z}\nM400")
        time.sleep(1)

    # --- Summary ---
    print(f"\n{'='*70}")
    print("  RESULTS SUMMARY")
    print(f"{'='*70}")

    mean_nf = float(np.mean(noise_floors))
    max_fwd = max(t0_to_t1_settle)
    max_rev = max(t1_to_t0_settle)
    overall_max = max(max_fwd, max_rev)
    suggested_timeout = overall_max + 5.0

    print(f"\nNoise floor measurements (480p): {[f'{v:.3f}' for v in noise_floors]}")
    print(f"  Mean noise floor: {mean_nf:.3f}px")
    print()
    print(f"T0→T1 settle times (s): {t0_to_t1_settle}")
    print(f"  max: {max_fwd:.2f}s")
    print()
    print(f"T1→T0 settle times (s): {t1_to_t0_settle}")
    print(f"  max: {max_rev:.2f}s")
    print()
    print(f"Overall max settle time: {overall_max:.2f}s")
    print()
    print(f"{'='*70}")
    print(f"  SUGGESTED CONSTANTS for corner_calibration.py:")
    print(f"{'='*70}")
    print(f"  TOOL_CHANGE_NOISE_FLOOR_PX = {mean_nf:.2f}  # [VERIFIED: empirical] "
          f"{N_TRIALS} trials, 2026-03-??")
    print(f"  TOOL_CHANGE_TIMEOUT_S      = {suggested_timeout:.1f} # "
          f"max({overall_max:.2f}s) + 5s safety margin")
    print()
    print("Update corner_calibration.py constants and mark [PROVISIONAL] → [VERIFIED: empirical].")
    print("Also update TOOL_CHANGE_NOISE_FLOOR_PX in behavioral_rules_camera_calibration.py.")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
