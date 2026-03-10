"""
thermal_snapshot_monitor.py — Issue #10 thermal data collection script.

Supports H2D (RTSPS) and A1 (TCP/TLS) printers.
Two operating modes:
  job mode (default):   waits for a print to start, monitors through cool-down
  standalone mode:      sets bed temp, runs for --hot-duration seconds, then cools down

Usage:
    cd ~/bambu-mcp
    # job mode (H2D, default):
    nohup .venv/bin/python thermal_snapshot_monitor.py > ~/thermal_captures/monitor_stdout.log 2>&1 &
    # standalone bench mode (A1, 100°C, 20 min hot phase):
    nohup .venv/bin/python thermal_snapshot_monitor.py --printer A1 --standalone --bed-temp 100 --hot-duration 1200 > ~/thermal_captures/a1_standalone.log 2>&1 &
"""

import io
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
import warnings
from datetime import datetime, timezone
from pathlib import Path

import argparse
import av
import matplotlib
matplotlib.use('Agg')
import matplotlib.cm as cm
import matplotlib.patches as patches
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image
from scipy.ndimage import binary_closing, binary_dilation
from scipy.ndimage import gaussian_filter, sobel as scipy_sobel
from scipy.ndimage import label as ndlabel
from scipy.spatial import ConvexHull as SpatialHull
from matplotlib.path import Path as MPath

warnings.filterwarnings('ignore')

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PRINTER_PROFILES = {
    "H2D": {
        "secret_pfx":    "bambu-h2d-printer",
        "camera":        "rtsps",
        "plate_left":    0.06,
        "plate_right":   0.78,
        "plate_top_det": 0.36,   # detection crop top
        "plate_top_dsp": 0.28,   # display crop top (adds headroom for path overlay)
    },
    "A1": {
        "secret_pfx":    "bambu-a1-printer",
        "camera":        "tcp_tls",
        # Bed travel geometry (empirically derived, 2026-03-10):
        #   The A1 is a bed-slinger: physical Y axis maps to frame X axis.
        #   Camera is gantry-mounted, looking down+forward at the bed.
        #   Y=0   (home/front) → plate on RIGHT side of frame: x=0.42–0.83W, y=0.44–0.98H
        #   Y=256 (max/back)   → plate on LEFT  side of frame: x=0.01–0.42W, y=0.52–0.98H
        #   Full travel envelope: x=0.01–0.84W, y=0.44–0.98H
        #   Verified by R-B warm-tone sampling across both Y=0 and Y=256 reference frames.
        "plate_left":    0.01,   # leftmost plate position (Y=max)
        "plate_right":   0.84,   # rightmost plate position (Y=0)
        "plate_top_det": 0.44,   # top of plate at Y=0 (highest visible point)
        "plate_top_dsp": 0.36,   # display crop top (adds headroom above plate)
        # Per-position constants for vision-based plate locator:
        "plate_y0_left":   0.42,   # plate left  edge when bed is at Y=0 (home)
        "plate_y0_right":  0.83,   # plate right edge when bed is at Y=0
        "plate_y0_top":    0.44,   # plate top   edge when bed is at Y=0
        "plate_ymax_left":  0.01,  # plate left  edge when bed is at Y=256
        "plate_ymax_right": 0.42,  # plate right edge when bed is at Y=256
        "plate_ymax_top":   0.52,  # plate top   edge when bed is at Y=256
        "plate_bottom":    0.98,   # plate bottom edge (consistent across Y positions)
    },
}
# Set by _parse_args() at startup; all functions read these module-level globals.
PROFILE: dict      = PRINTER_PROFILES["H2D"]
PRINTER_NAME: str  = "H2D"

FLOOR_TEMP_DEFAULT  = 25       # fallback ambient °C
CAPTURE_INTERVAL_S  = 60       # seconds between captures
POLL_INTERVAL_S     = 10       # seconds between job-start polls
COOLDOWN_READINGS   = 3        # consecutive readings required to confirm cool-down
BED_COOLDOWN_DELTA  = 10.0     # bed must drop to within this many °C of baseline
CHAM_COOLDOWN_DELTA = 5.0      # chamber must drop to within this many °C of baseline
GITHUB_ISSUE        = 10
GITHUB_REPO         = "synman/bambu-mcp"
OUTPUT_ROOT         = Path.home() / "thermal_captures"
MCP_PORTS           = [49152, 49153, 49154, 49155, 49156]

# ---------------------------------------------------------------------------
# Secrets + camera
# ---------------------------------------------------------------------------
def _secret(key: str) -> str:
    secrets = Path.home() / "bambu-printer-manager" / "secrets.py"
    return subprocess.check_output(
        [sys.executable, str(secrets), "get", key],
        stdin=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True).strip()


def _rtsps_frame(ip: str, access_code: str, timeout_s: int = 15) -> Image.Image:
    url = f"rtsps://bblp:{access_code}@{ip}:322/streaming/live/1"
    opts = {"rtsp_transport": "tcp", "stimeout": str(int(timeout_s * 1_000_000))}
    container = av.open(url, options=opts)
    frame = next(container.decode(video=0))
    container.close()
    return frame.to_image()


def _tcp_frame(ip: str, access_code: str) -> Image.Image:
    from camera.tcp_stream import capture_frame
    jpeg = capture_frame(ip, access_code)
    return Image.open(io.BytesIO(jpeg))


def _get_printer_state(auth: str) -> dict:
    for port in MCP_PORTS:
        try:
            req = urllib.request.Request(
                f"http://localhost:{port}/api/printer?printer={PRINTER_NAME}",
                headers={"Authorization": f"Basic {auth}"})
            data = json.loads(urllib.request.urlopen(req, timeout=3).read())
            return data
        except Exception:
            continue
    return {}


def fetch_snapshot(ip: str, access_code: str, auth: str) -> tuple:
    """Returns (pil_image, bed_temp, chamber_temp, gcode_state, layer, progress, subtask_name)."""
    state = _get_printer_state(auth)
    climate = (state.get("_printer_state") or {}).get("climate") or {}
    job_info = state.get("_active_job_info") or {}

    bed_temp     = climate.get("bed_temp")
    chamber_temp = climate.get("chamber_temp")
    gcode_state  = (state.get("_printer_state") or {}).get("gcode_state", "UNKNOWN")
    layer        = job_info.get("current_layer")
    progress     = job_info.get("print_percentage")
    subtask      = job_info.get("subtask_name", "unknown")

    if PROFILE["camera"] == "rtsps":
        img = _rtsps_frame(ip, access_code)
    else:
        img = _tcp_frame(ip, access_code)
    return (img,
            float(bed_temp) if bed_temp is not None else None,
            float(chamber_temp) if chamber_temp is not None else None,
            gcode_state, layer, progress, subtask)


# ---------------------------------------------------------------------------
# Thermal analysis (from h2d_heatmap.py POC)
# ---------------------------------------------------------------------------
def _largest_component(mask):
    labeled, n = ndlabel(mask)
    if n == 0:
        return mask
    counts = np.bincount(labeled.ravel())
    counts[0] = 0
    return labeled == int(counts.argmax())


def _hull_solidity(mask, subsample=6):
    ys, xs = np.where(mask)
    if len(ys) < 10:
        return 0.0, 0.0
    pts = np.column_stack([xs[::subsample], ys[::subsample]])
    if len(pts) < 4:
        pts = np.column_stack([xs, ys])
    try:
        hull = SpatialHull(pts)
        return float(mask.sum()) / max(float(hull.volume), 1.0), float(hull.volume)
    except Exception:
        return 0.0, 0.0


def _quad_corners(mask, subsample=4):
    ys, xs = np.where(mask)
    if len(ys) < 8:
        return None
    pts = np.column_stack([xs[::subsample], ys[::subsample]])
    if len(pts) < 8:
        pts = np.column_stack([xs, ys])
    try:
        hull   = SpatialHull(pts)
        hverts = pts[hull.vertices].astype(float)
    except Exception:
        return None
    closed = np.vstack([hverts, hverts[0]])
    codes  = [MPath.MOVETO] + [MPath.LINETO] * (len(hverts) - 1) + [MPath.CLOSEPOLY]
    return MPath(closed, codes)


def compute_heat(arr, bsz=25):
    H, W = arr.shape[:2]
    lum = (0.299*arr[:,:,0] + 0.587*arr[:,:,1] + 0.114*arr[:,:,2]) / 255.0
    var_map = np.zeros_like(lum)
    for r in range(0, H-bsz, bsz):
        for c in range(0, W-bsz, bsz):
            var_map[r:r+bsz, c:c+bsz] = lum[r:r+bsz, c:c+bsz].var()
    lum_n = (lum - lum.min()) / (lum.max() - lum.min() + 1e-8)
    var_n = (var_map - var_map.min()) / (var_map.max() - var_map.min() + 1e-8)
    return 0.5*lum_n + 0.5*var_n, lum_n


def _sobel_plate_mask(arr, seed_mask):
    """Expand seed_mask outward to the physical plate-rail geometric edge.

    Luminance detection stops at the hot-zone boundary (inside the plate).
    The physical plate-rail edge is a gradient peak in the raw camera image.
    Between the hot-zone edge and the plate-rail edge is a low-gradient valley
    (the cool plate rim).  This function walks outward looking for the
    valley-then-rise pattern:

        high (hot-zone edge) → low (cool rim) → high (plate-rail boundary)

    Returns (refined_mask, hit_edge).  refined_mask is the expansion just
    *before* the plate-rail ring.  Returns (seed_mask, False) on failure.
    """
    H, W = arr.shape[:2]
    gray = np.dot(arr[..., :3], [0.299, 0.587, 0.114]).astype(np.float32) / 255.0
    blurred = gaussian_filter(gray, sigma=1.5)
    sx = scipy_sobel(blurred, axis=1)
    sy = scipy_sobel(blurred, axis=0)
    grad_mag = np.hypot(sx, sy).astype(np.float32)

    DILATION_STEP = 3   # px per expansion step
    MAX_STEPS     = 30  # max ~90 px total expansion
    MAX_COV       = 0.88

    current     = seed_mask.copy()
    ring_grads  = []
    ring_masks  = [seed_mask.copy()]

    for _ in range(MAX_STEPS):
        expanded = binary_dilation(current, iterations=DILATION_STEP)
        ring = expanded & ~current
        if not ring.any():
            break
        if expanded.sum() / (H * W) > MAX_COV:
            break
        ring_grads.append(float(grad_mag[ring].mean()))
        ring_masks.append(expanded.copy())
        current = expanded

    if len(ring_grads) < 3:
        return seed_mask, False

    g_peak = max(ring_grads)
    if g_peak <= 0:
        return seed_mask, False
    g_norm = [g / g_peak for g in ring_grads]

    # Step 1: find first valley — gradient drops to < 25% of peak (past hot-zone edge)
    valley_i = -1
    for i, g in enumerate(g_norm):
        if g < 0.25:
            valley_i = i
            break

    if valley_i < 0:
        return seed_mask, False

    # Step 2: find the rise after the valley — gradient > 25% of peak (plate-rail edge)
    for i in range(valley_i + 1, len(g_norm)):
        if g_norm[i] > 0.25:
            # ring_masks[i+1] includes the edge ring (plate boundary pixels)
            refined = ring_masks[i + 1] if i + 1 < len(ring_masks) else ring_masks[i]
            if refined.sum() / (H * W) <= MAX_COV:
                return refined, True

    return seed_mask, False


def detect_plate_thermal(arr, floor_temp, bed_temp):
    H, W = arr.shape[:2]
    heat, lum_n = compute_heat(arr)
    lum_floor = float(np.percentile(lum_n, 2))

    NOZZLE_CLIP = 0.90
    lum_det = lum_n.copy()
    hot_frac = float((lum_det > NOZZLE_CLIP).mean())
    if 0 < hot_frac < 0.04:
        lum_det = np.clip(lum_det, None, NOZZLE_CLIP)

    bsz = 25
    best, best_pos = -1, (H//2, W//2)
    for r in range(0, H-bsz, bsz):
        for c in range(0, W-bsz, bsz):
            m = lum_det[r:r+bsz, c:c+bsz].mean()
            if m > best:
                best = m; best_pos = (r+bsz//2, c+bsz//2)
    hy_seed, hx_seed = best_pos
    lum_anchor = float(lum_det[hy_seed, hx_seed])

    if bed_temp and floor_temp:
        scale    = (bed_temp - floor_temp) / max(lum_anchor - lum_floor, 1e-4)
        temp_map = floor_temp + (lum_n - lum_floor) * scale
    else:
        bed_temp   = 100.0
        floor_temp = 0.0
        temp_map   = (lum_n - lum_floor) / max(lum_anchor - lum_floor, 1e-4) * 100.0

    MIN_PX        = int(0.05 * H * W)
    CLIFF_THRESH  = 0.05
    MIN_COV_CLIFF = 0.45   # raised: skip early inner-gradient cliff (was 0.25)
    MAX_COV_VALID = 0.88   # reject boundaries covering >88% of crop (too large)
    N_STEPS       = 80     # finer sweep resolution (was 60)
    thresholds    = np.linspace(lum_anchor, lum_floor + 0.02, N_STEPS)  # sweep lower (was +0.05)
    sweep = []

    for t in thresholds:
        mask = _largest_component(binary_closing(lum_det >= t, iterations=2))
        px = int(mask.sum())
        if px < MIN_PX:
            continue
        sol, _ = _hull_solidity(mask)
        sweep.append((t, px, sol, mask))

    best_thresh = None
    best_mask   = None
    cliff_delta = 0.0
    best_sol    = 0.0

    if len(sweep) >= 2:
        # Pass 1: detect coverage JUMP (plate-edge transition often shows as big coverage
        # increase, NOT solidity drop, as the background floods in at a specific threshold)
        JUMP_THRESH = 0.08  # 8% per-step coverage jump = major boundary transition
        jump_i = -1
        for i in range(1, len(sweep)):
            pre_cov = sweep[i-1][1] / (H * W)
            post_cov = sweep[i][1] / (H * W)
            cov_jump = post_cov - pre_cov
            if cov_jump > JUMP_THRESH and pre_cov >= MIN_COV_CLIFF:
                jump_i = i
                cliff_delta = cov_jump
                break  # first jump after MIN_COV_CLIFF = plate-background boundary
        if jump_i > 0:
            best_thresh, _, best_sol, best_mask = sweep[jump_i - 1]
            # Post-validate jump result
            if best_mask.sum() / (H * W) > MAX_COV_VALID:
                best_mask = None

        # Pass 2: fallback — largest solidity drop after MIN_COV_CLIFF
        if best_mask is None:
            max_drop, cliff_i = 0.0, -1
            for i in range(1, len(sweep)):
                drop = sweep[i-1][2] - sweep[i][2]
                pre_cov = sweep[i-1][1] / (H * W)
                if drop > max_drop and pre_cov >= MIN_COV_CLIFF:
                    max_drop, cliff_i = drop, i
            if max_drop >= CLIFF_THRESH and cliff_i > 0:
                best_thresh, _, best_sol, best_mask = sweep[cliff_i - 1]
                cliff_delta = max_drop
                if best_mask.sum() / (H * W) > MAX_COV_VALID:
                    best_mask = None

        # Pass 3: highest-coverage valid solidity within range
        if best_mask is None:
            valid = [(t, px, sol, m) for t, px, sol, m in sweep
                     if sol >= 0.82 and px / (H * W) <= MAX_COV_VALID]
            if valid:
                best_thresh, _, best_sol, best_mask = max(valid, key=lambda x: x[1])
            elif sweep:
                by_sol = sorted(sweep, key=lambda x: (-x[2], x[1]))
                best_thresh, _, best_sol, best_mask = by_sol[0]

    if best_mask is None:
        best_thresh = (lum_anchor + lum_floor) / 2.0
        best_mask   = _largest_component(
            binary_closing(lum_n >= best_thresh, iterations=2))

    plate_mask = best_mask

    # Sobel refinement: expand luminance-detected hot zone outward to the
    # physical plate-rail geometric edge (temperature-independent hard edge).
    sobel_mask, hit_edge = _sobel_plate_mask(arr, plate_mask)
    if hit_edge:
        plate_mask = sobel_mask

    coverage_pct = 100.0 * plate_mask.sum() / (H * W)

    ys, xs = np.where(plate_mask)
    if len(ys) < 10:
        ys, xs = np.mgrid[H//4:3*H//4, W//4:3*W//4]
        ys, xs = ys.ravel(), xs.ravel()
    cy = int(ys.mean());  cx = int(xs.mean())

    boundary_path = _quad_corners(plate_mask)
    if boundary_path is None:
        x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
        verts = [(x0,y0),(x1,y0),(x1,y1),(x0,y1),(x0,y0)]
        codes = [MPath.MOVETO]+[MPath.LINETO]*3+[MPath.CLOSEPOLY]
        boundary_path = MPath(verts, codes)

    yy, xx  = np.mgrid[0:H, 0:W]
    grid_xy = np.stack([xx.ravel(), yy.ravel()], axis=1)
    hull_mask = boundary_path.contains_points(grid_xy).reshape(H, W)
    ring = binary_dilation(hull_mask, iterations=6) & ~hull_mask

    outside_norm = 0.0
    if ring.any():
        outside_norm = float((lum_n[ring].mean() - lum_floor) /
                              max(lum_anchor - lum_floor, 1e-4))

    hot_flat  = int(np.argmax(lum_n * plate_mask.astype(float)))
    hy_g = hot_flat // W;  hx_g = hot_flat % W
    # Use absolute-minimum pixel only if it's in the top 5 rows or left/right 5 cols
    # (guaranteed outside-plate region).  If it's interior, fall back to the top-left
    # corner which is always outside the plate for both H2D and A1 camera geometries.
    cool_flat = int(np.argmin(lum_n))
    cy_c_raw = cool_flat // W; cx_c_raw = cool_flat % W
    in_border = (cy_c_raw < 5 or cy_c_raw >= H - 5 or cx_c_raw < 5 or cx_c_raw >= W - 5)
    if in_border or not hull_mask[cy_c_raw, cx_c_raw]:
        cy_c, cx_c = cy_c_raw, cx_c_raw          # original path — pixel is outside hull
    else:
        # Min pixel is inside hull (e.g. cooled print object on bed); use top-left corner
        cy_c, cx_c = 2, 2
    hot_in    = bool(hull_mask[hy_g, hx_g])
    cool_out  = not bool(hull_mask[cy_c, cx_c])
    validation_pass = hot_in and cool_out

    tick_temps = [floor_temp,
                  int(floor_temp + 0.33*(bed_temp - floor_temp)),
                  int(floor_temp + 0.67*(bed_temp - floor_temp)),
                  bed_temp]
    tick_norms = [(t - floor_temp) / max(bed_temp - floor_temp, 1) for t in tick_temps]

    return dict(
        arr=arr, heat=heat, lum_n=lum_n, temp_map=temp_map,
        boundary_path=boundary_path, hull_mask=hull_mask,
        cen=(cy, cx), hot=(hx_g, hy_g), cool=(cx_c, cy_c),
        val=f"▲ hot {'✓' if hot_in else '✗'} inside · ▼ cool {'✓' if cool_out else '✗'} outside",
        floor_temp=floor_temp, bed_temp=bed_temp,
        tick_temps=tick_temps, tick_norms=tick_norms,
        lum_anchor=lum_anchor, lum_floor=lum_floor,
        coverage_pct=coverage_pct, cliff_delta=cliff_delta,
        solidity=best_sol, outside_norm=outside_norm,
        validation_pass=validation_pass,
    )


def _add_colorbar(ax, fig, p):
    from mpl_toolkits.axes_grid1 import make_axes_locatable
    divider = make_axes_locatable(ax)
    cax = divider.append_axes('right', size='3%', pad=0.05)
    sm = plt.cm.ScalarMappable(cmap='inferno', norm=plt.Normalize(0, 1))
    sm.set_array([])
    cbar = fig.colorbar(sm, cax=cax)
    cbar.set_ticks(p['tick_norms'])
    cbar.set_ticklabels([f"{t}°" for t in p['tick_temps']])
    cbar.ax.yaxis.set_tick_params(color='lightgray', labelsize=7)
    plt.setp(cbar.ax.yaxis.get_ticklabels(), color='lightgray')
    cbar.set_label('°C', color='lightgray', fontsize=7)


def _offset_path(path, dy):
    verts = path.vertices.copy()
    verts[:, 1] += dy
    return MPath(verts, path.codes)


def render_and_save(raw_img: Image.Image, p: dict, out_path: Path,
                    title: str, bed_label: str, floor_label: str,
                    path_dy: int) -> None:
    disp_arr = np.array(raw_img)

    def make_comp(img_pil, heat):
        rgba = (cm.get_cmap('inferno')(heat)*255).astype(np.uint8)
        hp = Image.fromarray(rgba, 'RGBA')
        hp.putalpha(Image.fromarray((heat*140).astype(np.uint8)))
        return Image.alpha_composite(img_pil.convert('RGBA'), hp).convert('RGB')

    bpath = _offset_path(p['boundary_path'], path_dy)
    floor_temp = p['floor_temp']
    bed_temp   = p['bed_temp']

    # Panel 1: camera + overlay
    comp = make_comp(Image.fromarray(disp_arr),
                     np.zeros(disp_arr.shape[:2]))
    # Panel 2: thermal map
    dH, dW = disp_arr.shape[:2]
    lum_d = (0.299*disp_arr[:,:,0] + 0.587*disp_arr[:,:,1] +
             0.114*disp_arr[:,:,2]) / 255.0
    lum_d_n = (lum_d - lum_d.min()) / (lum_d.max() - lum_d.min() + 1e-8)
    temp_d  = floor_temp + (lum_d_n - p['lum_floor']) / \
              max(p['lum_anchor'] - p['lum_floor'], 1e-4) * (bed_temp - floor_temp)
    temp_norm = np.clip((temp_d - floor_temp) / max(bed_temp - floor_temp, 1), 0, 1)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(22, 9), facecolor='#111')
    ax1.set_facecolor('#111'); ax2.set_facecolor('#111')

    ax1.imshow(np.array(comp))
    ax1.add_patch(patches.PathPatch(bpath, lw=2, edgecolor='cyan', facecolor='none', linestyle='--'))
    cy, cx = p['cen'][0] + path_dy, p['cen'][1]
    ax1.plot(cx, cy, 'w*', ms=10, label='centroid')
    ax1.plot(p['hot'][0], p['hot'][1]+path_dy, 'ro', ms=7, label='hottest ▲')
    ax1.plot(p['cool'][0], p['cool'][1]+path_dy, 'bs', ms=7, label='coolest ▼')
    ax1.set_title("Camera + Boundary", fontsize=9, pad=4, color='white')
    ax1.set_xlabel(p['val'], fontsize=7, color='lightgray')
    ax1.set_xticks([]); ax1.set_yticks([])
    ax1.legend(fontsize=6, loc='upper right', framealpha=0.4, labelcolor='white', facecolor='#333')
    _add_colorbar(ax1, fig, p)

    ax2.imshow(temp_norm, cmap='inferno', vmin=0, vmax=1, interpolation='bilinear')
    ax2.add_patch(patches.PathPatch(bpath, lw=2, edgecolor='cyan', facecolor='none', linestyle='--'))
    ax2.plot(cx, cy, 'w*', ms=10, label='centroid')
    ax2.plot(p['hot'][0], p['hot'][1]+path_dy, 'ro', ms=7, label='hottest ▲')
    ax2.plot(p['cool'][0], p['cool'][1]+path_dy, 'bs', ms=7, label='coolest ▼')
    ax2.set_title(f"Thermal Map  —  floor {floor_label} → bed {bed_label}", fontsize=9, pad=4, color='white')
    ax2.set_xlabel(p['val'], fontsize=7, color='lightgray')
    ax2.set_xticks([]); ax2.set_yticks([])
    ax2.legend(fontsize=6, loc='upper right', framealpha=0.4, labelcolor='white', facecolor='#333')
    _add_colorbar(ax2, fig, p)

    fig.suptitle(title, color='lightgray', fontsize=10, y=1.01)
    plt.tight_layout(pad=0.5)
    plt.savefig(str(out_path), dpi=150, bbox_inches='tight', facecolor='#111')
    plt.close()


def crop_plate(img: Image.Image) -> Image.Image:
    W, H = img.size
    return img.crop((int(PROFILE["plate_left"]*W), int(PROFILE["plate_top_det"]*H),
                     int(PROFILE["plate_right"]*W), H))

def crop_plate_display(img: Image.Image) -> Image.Image:
    W, H = img.size
    return img.crop((int(PROFILE["plate_left"]*W), int(PROFILE["plate_top_dsp"]*H),
                     int(PROFILE["plate_right"]*W), H))

def path_dy_for(img: Image.Image) -> int:
    _, H = img.size
    return int(PROFILE["plate_top_det"] * H) - int(PROFILE["plate_top_dsp"] * H)


# ---------------------------------------------------------------------------
# Bed temperature control
# ---------------------------------------------------------------------------
def _set_bed_temp(temp: int, auth: str) -> None:
    data = json.dumps({"printer": PRINTER_NAME, "temp": temp}).encode()
    for port in MCP_PORTS:
        try:
            req = urllib.request.Request(
                f"http://localhost:{port}/api/set_bed_target_temp",
                data=data, method="PATCH",
                headers={"Authorization": f"Basic {auth}",
                         "Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)
            return
        except Exception:
            continue
    raise RuntimeError("_set_bed_temp: could not reach MCP API on any port")


# ---------------------------------------------------------------------------
# GitHub comment
# ---------------------------------------------------------------------------
def post_github_comment(body: str) -> None:
    try:
        subprocess.run(
            ["gh", "issue", "comment", str(GITHUB_ISSUE),
             "--repo", GITHUB_REPO, "--body", body],
            check=True, capture_output=True)
    except Exception as e:
        logging.warning(f"Failed to post GitHub comment: {e}")


# ---------------------------------------------------------------------------
# Monitor loop
# ---------------------------------------------------------------------------
def run_monitor(args) -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ])
    log = logging.getLogger("thermal_monitor")

    log.info(f"Starting thermal snapshot monitor — printer={PRINTER_NAME} "
             f"standalone={args.standalone}")
    ip          = _secret(f"{PROFILE['secret_pfx']}_ip")
    access_code = _secret(f"{PROFILE['secret_pfx']}_access_code")
    auth        = _secret("bpm_api_auth")

    # --- Baseline (ambient) temps ---
    # If the printer is already hot (job already running), use the floor default
    # so cool-down detection targets actual ambient, not the preheated state.
    log.info("Capturing baseline temperatures (ambient)...")
    state = _get_printer_state(auth)
    climate = (state.get("_printer_state") or {}).get("climate") or {}
    raw_bed     = float(climate.get("bed_temp") or 0)
    raw_chamber = float(climate.get("chamber_temp") or 0)
    if raw_bed > 50 or raw_chamber > 35:
        baseline_bed     = float(FLOOR_TEMP_DEFAULT)
        baseline_chamber = float(FLOOR_TEMP_DEFAULT)
        log.info(f"Printer already hot ({raw_bed}°C bed / {raw_chamber}°C chamber) — using ambient baseline: {baseline_bed}°C")
    else:
        baseline_bed     = raw_bed or float(FLOOR_TEMP_DEFAULT)
        baseline_chamber = raw_chamber or float(FLOOR_TEMP_DEFAULT)
        log.info(f"Baseline: bed={baseline_bed}°C  chamber={baseline_chamber}°C")

    # --- Wait for job start (or standalone preheat) ---
    if args.standalone:
        log.info(f"Standalone mode — setting bed to {args.bed_temp}°C...")
        _set_bed_temp(args.bed_temp, auth)
        log.info(f"  Waiting for bed to reach {args.bed_temp}°C (±5°C, max 15 min)...")
        for _ in range(90):
            state   = _get_printer_state(auth)
            climate = (state.get("_printer_state") or {}).get("climate") or {}
            bt      = float(climate.get("bed_temp") or 0)
            if bt >= args.bed_temp - 5:
                log.info(f"  Bed reached {bt:.0f}°C — starting captures.")
                break
            log.info(f"  Bed at {bt:.0f}°C (target {args.bed_temp}°C)...")
            time.sleep(10)
        subtask_name = f"STANDALONE_{PRINTER_NAME}_b{args.bed_temp}"
        hot_start    = time.monotonic()
    else:
        hot_start = None
        log.info(f"Waiting for print job to start (polling every {POLL_INTERVAL_S}s)...")
        subtask_name = "unknown"
        while True:
            state = _get_printer_state(auth)
            gcode_state = (state.get("_printer_state") or {}).get("gcode_state", "UNKNOWN")
            job = state.get("_active_job_info") or {}
            subtask_name = job.get("subtask_name", "unknown") or "unknown"
            if gcode_state in ("RUNNING", "PREPARE"):
                log.info(f"Job started: '{subtask_name}' (gcode_state={gcode_state})")
                break
            log.info(f"  gcode_state={gcode_state} — waiting...")
            time.sleep(POLL_INTERVAL_S)

    # --- Setup output directory ---
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in subtask_name)
    ts_start  = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_dir   = OUTPUT_ROOT / f"{safe_name}_{ts_start}"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_handler = logging.FileHandler(out_dir / "monitor.log")
    log_handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s"))
    log.addHandler(log_handler)
    log.info(f"Output directory: {out_dir}")
    log.info(f"Capture interval: {CAPTURE_INTERVAL_S}s")

    captures = []
    seq = 0
    cooldown_count = 0
    job_done = False

    # --- Capture loop ---
    while True:
        seq += 1
        ts = datetime.now(timezone.utc)
        ts_str = ts.strftime("%Y%m%d_%H%M%S")
        log.info(f"--- Capture #{seq} ---")

        try:
            raw_img, bed_temp, chamber_temp, gcode_state, layer, progress, subtask_name = \
                fetch_snapshot(ip, access_code, auth)
        except Exception as e:
            log.warning(f"  fetch_snapshot failed: {e} — skipping")
            time.sleep(CAPTURE_INTERVAL_S)
            continue

        bed_label   = f"{bed_temp:.0f}°" if bed_temp else "?"
        floor_label = f"{chamber_temp:.0f}°" if chamber_temp else f"{FLOOR_TEMP_DEFAULT}°"
        log.info(f"  bed={bed_label}  chamber={floor_label}  state={gcode_state}  "
                 f"layer={layer}  progress={progress}%")

        floor_temp = chamber_temp if chamber_temp else FLOOR_TEMP_DEFAULT

        try:
            crop_pil = crop_plate(raw_img)
            disp_pil = crop_plate_display(raw_img)
            dy       = path_dy_for(raw_img)
            p        = detect_plate_thermal(np.array(crop_pil), floor_temp, bed_temp)

            png_name  = f"{seq:04d}_{ts_str}_b{int(bed_temp or 0)}_c{int(chamber_temp or 0)}.png"
            json_name = f"{seq:04d}_{ts_str}.json"
            png_path  = out_dir / png_name

            title = (f"{PRINTER_NAME} Thermal  |  #{seq}  |  {ts_str}  |  "
                     f"Bed: {bed_label}C  ·  Chamber: {floor_label}C  |  "
                     f"State: {gcode_state}  Layer: {layer}  {progress}%")
            render_and_save(disp_pil, p, png_path, title, bed_label, floor_label, dy)

            meta = {
                "seq": seq,
                "timestamp": ts.isoformat(),
                "gcode_state": gcode_state,
                "layer": layer,
                "progress": progress,
                "bed_temp": bed_temp,
                "chamber_temp": chamber_temp,
                "baseline_bed": baseline_bed,
                "baseline_chamber": baseline_chamber,
                "lum_anchor": round(p["lum_anchor"], 4),
                "lum_floor": round(p["lum_floor"], 4),
                "coverage_pct": round(p["coverage_pct"], 1),
                "cliff_delta": round(p["cliff_delta"], 4),
                "solidity": round(p["solidity"], 4),
                "outside_norm": round(p["outside_norm"], 4),
                "validation_pass": p["validation_pass"],
                "png": png_name,
            }
            (out_dir / json_name).write_text(json.dumps(meta, indent=2))
            captures.append(meta)
            log.info(f"  saved {png_name}  coverage={p['coverage_pct']:.1f}%  "
                     f"norm={p['outside_norm']:.2f}  val={'✓' if p['validation_pass'] else '✗'}")

        except Exception as e:
            log.warning(f"  thermal analysis failed: {e}")
            captures.append({"seq": seq, "timestamp": ts.isoformat(),
                              "gcode_state": gcode_state, "bed_temp": bed_temp,
                              "chamber_temp": chamber_temp, "error": str(e)})

        # --- Standalone hot phase end ---
        if args.standalone and hot_start is not None and not job_done:
            if time.monotonic() - hot_start >= args.hot_duration:
                log.info(f"Standalone hot phase complete ({args.hot_duration}s). "
                         f"Setting bed to 0°C.")
                _set_bed_temp(0, auth)
                job_done = True

        # --- Check for job completion (job mode only) ---
        if not args.standalone and not job_done and gcode_state in ("FINISH", "FAILED", "IDLE"):
            log.info(f"Job ended (gcode_state={gcode_state}). Entering cool-down monitoring.")
            job_done = True

        # --- Cool-down check (only after job ends) ---
        if job_done and bed_temp is not None:
            bed_ok  = bed_temp <= baseline_bed + BED_COOLDOWN_DELTA
            cham_ok = (chamber_temp is None or
                       chamber_temp <= baseline_chamber + CHAM_COOLDOWN_DELTA)
            if bed_ok and cham_ok:
                cooldown_count += 1
                log.info(f"  Cool-down reading {cooldown_count}/{COOLDOWN_READINGS} "
                         f"(bed={bed_label}, chamber={floor_label})")
                if cooldown_count >= COOLDOWN_READINGS:
                    log.info("Cool-down complete. Stopping monitor.")
                    break
            else:
                if cooldown_count > 0:
                    log.info(f"  Temps not yet stable — resetting cooldown counter")
                cooldown_count = 0

        time.sleep(CAPTURE_INTERVAL_S)

    # --- Summary ---
    total = len(captures)
    errors = sum(1 for c in captures if "error" in c)
    first = next((c for c in captures if "error" not in c), None)
    last  = next((c for c in reversed(captures) if "error" not in c), None)
    duration_min = (datetime.now(timezone.utc) -
                    datetime.fromisoformat(captures[0]["timestamp"])).seconds // 60

    summary_lines = [
        f"## Thermal Snapshot Monitor — Run Complete",
        f"",
        f"**Printer:** {PRINTER_NAME}  |  **Mode:** {'Standalone' if args.standalone else 'Job tracking'}",
        f"**Job:** {subtask_name}",
        f"**Output:** `{out_dir}`",
        f"**Duration:** ~{duration_min} min  |  **Captures:** {total} total, {errors} errors",
        f"",
        f"**Baseline (ambient):** bed={baseline_bed}°C, chamber={baseline_chamber}°C",
        f"",
    ]
    if first:
        summary_lines += [
            f"**First capture (#{first['seq']}):** "
            f"bed={first.get('bed_temp')}°C, chamber={first.get('chamber_temp')}°C, "
            f"coverage={first.get('coverage_pct', '?')}%, norm={first.get('outside_norm', '?')}",
        ]
    if last and last != first:
        summary_lines += [
            f"**Last capture (#{last['seq']}):** "
            f"bed={last.get('bed_temp')}°C, chamber={last.get('chamber_temp')}°C, "
            f"coverage={last.get('coverage_pct', '?')}%, norm={last.get('outside_norm', '?')}",
        ]
    summary_lines += [
        f"",
        f"Captures saved to `{out_dir}` — PNG + JSON sidecar per frame.",
        f"Ready for model refinement analysis.",
    ]
    summary = "\n".join(summary_lines)
    log.info("\n" + summary)

    post_github_comment(summary)
    log.info(f"Summary posted to issue #{GITHUB_ISSUE}.")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Thermal snapshot monitor for Bambu printers")
    parser.add_argument("--printer",      default="H2D", choices=list(PRINTER_PROFILES),
                        help="Printer to monitor (default: H2D)")
    parser.add_argument("--standalone",   action="store_true",
                        help="Standalone bench mode: set bed temp, capture, cool down")
    parser.add_argument("--bed-temp",     type=int, default=100,
                        help="Bed target °C for standalone mode (default: 100)")
    parser.add_argument("--hot-duration", type=int, default=1200,
                        help="Seconds to hold hot phase before cooling (default: 1200)")
    return parser.parse_args()


if __name__ == "__main__":
    _args = _parse_args()
    PROFILE      = PRINTER_PROFILES[_args.printer]
    PRINTER_NAME = _args.printer
    run_monitor(_args)
