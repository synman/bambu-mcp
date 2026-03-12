#!/usr/bin/env python3
"""
H2D Corner Calibration — GCode nozzle→corner, diff-frame nozzle detection, DLT solve.

Issue #10, Track 3: Camera plate-boundary geometric calibration.

Prerequisites:
- H2D printer IDLE (gcode_state != RUNNING/PREPARE)
- Bed empty (user confirmed)
- bambu-mcp server running on localhost

Sequence per corner:
  1. Move nozzle to corner XY at Z_CLEARANCE (10mm)
  2. Capture reference frame (nozzle far from bed)
  3. Descend to Z_CAPTURE (2mm) — nozzle tip visible above bed
  4. Capture nozzle frame
  5. Diff → detect nozzle centroid in pixel space
  6. Ascend back to Z_CLEARANCE

After all corners: 3-point DLT solve → camera projection matrix.

Safety: all GCode follows the mandatory safe motion pattern from global rules
(GCode Calibration Motion Safety):
  - No XY travel at Z=0
  - Z transitions only when fully stationary (M400)
  - Every movement path clear (user confirmed bed empty)
"""

import numpy as np
from PIL import Image
import urllib.request
import urllib.parse
import base64
import io
import json
import time
import sys
import os

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_BASE = "http://localhost:49152/api"
PRINTER_NAME = "H2D"

Z_CLEARANCE = 10    # mm — safe travel height
Z_CAPTURE = 2       # mm — nozzle visible above bed surface
F_XY = 3000         # mm/min — conservative XY travel speed
F_Z = 600           # mm/min — conservative Z speed

# H2D bed: 350mm x 320mm (X x Y)
# Corner world coordinates (mm) — 5mm inset from edges
CORNERS = {
    "BL": (5, 315),      # Back-Left
    "BR": (345, 315),    # Back-Right
    "FR": (345, 5),      # Front-Right
    # FL (5, 5) is off-camera — skip
}

# Expected pixel regions (approximate, from issue #10 comment 9)
# Used to constrain nozzle search area — not required for DLT
EXPECTED_PIXELS = {
    "BL": (36, 374),
    "BR": (1552, 416),
    "FR": (1402, 1068),
}

SNAPSHOT_RESOLUTION = "native"
SNAPSHOT_QUALITY = 85
SETTLE_SECONDS = 2.0    # wait after M400 before snapshot (vibration damping)

OUTPUT_DIR = "/tmp/h2d_corner_calibration"

# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def send_gcode(gcode: str) -> dict:
    """Send GCode to H2D via MCP HTTP API."""
    data = json.dumps({"printer": PRINTER_NAME, "gcode": gcode}).encode()
    req = urllib.request.Request(
        f"{API_BASE}/send_gcode",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def get_snapshot() -> Image.Image:
    """Capture a snapshot from H2D camera, return as PIL Image."""
    params = urllib.parse.urlencode({
        "printer": PRINTER_NAME,
        "resolution": SNAPSHOT_RESOLUTION,
        "quality": SNAPSHOT_QUALITY,
    })
    url = f"{API_BASE}/snapshot?{params}"
    with urllib.request.urlopen(url) as resp:
        data = json.loads(resp.read())
    # data_uri is "data:image/jpeg;base64,..."
    data_uri = data["data_uri"]
    b64_data = data_uri.split(",", 1)[1]
    img_bytes = base64.b64decode(b64_data)
    return Image.open(io.BytesIO(img_bytes)).convert("RGB")

def get_printer_state() -> str:
    """Get current gcode_state."""
    params = urllib.parse.urlencode({"printer": PRINTER_NAME})
    url = f"{API_BASE}/printer?{params}"
    with urllib.request.urlopen(url) as resp:
        data = json.loads(resp.read())
        ps = data.get("_printer_state", {})
        return ps.get("gcode_state", "UNKNOWN")

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

def gcode_descend_to_capture() -> str:
    """Descend from clearance to capture height."""
    return "\n".join([
        f"G0 Z{Z_CAPTURE} F{F_Z}",     # descend to capture height
        "M400",                          # settle
    ])

def gcode_ascend_to_clearance() -> str:
    """Ascend from capture to clearance height."""
    return "\n".join([
        f"G0 Z{Z_CLEARANCE} F{F_Z}",   # ascend to clearance
        "M400",                          # full stop before next XY move
    ])

# ---------------------------------------------------------------------------
# Nozzle detection (diff-based)
# ---------------------------------------------------------------------------

def detect_nozzle_centroid(ref_img: Image.Image, nozzle_img: Image.Image,
                           expected_px: tuple = None,
                           search_radius: int = 100) -> tuple:
    """
    Detect nozzle centroid via local-crop reference/nozzle frame differencing.

    The bed moves between Z=10 and Z=2, causing a global perspective shift
    that dominates global diffs. To isolate the nozzle signal, we crop a tight
    region around the expected pixel position before diffing. This eliminates
    the global motion artifact and lets even weak nozzle signals emerge.

    Args:
        ref_img: frame at Z_CLEARANCE (nozzle far, small in image)
        nozzle_img: frame at Z_CAPTURE (nozzle close, large in image)
        expected_px: (x, y) approximate expected pixel location (REQUIRED)
        search_radius: pixel radius for local crop (default 100)

    Returns:
        (cx, cy, confidence) — centroid in pixel coords, confidence 0-1
    """
    ref = np.array(ref_img.convert('L'), dtype=np.float32)
    noz = np.array(nozzle_img.convert('L'), dtype=np.float32)
    h, w = ref.shape

    if expected_px is None:
        print("  WARNING: expected_px is required for local-crop detection")
        return (0, 0, 0.0)

    ex, ey = int(expected_px[0]), int(expected_px[1])

    # Crop tight local region around expected nozzle position
    y_lo = max(0, ey - search_radius)
    y_hi = min(h, ey + search_radius)
    x_lo = max(0, ex - search_radius)
    x_hi = min(w, ex + search_radius)

    ref_crop = ref[y_lo:y_hi, x_lo:x_hi]
    noz_crop = noz[y_lo:y_hi, x_lo:x_hi]
    local_diff = np.abs(noz_crop - ref_crop)

    dmax = local_diff.max()
    if dmax < 5.0:
        print(f"  WARNING: local diff max={dmax:.1f} — nozzle not visible")
        return (0, 0, 0.0)

    # Multi-threshold approach: start low (15), find centroid, then refine
    # at higher thresholds if enough pixels remain
    best_cx, best_cy = 0.0, 0.0
    best_count = 0

    for thresh in [15, 25, 40]:
        mask = local_diff > thresh
        count = mask.sum()
        if count < 5:
            break  # too few pixels at this threshold
        ys, xs = np.where(mask)
        best_cx = float(np.mean(xs)) + x_lo
        best_cy = float(np.mean(ys)) + y_lo
        best_count = count

    if best_count == 0:
        print(f"  WARNING: no nozzle pixels above threshold=15 in crop")
        return (0, 0, 0.0)

    # Confidence: based on local diff strength relative to max and pixel count
    # Higher diff max and more pixels = higher confidence
    signal_strength = dmax / 255.0
    fill_ratio = best_count / ((y_hi - y_lo) * (x_hi - x_lo))
    confidence = min(1.0, signal_strength * fill_ratio * 50)

    # Offset from expected: penalize if centroid is far from expected
    offset = np.sqrt((best_cx - ex)**2 + (best_cy - ey)**2)
    if offset > search_radius * 0.8:
        confidence *= 0.5  # large offset = less trustworthy

    print(f"  Local crop [{x_lo}:{x_hi}, {y_lo}:{y_hi}]: "
          f"diff_max={dmax:.1f}, count={best_count}, "
          f"centroid=({best_cx:.1f},{best_cy:.1f}), offset={offset:.1f}px, "
          f"confidence={confidence:.3f}")

    return (best_cx, best_cy, confidence)

# ---------------------------------------------------------------------------
# DLT solve (3 correspondences + constraints)
# ---------------------------------------------------------------------------

def solve_projection_3point(world_pts: np.ndarray, pixel_pts: np.ndarray) -> dict:
    """
    Solve for camera projection matrix from 3 world↔pixel correspondences.

    Uses Direct Linear Transform (DLT) with Z=0 plane constraint
    (all world points are on the bed surface).

    For Z=0, the full 3x4 projection matrix P reduces to a 3x3 homography H
    mapping (X, Y, 1) → (u, v, 1) in homogeneous coordinates.

    3 correspondences give exactly 6 equations for 8 unknowns (H has 9 entries,
    minus 1 for scale). This is underdetermined by 2, but for our purposes
    (checking Zc plausibility), the homography gives us the camera-to-bed
    transform which encodes the camera height.

    Args:
        world_pts: (3, 2) array of (X, Y) world coordinates (mm)
        pixel_pts: (3, 2) array of (u, v) pixel coordinates

    Returns:
        dict with: H (3x3 homography), Zc_estimate, reprojection_error,
                   success (bool)
    """
    n = len(world_pts)
    assert n >= 3, "Need at least 3 correspondences"

    # Build DLT matrix A for homography (Z=0 plane)
    # For each correspondence (X,Y) → (u,v):
    #   [ X  Y  1  0  0  0  -uX  -uY  -u ] [h1..h9]^T = 0
    #   [ 0  0  0  X  Y  1  -vX  -vY  -v ]
    A = []
    for i in range(n):
        X, Y = world_pts[i]
        u, v = pixel_pts[i]
        A.append([X, Y, 1, 0, 0, 0, -u*X, -u*Y, -u])
        A.append([0, 0, 0, X, Y, 1, -v*X, -v*Y, -v])
    A = np.array(A)

    # SVD solve: h is the last row of V^T (smallest singular value)
    _, S, Vt = np.linalg.svd(A)
    h = Vt[-1]
    H = h.reshape(3, 3)

    # Normalize so H[2,2] = 1
    H = H / H[2, 2]

    # Reprojection error
    errors = []
    for i in range(n):
        X, Y = world_pts[i]
        pt_h = H @ np.array([X, Y, 1.0])
        pt_h = pt_h / pt_h[2]
        err = np.sqrt((pt_h[0] - pixel_pts[i][0])**2 + (pt_h[1] - pixel_pts[i][1])**2)
        errors.append(err)
    reproj_err = float(np.mean(errors))

    # Estimate camera height Zc from homography
    # H = K [r1 r2 t] where K is intrinsic matrix
    # For a rough Zc estimate: ||H[:,0]|| and ||H[:,1]|| encode focal_length/Zc
    # A simpler heuristic: the determinant of H relates to (f/Zc)^2
    # det(H) ≈ (f/Zc)^2 * area_scale
    # Since we don't know f, estimate Zc from the known bed dimensions
    # mapped to pixel dimensions

    # Bed diagonal in mm
    bed_diag_mm = np.sqrt(350**2 + 320**2)  # ~474mm
    # Pixel diagonal (BL to FR)
    if n >= 2:
        px_diag = np.sqrt((pixel_pts[0][0] - pixel_pts[-1][0])**2 +
                         (pixel_pts[0][1] - pixel_pts[-1][1])**2)
    else:
        px_diag = 1000  # fallback

    # Very rough Zc estimate assuming ~4mm/pixel at typical distance
    # f/Zc ≈ px_diag / bed_diag_mm → Zc ≈ f * bed_diag_mm / px_diag
    # For a typical 1080p camera, f ≈ 1000-1500 pixels
    f_est = 1200  # rough estimate for H2D camera focal length in pixels
    Zc_est = f_est * bed_diag_mm / px_diag if px_diag > 0 else 0

    return {
        "H": H.tolist(),
        "Zc_estimate_mm": round(float(Zc_est), 1),
        "reprojection_error_px": round(reproj_err, 2),
        "success": reproj_err < 10.0 and 200 < Zc_est < 800,
        "singular_values": S.tolist(),
    }

# ---------------------------------------------------------------------------
# Main calibration sequence
# ---------------------------------------------------------------------------

def run_calibration():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=" * 60)
    print("H2D Corner Calibration — Issue #10 Track 3")
    print("=" * 60)

    # Pre-flight: verify printer is idle
    state = get_printer_state()
    print(f"\nPrinter state: {state}")
    if state.upper() in ("RUNNING", "PREPARE"):
        print("ERROR: Printer is actively printing. Cannot run calibration.")
        print("Wait for job to finish or pause first.")
        sys.exit(1)

    print(f"\nCorners to calibrate: {list(CORNERS.keys())}")
    print(f"Z_CLEARANCE={Z_CLEARANCE}mm, Z_CAPTURE={Z_CAPTURE}mm")
    print(f"XY speed: F{F_XY} ({F_XY/60:.0f} mm/s), Z speed: F{F_Z} ({F_Z/60:.0f} mm/s)")
    print()

    # Step 1: Home and move to clearance
    print(">>> Homing all axes...")
    result = send_gcode(gcode_home_and_clearance())
    print(f"    Home result: {result}")
    time.sleep(8)  # homing takes a few seconds

    # Capture a "home" reference frame
    print(">>> Capturing home reference frame...")
    home_frame = get_snapshot()
    home_frame.save(os.path.join(OUTPUT_DIR, "home_reference.png"))
    print(f"    Saved home reference ({home_frame.size[0]}x{home_frame.size[1]})")

    # Step 2: Visit each corner
    results = {}

    for corner_name, (cx, cy) in CORNERS.items():
        print(f"\n{'─' * 50}")
        print(f"Corner {corner_name}: world=({cx}, {cy})")
        print(f"{'─' * 50}")

        expected = EXPECTED_PIXELS.get(corner_name)

        # Move to corner at clearance
        print(f"  >>> Moving to X{cx} Y{cy} at Z{Z_CLEARANCE}...")
        send_gcode(gcode_move_to_corner(cx, cy))
        time.sleep(SETTLE_SECONDS)

        # Capture reference (nozzle far from bed)
        print(f"  >>> Capturing reference frame at Z{Z_CLEARANCE}...")
        ref_frame = get_snapshot()
        ref_path = os.path.join(OUTPUT_DIR, f"{corner_name}_ref_Z{Z_CLEARANCE}.png")
        ref_frame.save(ref_path)
        print(f"      Saved: {ref_path}")

        # Descend to capture height
        print(f"  >>> Descending to Z{Z_CAPTURE}...")
        send_gcode(gcode_descend_to_capture())
        time.sleep(SETTLE_SECONDS)

        # Capture nozzle frame
        print(f"  >>> Capturing nozzle frame at Z{Z_CAPTURE}...")
        nozzle_frame = get_snapshot()
        noz_path = os.path.join(OUTPUT_DIR, f"{corner_name}_nozzle_Z{Z_CAPTURE}.png")
        nozzle_frame.save(noz_path)
        print(f"      Saved: {noz_path}")

        # Detect nozzle centroid
        print(f"  >>> Detecting nozzle centroid (expected ~{expected})...")
        cx_px, cy_px, conf = detect_nozzle_centroid(ref_frame, nozzle_frame,
                                                     expected_px=expected)
        print(f"      Detected: ({cx_px:.1f}, {cy_px:.1f}) confidence={conf:.3f}")

        if conf < 0.01:
            print(f"      ⚠️  LOW CONFIDENCE — nozzle may not be visible")

        # Save diff visualization
        ref_np = np.array(ref_frame, dtype=np.float32)
        noz_np = np.array(nozzle_frame, dtype=np.float32)
        diff = np.abs(noz_np - ref_np)
        diff_gray = np.sqrt(np.sum(diff ** 2, axis=2))
        diff_norm = diff_gray / max(diff_gray.max(), 1.0)
        diff_img = Image.fromarray((diff_norm * 255).astype(np.uint8))
        diff_path = os.path.join(OUTPUT_DIR, f"{corner_name}_diff.png")
        diff_img.save(diff_path)
        print(f"      Diff saved: {diff_path}")

        results[corner_name] = {
            "world_xy": [cx, cy],
            "pixel_xy": [round(cx_px, 1), round(cy_px, 1)],
            "confidence": round(conf, 4),
            "expected_pixel": list(expected) if expected else None,
        }

        # Ascend before next corner
        print(f"  >>> Ascending to Z{Z_CLEARANCE}...")
        send_gcode(gcode_ascend_to_clearance())
        time.sleep(SETTLE_SECONDS)

    # Step 3: Return nozzle to safe position
    print(f"\n>>> Returning to center at Z{Z_CLEARANCE}...")
    send_gcode(f"G0 X175 Y160 F{F_XY}\nM400")
    time.sleep(1)

    # Step 4: DLT solve
    print(f"\n{'=' * 60}")
    print("DLT Solve — 3-Point Homography")
    print(f"{'=' * 60}")

    # Filter corners with sufficient confidence
    good_corners = {k: v for k, v in results.items() if v["confidence"] > 0.01}
    print(f"\nUsable corners: {list(good_corners.keys())} ({len(good_corners)}/3)")

    dlt_result = None
    if len(good_corners) >= 3:
        world_pts = np.array([v["world_xy"] for v in good_corners.values()])
        pixel_pts = np.array([v["pixel_xy"] for v in good_corners.values()])

        dlt_result = solve_projection_3point(world_pts, pixel_pts)
        print(f"\nHomography H:")
        H = np.array(dlt_result["H"])
        for row in H:
            print(f"  [{row[0]:10.4f} {row[1]:10.4f} {row[2]:10.4f}]")
        print(f"\nZc estimate: {dlt_result['Zc_estimate_mm']} mm")
        print(f"Reprojection error: {dlt_result['reprojection_error_px']} px")
        print(f"Success: {dlt_result['success']}")

        if dlt_result["success"]:
            print("\n✅ Calibration PLAUSIBLE — Zc in expected range")
        else:
            print("\n⚠️  Calibration INCONCLUSIVE — Zc outside expected range or high error")
    else:
        print("\n❌ INSUFFICIENT corners for DLT solve (need 3, got {})".format(len(good_corners)))

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

    # If successful, persist to calibration dir
    if dlt_result and dlt_result["success"]:
        cal_dir = os.path.expanduser("~/.bambu-mcp/calibration")
        os.makedirs(cal_dir, exist_ok=True)
        cal_path = os.path.join(cal_dir, "H2D.json")
        with open(cal_path, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Calibration persisted: {cal_path}")

    # Summary
    print(f"\n{'=' * 60}")
    print("SUMMARY")
    print(f"{'=' * 60}")
    for name, data in results.items():
        status = "✅" if data["confidence"] > 0.01 else "⚠️"
        print(f"  {status} {name}: world=({data['world_xy'][0]}, {data['world_xy'][1]}) "
              f"→ pixel=({data['pixel_xy'][0]}, {data['pixel_xy'][1]}) "
              f"conf={data['confidence']:.4f}")
    if dlt_result:
        print(f"\n  DLT: Zc={dlt_result['Zc_estimate_mm']}mm, "
              f"reproj_err={dlt_result['reprojection_error_px']}px, "
              f"success={dlt_result['success']}")
    print(f"\n  All outputs in: {OUTPUT_DIR}/")

    return output

if __name__ == "__main__":
    run_calibration()
