#!/usr/bin/env python3
"""
Nozzle Position Comparator — T0 vs T1 hotspot measurement.

Moves toolhead to bed center, activates each nozzle in turn, captures a thermal
frame per nozzle, diffs them, and reports the pixel offset between T0 and T1
thermal centroids.  If H2D.json is present, also projects both through H⁻¹ to
report world-space offset.

Output validates / corrects NL_FROM_HOTSPOT_OFFSET in plate_corner_repeatability.py.

Usage:
    cd ~/bambu-mcp
    .venv/bin/python camera/nozzle_compare.py

Prerequisites:
    bambu-mcp server running on localhost:49152
    H2D printer IDLE (not printing)

Output:
    /tmp/nozzle_compare_t0.png    — T0 thermal frame
    /tmp/nozzle_compare_t1.png    — T1 thermal frame
    /tmp/nozzle_compare_diff.png  — abs diff, annotated with centroids
"""

import urllib.request
import urllib.parse
import json
import base64
import io
import time
import math
import numpy as np
from PIL import Image, ImageDraw
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE     = "http://localhost:49152/api"  # updated by _find_api_base() below
PRINTER_NAME = "H2D"

# Bed center position for comparison capture
CMP_X = 175
CMP_Y = 160
CMP_Z = 2      # mm — nozzle visible above bed surface

NOZZLE_TEMP  = 180   # °C — lower than calibration; enough hotspot without over-stress
HEAT_WAIT    = 30    # seconds — settle after temp confirmed
SNAP_RES     = "720p"
SNAP_W, SNAP_H = 1280, 720

# Diff threshold for centroid computation (0-255, R-B channel)
DIFF_THRESHOLD = 15

# H2D.json calibration file (optional — used for world-space projection)
H_JSON_PATH = Path.home() / ".bambu-mcp/calibration/H2D.json"

# ---------------------------------------------------------------------------
# API port discovery (matches corner_calibration.py pattern)
# ---------------------------------------------------------------------------

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

def _request(endpoint: str, payload: dict, method: str = "POST") -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{API_BASE}/{endpoint}",
        data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def send_gcode(gcode: str) -> dict:
    """Send GCode via send_gcode endpoint (Tier 2 in camera script context).

    Only valid when no dedicated HTTP route covers the operation.
    Legitimate: G28 (home), G0/G1 (motion), G90/G91 (mode), M400 (wait for stop).
    NOT valid for temperature — use set_nozzle_temp() which calls PATCH /api/set_tool_target_temp.
    """
    return _request("send_gcode", {"printer": PRINTER_NAME, "gcode": gcode}, method="POST")


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
    """Read current T0 and T1 nozzle temperature targets via GET /api/temperatures."""
    d = get_temps()
    nozzles = d.get("nozzles", [])
    t0 = float(nozzles[0]["target"]) if len(nozzles) > 0 else -1.0
    t1 = float(nozzles[1]["target"]) if len(nozzles) > 1 else -1.0
    return t0, t1


# Idle nozzle heat timeout constants — firmware silently resets targets to 38°C
# after IDLE_NOZZLE_HEAT_TIMEOUT_S when gcode_state is IDLE/FINISH/FAILED.
# [VERIFIED: empirical 2026-03-14] calibrate_idle_nozzle_timeout.py --trials 3
# Trial 1=300.91s, Trial 2=299.95s, Trial 3=301.19s; mean=300.7s; σ<0.6s. Constant unchanged at 300s.
# NOTE: re-asserting target while nozzle is already hot does NOT reset the firmware timer.
# Reactive drift check (IDLE_HEAT_POLL_INTERVAL_S) is what prevents indefinite timeout.
IDLE_NOZZLE_HEAT_TIMEOUT_S  = 300     # [VERIFIED: empirical 2026-03-14] 3 trials, mean=300.7s
IDLE_HEAT_KEEPALIVE_S       = IDLE_NOZZLE_HEAT_TIMEOUT_S * 0.75  # = 225s
IDLE_HEAT_POLL_INTERVAL_S   = 10.0


def heat_and_wait(t0: int, t1: int, duration_s: float) -> None:
    """Wait duration_s while defending against firmware idle nozzle heat timeout.

    H2D firmware silently resets nozzle targets to 38°C after IDLE_NOZZLE_HEAT_TIMEOUT_S
    when gcode_state is IDLE, FINISH, or FAILED.

    Two concurrent independent checks in a 0.5s inner loop:
      Proactive timer:  elapsed >= IDLE_HEAT_KEEPALIVE_S since last set_nozzle_temp →
                        re-assert before firmware timeout fires.
      Reactive poll:    every IDLE_HEAT_POLL_INTERVAL_S → read targets via GET /api/temperatures →
                        re-assert only if target drifted (firmware already reset it; log WARN).
    Both share last_assert. set_nozzle_temp() never called speculatively on every tick.
    All temperature commands use PATCH /api/set_tool_target_temp (Tier 1) — never raw gcode/M104.
    """
    set_nozzle_temp(t0, extruder=0)
    set_nozzle_temp(t1, extruder=1)
    last_assert = time.time()
    next_poll   = time.time() + IDLE_HEAT_POLL_INTERVAL_S
    deadline    = time.time() + duration_s

    while time.time() < deadline:
        now = time.time()

        if now - last_assert >= IDLE_HEAT_KEEPALIVE_S:
            set_nozzle_temp(t0, extruder=0)
            set_nozzle_temp(t1, extruder=1)
            last_assert = now

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


def toggle_active_tool() -> None:
    """PATCH /api/toggle_active_tool — T0→T1 or T1→T0."""
    params = urllib.parse.urlencode({"printer": PRINTER_NAME})
    req = urllib.request.Request(
        f"{API_BASE}/toggle_active_tool?{params}",
        method="PATCH",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def wait_for_tool_change_settle(timeout: float = 16.2) -> None:
    """Poll 480p snapshots at ~2s effective rate; return when frame-diff drops to noise floor.

    [VERIFIED: empirical] calibrate_tool_change_settle.py, 3 trials, 2026-03-13.
    T0→T1 settles in 7.22–7.27s; T1→T0 settles in 11.04–11.19s (asymmetric).
    timeout=16.2s = max(T1→T0=11.19s) + 5s margin.
    NOISE_FLOOR=2.70px measured at 480p/(80,80)/Z=2mm.
    """
    import base64 as _b64, io as _io
    POLL = 0.3
    STABLE_N = 3
    NOISE_MULT = 1.5
    NOISE_FLOOR = 2.70  # [VERIFIED: empirical 2026-03-13] 480p at (80,80), Z=2mm

    def _snap360() -> np.ndarray:
        p = urllib.parse.urlencode({"printer": PRINTER_NAME, "resolution": "480p", "quality": 65})
        with urllib.request.urlopen(f"{API_BASE}/snapshot?{p}", timeout=10) as r:
            d = json.loads(r.read())
        if "url" in d:
            with urllib.request.urlopen(d["url"], timeout=10) as r:
                d = json.loads(r.read())
        b64 = d.get("data_uri", "").split(",", 1)[-1]
        from PIL import Image as _Im
        img = _Im.open(_io.BytesIO(_b64.b64decode(b64))).convert("L")
        return np.array(img, dtype=np.float32)

    threshold = NOISE_FLOOR * NOISE_MULT
    prev = _snap360()
    stable = 0
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(POLL)
        cur = _snap360()
        diff = float(np.mean(np.abs(cur - prev)))
        prev = cur
        if diff <= threshold:
            stable += 1
            if stable >= STABLE_N:
                return
        else:
            stable = 0
    print("    ⚠️  wait_for_tool_change_settle: timeout reached")


def get_snapshot() -> Image.Image:
    params = urllib.parse.urlencode({
        "printer": PRINTER_NAME, "resolution": SNAP_RES, "quality": 75,
    })
    with urllib.request.urlopen(f"{API_BASE}/snapshot?{params}", timeout=30) as resp:
        data = json.loads(resp.read())
    if "url" in data:
        with urllib.request.urlopen(data["url"], timeout=30) as r:
            data = json.loads(r.read())
    b64 = data.get("data_uri", "").split(",", 1)[-1]
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")


def get_temps() -> dict:
    params = urllib.parse.urlencode({"printer": PRINTER_NAME})
    with urllib.request.urlopen(f"{API_BASE}/temperatures?{params}", timeout=10) as resp:
        return json.loads(resp.read())


def wait_for_temp(target: float, tolerance: float = 5.0, timeout: float = 120.0) -> bool:
    """Poll active nozzle temp until within tolerance of target."""
    t_start = time.time()
    while time.time() - t_start < timeout:
        try:
            d = get_temps()
            nozzles = d.get("nozzles", [])
            if nozzles:
                current = nozzles[0].get("current", 0.0)
                print(f"\r    temp={current:.0f}°C / {target:.0f}°C  ", end="", flush=True)
                if abs(current - target) <= tolerance:
                    print()
                    return True
        except Exception:
            pass
        time.sleep(3)
    print()
    return False

# ---------------------------------------------------------------------------
# Hotspot detection (R - B channel centroid)
# ---------------------------------------------------------------------------

def rb_centroid(img: Image.Image, threshold: int = DIFF_THRESHOLD) -> tuple[int, int] | None:
    """Return (x, y) centroid of pixels where R-B > threshold, or None."""
    arr = np.array(img).astype(np.int16)
    rb = arr[:, :, 0].astype(np.int16) - arr[:, :, 2].astype(np.int16)
    mask = rb > threshold
    if not np.any(mask):
        return None
    ys, xs = np.where(mask)
    weights = rb[mask].astype(np.float32)
    cx = float(np.sum(xs * weights) / np.sum(weights))
    cy = float(np.sum(ys * weights) / np.sum(weights))
    return (int(round(cx)), int(round(cy)))

# ---------------------------------------------------------------------------
# World-space projection (optional, requires H2D.json)
# ---------------------------------------------------------------------------

def load_homography() -> np.ndarray | None:
    """Load H matrix from H2D.json. Returns 3x3 float64 or None."""
    if not H_JSON_PATH.exists():
        return None
    try:
        d = json.loads(H_JSON_PATH.read_text())
        H_list = d.get("H")
        if H_list:
            return np.array(H_list, dtype=np.float64).reshape(3, 3)
    except Exception:
        pass
    return None


def pixel_to_world(px: int, py: int, H: np.ndarray) -> tuple[float, float]:
    """Project pixel (px, py) through H⁻¹ to world coords (mm)."""
    H_inv = np.linalg.inv(H)
    v = H_inv @ np.array([px, py, 1.0])
    return (v[0] / v[2], v[1] / v[2])

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("\n" + "="*60)
    print("Nozzle Comparator — T0 vs T1 hotspot measurement")
    print("="*60 + "\n")

    # Safety pre-flight
    print("Pre-flight: printer must be IDLE with no active print.")
    print("This script moves the toolhead and heats nozzles to 180°C.\n")

    # --- Move to capture position ---
    print(f"[1/6] Moving to bed center (X={CMP_X} Y={CMP_Y} Z={CMP_Z})...")
    send_gcode(f"G90\nG0 X{CMP_X} Y{CMP_Y} F3000\nM400")
    time.sleep(8)
    send_gcode(f"G0 Z{CMP_Z} F600\nM400")
    time.sleep(3)

    # --- Heat T0 ---
    # T0 is active by default at startup; no toggle needed.
    # Capture idle baseline at capture position BEFORE heating.
    print(f"\n[2/6] Activating T0 (default), heating to {NOZZLE_TEMP}°C...")
    set_nozzle_temp(NOZZLE_TEMP, extruder=0)
    print("    Waiting for T0 temp...")
    if not wait_for_temp(NOZZLE_TEMP):
        print("    WARNING: T0 didn't reach target — continuing anyway")
    print(f"    Settling {HEAT_WAIT}s for thermal soak (keepalive active)...")
    heat_and_wait(NOZZLE_TEMP, 0, HEAT_WAIT)

    # --- Capture T0 frame ---
    print("\n[3/6] Capturing T0 frame...")
    t0_img = get_snapshot()
    t0_path = Path("/tmp/nozzle_compare_t0.png")
    t0_img.save(str(t0_path))
    t0_centroid = rb_centroid(t0_img)
    print(f"    T0 saved: {t0_path}")
    print(f"    T0 R-B centroid: {t0_centroid}")

    # --- Cool T0 before swap ---
    print("    Cooling T0 to idle...")
    set_nozzle_temp(0, extruder=0)
    time.sleep(10)

    # --- Swap to T1 using PATCH /api/toggle_active_tool ---
    # The carriage physically shifts when T1 becomes active.
    # Must re-position to CMP_X,CMP_Y and wait for settle before capturing.
    print(f"\n[4/6] Toggling to T1 via API (toggle_active_tool)...")
    toggle_active_tool()
    print("    Waiting for carriage settle...")
    wait_for_tool_change_settle()
    # Re-position nozzle at capture coordinates (carriage shifted)
    send_gcode(f"G90\nG0 X{CMP_X} Y{CMP_Y} Z{CMP_Z} F3000\nM400")
    time.sleep(3)
    set_nozzle_temp(NOZZLE_TEMP, extruder=1)
    print("    Waiting for T1 temp...")
    if not wait_for_temp(NOZZLE_TEMP):
        print("    WARNING: T1 didn't reach target — continuing anyway")
    print(f"    Settling {HEAT_WAIT}s for thermal soak (keepalive active)...")
    heat_and_wait(0, NOZZLE_TEMP, HEAT_WAIT)

    # --- Capture T1 frame ---
    print("\n[5/6] Capturing T1 frame...")
    t1_img = get_snapshot()
    t1_path = Path("/tmp/nozzle_compare_t1.png")
    t1_img.save(str(t1_path))
    t1_centroid = rb_centroid(t1_img)
    print(f"    T1 saved: {t1_path}")
    print(f"    T1 R-B centroid: {t1_centroid}")

    # --- Diff + annotate ---
    print("\n[6/6] Computing diff and annotating...")
    t0_arr = np.array(t0_img).astype(np.int16)
    t1_arr = np.array(t1_img).astype(np.int16)
    diff_arr = np.abs(t0_arr - t1_arr).astype(np.uint8)
    diff_img = Image.fromarray(diff_arr, mode="RGB")

    draw = ImageDraw.Draw(diff_img)
    if t0_centroid:
        x, y = t0_centroid
        draw.ellipse([x-8, y-8, x+8, y+8], outline=(255, 100, 100), width=2)
        draw.text((x+10, y-10), "T0", fill=(255, 100, 100))
    if t1_centroid:
        x, y = t1_centroid
        draw.ellipse([x-8, y-8, x+8, y+8], outline=(100, 100, 255), width=2)
        draw.text((x+10, y-10), "T1", fill=(100, 100, 255))
    if t0_centroid and t1_centroid:
        draw.line([t0_centroid, t1_centroid], fill=(255, 255, 0), width=1)

    diff_path = Path("/tmp/nozzle_compare_diff.png")
    diff_img.save(str(diff_path))
    print(f"    Diff saved: {diff_path}")

    # --- Report ---
    print("\n" + "="*60)
    print("RESULTS")
    print("="*60)
    if t0_centroid and t1_centroid:
        dx = t1_centroid[0] - t0_centroid[0]
        dy = t1_centroid[1] - t0_centroid[1]
        dist = math.sqrt(dx*dx + dy*dy)
        print(f"  T0 pixel:    {t0_centroid}")
        print(f"  T1 pixel:    {t1_centroid}")
        print(f"  Delta (T1-T0): dx={dx:+d}  dy={dy:+d}  dist={dist:.1f}px")
        print(f"\n  Pixel-space T1-T0 offset: ({dx}, {dy})")

        H = load_homography()
        if H is not None:
            w0 = pixel_to_world(*t0_centroid, H)
            w1 = pixel_to_world(*t1_centroid, H)
            wdx = w1[0] - w0[0]
            wdy = w1[1] - w0[1]
            print(f"\n  World-space (via H2D.json):")
            print(f"    T0 world:    ({w0[0]:.1f}, {w0[1]:.1f}) mm")
            print(f"    T1 world:    ({w1[0]:.1f}, {w1[1]:.1f}) mm")
            print(f"    Delta (T1-T0): dX={wdx:+.1f}mm  dY={wdy:+.1f}mm")
            print(f"\n  Expected T0-T1 world offset: 25mm in X (T0=right, T1=left)")
            print(f"  [VERIFIED: BambuStudio fdm_bbl_3dp_002_common.json extruder_printable_area, 2026-03-13]")
        else:
            print("\n  (H2D.json not found — world-space projection skipped)")
    else:
        if not t0_centroid:
            print("  WARNING: T0 R-B centroid not found (no hotspot above threshold)")
        if not t1_centroid:
            print("  WARNING: T1 R-B centroid not found (no hotspot above threshold)")
        print("  Cannot compute offset without both centroids.")

    print(f"\nImages: {t0_path}  {t1_path}  {diff_path}")
    print("Open with:  open /tmp/nozzle_compare_diff.png")
    print("="*60 + "\n")

    # Restore: cool nozzles, toggle back to T0, wait for settle
    set_nozzle_temp(0, extruder=0)
    set_nozzle_temp(0, extruder=1)
    toggle_active_tool()   # T1→T0
    wait_for_tool_change_settle()


if __name__ == "__main__":
    main()
