"""
camera/job_analyzer.py — Active job state report engine.

Produces a JobStateReport: a cohesive suite of digital assets representing
every meaningful dimension of the active print job.

Asset categories:
  P — Project Identity  : project_thumbnail_png, project_layout_png
  C — Live Camera       : raw_png, diff_png
  D — Anomaly Detection : air_zone_png, mask_png, annotated_png, heat_png, edge_png
  H — Print Health      : health_panel_png
  X — Composite         : job_state_composite_png

Spaghetti detection (Category D) is one module within the larger report.
All image processing uses numpy + pillow only — no scipy, skimage, or cv2.

Spaghetti score thresholds derived from Obico open-source project (threshold=0.08)
and first-principles analysis of Bambu xcam sensitivity tiers (warning=0.08,
critical=0.20).
"""

from __future__ import annotations

import base64
import io
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Design system — colours sourced exactly from camera/mjpeg_server.py HUD CSS
# ---------------------------------------------------------------------------
C_BG_PAGE  = (0,   0,   0)
C_BG_PANEL = (0,   0,   0,   192)   # rgba(0,0,0,.75)
C_BORDER   = (255, 255, 255,  20)   # rgba(255,255,255,.08)
C_SEP      = (255, 255, 255,  20)
C_VAL      = (221, 221, 221)        # #ddd
C_LBL      = (136, 136, 136)        # #888
C_DIM      = (85,  85,  85)         # #555
C_GOLD     = (224, 184,  78)        # #e0b84e
C_OK       = (96,  208, 128)        # #60d080
C_HOT      = (255, 144,  64)        # #ff9040
C_WARN     = (255, 204,  64)        # #ffcc40
C_CRIT     = (255,  80,  80)        # #ff5050
C_INFO     = (128, 160, 255)        # #80a0ff

# Verdict badge bg/fg — matches HUD .bRUNNING / .bPAUSE / .bFAILED
_VERDICT_BADGE = {
    "clean":    {"bg": (26,  92, 42), "fg": C_OK},
    "warning":  {"bg": (92,  74, 26), "fg": C_WARN},
    "critical": {"bg": (92,  26, 26), "fg": C_CRIT},
}

# Overlay alphas — match tools/files.py plate renderer
_ALPHA_FILL    = 40
_ALPHA_OUTLINE = 230
_ALPHA_HEATMAP = 160

# Zone definitions (fraction of frame dimensions)
_AIR_TOP    = 0.00
_AIR_BOTTOM = 0.40
_AIR_LEFT   = 0.10
_AIR_RIGHT  = 0.90
_PLATE_TOP    = 0.35
_PLATE_BOTTOM = 0.80
_PLATE_LEFT   = 0.20
_PLATE_RIGHT  = 0.80

# Spaghetti score thresholds (Obico-derived + xcam tier mapping)
_THRESH_BRIGHT  = 120   # brightness threshold for hot-pixel detection
_THRESH_WARN    = 0.08  # score → warning
_THRESH_CRIT    = 0.20  # score → critical

# ---------------------------------------------------------------------------
# Module-level per-printer reference store
# ---------------------------------------------------------------------------
_references: dict[str, tuple[bytes, float]] = {}   # name → (jpeg_bytes, timestamp)


def store_reference(name: str, jpeg: bytes) -> None:
    _references[name] = (jpeg, time.monotonic())
    log.debug("store_reference: stored %d bytes for %s", len(jpeg), name)


def get_reference(name: str) -> tuple[Optional[bytes], Optional[float]]:
    """Return (jpeg_bytes, age_seconds) or (None, None) if no reference stored."""
    entry = _references.get(name)
    if entry is None:
        return None, None
    jpeg, ts = entry
    return jpeg, time.monotonic() - ts


def clear_reference(name: str) -> None:
    _references.pop(name, None)


# ---------------------------------------------------------------------------
# Font helper — matches HUD CSS font stack
# ---------------------------------------------------------------------------
def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in (
        "/System/Library/Fonts/Courier New.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Colour ramp helpers
# ---------------------------------------------------------------------------
def _lerp_colour(a: tuple, b: tuple, t: float) -> tuple:
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def _brightness_ramp(v: float) -> tuple:
    """Map 0→1 brightness to C_DIM→C_HOT→C_CRIT."""
    if v < 0.5:
        return _lerp_colour(C_DIM, C_HOT, v * 2)
    return _lerp_colour(C_HOT, C_CRIT, (v - 0.5) * 2)


def _diff_ramp(v: float) -> tuple:
    """Map -1→0→1 (cooling→none→warming) to C_INFO→C_DIM→C_CRIT."""
    if v < 0:
        return _lerp_colour(C_DIM, C_INFO, min(1.0, abs(v)))
    return _lerp_colour(C_DIM, C_CRIT, min(1.0, v))


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class JobStateReport:
    # Spaghetti detection metrics
    verdict: str = "clean"
    score: float = 0.0
    hot_pct: float = 0.0
    strand_score: float = 0.0
    edge_density: float = 0.0
    diff_score: Optional[float] = None
    reference_age_s: Optional[float] = None

    # Quality tier used for all assets
    quality: str = "preview"

    # YOLO detections (additive layer)
    yolo_detections: list = field(default_factory=list)
    yolo_boost: float = 0.0
    yolo_available: bool = False

    # P — Project Identity
    project_thumbnail_png: Optional[bytes] = None
    project_layout_png: Optional[bytes] = None

    # C — Live Camera
    raw_png: bytes = field(default_factory=bytes)
    diff_png: Optional[bytes] = None

    # D — Anomaly Detection
    air_zone_png: bytes = field(default_factory=bytes)
    mask_png: bytes = field(default_factory=bytes)
    annotated_png: bytes = field(default_factory=bytes)
    heat_png: bytes = field(default_factory=bytes)
    edge_png: bytes = field(default_factory=bytes)

    # H — Print Health
    health_panel_png: bytes = field(default_factory=bytes)

    # X — Composite
    job_state_composite_png: bytes = field(default_factory=bytes)


# ---------------------------------------------------------------------------
# Quality tier → pixel dimensions
# ---------------------------------------------------------------------------
_QUALITY_DIMS = {
    "preview":  (320, 180),
    "standard": (640, 360),
    "full":     (0,   0),    # 0 = original
}


def _resolve_quality(verdict: str, quality: str) -> str:
    if quality != "auto":
        return quality
    return {"clean": "preview", "warning": "standard", "critical": "full"}.get(verdict, "standard")


def _tier_dims(quality: str, orig_w: int, orig_h: int) -> tuple[int, int]:
    tw, th = _QUALITY_DIMS.get(quality, (320, 180))
    if tw == 0:
        return orig_w, orig_h
    # Maintain aspect ratio within tier bounds
    scale = min(tw / orig_w, th / orig_h)
    return int(orig_w * scale), int(orig_h * scale)


def _encode_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def _encode_jpeg(img: Image.Image, quality: str) -> bytes:
    q_map = {"preview": 65, "standard": 75, "full": 85}
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=q_map.get(quality, 75), optimize=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Directional line-detection kernels (5×5, verified with PIL.ImageFilter.Kernel)
# ---------------------------------------------------------------------------
def _make_kernel(weights: list[float]) -> ImageFilter.Kernel:
    # size must be a tuple per verified API
    return ImageFilter.Kernel(size=(5, 5), kernel=weights, scale=1, offset=128)


# 0° horizontal lines
_K_HORIZ = _make_kernel([
     0,  0,  0,  0,  0,
    -1, -1, -1, -1, -1,
     2,  2,  2,  2,  2,
    -1, -1, -1, -1, -1,
     0,  0,  0,  0,  0,
])
# 90° vertical lines (drooping strands)
_K_VERT = _make_kernel([
     0, -1,  2, -1,  0,
     0, -1,  2, -1,  0,
     0, -1,  2, -1,  0,
     0, -1,  2, -1,  0,
     0, -1,  2, -1,  0,
])
# 45° diagonal
_K_DIAG45 = _make_kernel([
     2, -1,  0, -1,  0,
    -1,  2, -1,  0, -1,
     0, -1,  2, -1,  0,
    -1,  0, -1,  2, -1,
     0, -1,  0, -1,  2,
])
# 135° diagonal
_K_DIAG135 = _make_kernel([
     0, -1,  0, -1,  2,
    -1,  0, -1,  2, -1,
     0, -1,  2, -1,  0,
    -1,  2, -1,  0, -1,
     2, -1,  0, -1,  0,
])


def _apply_kernel(gray_img: Image.Image, kernel: ImageFilter.Kernel) -> np.ndarray:
    """Apply kernel to greyscale image; return normalised float array 0→1."""
    result = gray_img.filter(kernel)
    arr = np.array(result, dtype=np.float32) / 255.0
    # Kernel offset=128 means mid-grey is zero response; clip below 0.5 → 0
    arr = np.clip(arr - 0.5, 0, None) * 2.0
    return arr


# ---------------------------------------------------------------------------
# Core spaghetti analysis
# ---------------------------------------------------------------------------
def _analyse_spaghetti(
    frame_rgb: np.ndarray,
    ref_rgb: Optional[np.ndarray],
    W: int,
    H: int,
) -> tuple[float, float, float, float, Optional[float]]:
    """
    Returns (score, hot_pct, strand_score, edge_density, diff_score).

    score thresholds: <0.08 clean, 0.08–0.20 warning, ≥0.20 critical
    (Obico THRESH=0.08 + Bambu xcam sensitivity tier mapping)
    """
    # Zone pixel coordinates
    az_y0 = int(_AIR_TOP    * H)
    az_y1 = int(_AIR_BOTTOM * H)
    az_x0 = int(_AIR_LEFT   * W)
    az_x1 = int(_AIR_RIGHT  * W)

    air_zone = frame_rgb[az_y0:az_y1, az_x0:az_x1]   # shape (zone_h, zone_w, 3)

    # 1. Hot-pixel scan — brightness > threshold
    brightness = air_zone.mean(axis=2)                  # (zone_h, zone_w)
    hot_mask   = brightness > _THRESH_BRIGHT
    hot_pct    = float(hot_mask.sum()) / hot_mask.size if hot_mask.size > 0 else 0.0

    # 2. Local variance texture — 8×8 sliding window std-dev
    az_float = air_zone.mean(axis=2).astype(np.float32)
    # Pad to ensure full windows; use numpy stride tricks
    wh, ww = 8, 8
    try:
        from numpy.lib.stride_tricks import sliding_window_view
        windows = sliding_window_view(az_float, (wh, ww))  # (..., 8, 8)
        local_std = windows.std(axis=(-2, -1))
        local_var = float(local_std.mean())
    except Exception:
        local_var = float(np.std(az_float))

    # 3. Directional kernel response (strand score)
    gray_pil   = Image.fromarray(air_zone.mean(axis=2).astype(np.uint8))
    r_horiz    = _apply_kernel(gray_pil, _K_HORIZ)
    r_vert     = _apply_kernel(gray_pil, _K_VERT)
    r_d45      = _apply_kernel(gray_pil, _K_DIAG45)
    r_d135     = _apply_kernel(gray_pil, _K_DIAG135)
    # Max response across all four directions — strand-like structure in any orientation
    strand_map = np.maximum(np.maximum(r_horiz, r_vert), np.maximum(r_d45, r_d135))
    strand_score = float(strand_map.mean())
    edge_density = float((r_horiz + r_vert + r_d45 + r_d135).mean() / 4)

    # 4. Frame diff score
    diff_score: Optional[float] = None
    if ref_rgb is not None and ref_rgb.shape == frame_rgb.shape:
        diff = np.abs(frame_rgb.astype(np.float32) - ref_rgb.astype(np.float32))
        # Focus on air zone diff
        diff_air = diff[az_y0:az_y1, az_x0:az_x1]
        diff_score = float(diff_air.mean() / 255.0)

    # 5. Composite score
    ds = diff_score if diff_score is not None else 0.0
    score = hot_pct * 0.6 + (local_var / 5000.0) * 0.25 + ds * 0.15
    score = min(score, 1.0)

    return score, hot_pct, strand_score, edge_density, diff_score


# ---------------------------------------------------------------------------
# Asset builders — all return PNG bytes at (tw, th)
# ---------------------------------------------------------------------------

def _build_raw_png(frame_rgb: np.ndarray, tw: int, th: int) -> bytes:
    """F1 — unprocessed frame resized to tier dimensions."""
    img = Image.fromarray(frame_rgb).resize((tw, th), Image.LANCZOS)
    return _encode_png(img)


def _build_air_zone_png(frame_rgb: np.ndarray, W: int, H: int, tw: int, th: int) -> bytes:
    """F2 — air zone crop enlarged to fill tier resolution."""
    y0, y1 = int(_AIR_TOP * H), int(_AIR_BOTTOM * H)
    x0, x1 = int(_AIR_LEFT * W), int(_AIR_RIGHT * W)
    crop = Image.fromarray(frame_rgb[y0:y1, x0:x1])
    img  = crop.resize((tw, th), Image.LANCZOS)
    return _encode_png(img)


def _build_mask_png(frame_rgb: np.ndarray, W: int, H: int, tw: int, th: int) -> bytes:
    """
    F3 — binary threshold mask. Strictly monochrome.
    White pixels = those that crossed the brightness threshold in the air zone.
    Same array + same threshold used to compute hot_pct — algorithm transparency.
    """
    y0, y1 = int(_AIR_TOP * H), int(_AIR_BOTTOM * H)
    x0, x1 = int(_AIR_LEFT * W), int(_AIR_RIGHT * W)
    air = frame_rgb[y0:y1, x0:x1]
    brightness = air.mean(axis=2)
    mask = (brightness > _THRESH_BRIGHT).astype(np.uint8) * 255

    # Place mask on black full-frame canvas
    canvas = np.zeros((H, W), dtype=np.uint8)
    canvas[y0:y1, x0:x1] = mask
    img = Image.fromarray(canvas, mode="L").resize((tw, th), Image.NEAREST)
    return _encode_png(img.convert("RGB"))


def _build_annotated_png(
    frame_rgb: np.ndarray,
    W: int,
    H: int,
    tw: int,
    th: int,
    verdict: str,
    score: float,
    hot_pct: float,
    strand_score: float,
    edge_density: float,
    diff_score: Optional[float],
    reference_age_s: Optional[float],
    layer: int,
    total_layers: int,
) -> bytes:
    """A1 — full frame with zone overlays, hot-pixel heatmap, score inset."""
    img  = Image.fromarray(frame_rgb).resize((tw, th), Image.LANCZOS).convert("RGBA")
    draw = ImageDraw.Draw(img, "RGBA")

    sx = tw / W
    sy = th / H

    # Zone coordinates in tier space
    az_x0 = int(_AIR_LEFT   * W * sx);  az_y0 = int(_AIR_TOP    * H * sy)
    az_x1 = int(_AIR_RIGHT  * W * sx);  az_y1 = int(_AIR_BOTTOM * H * sy)
    pz_x0 = int(_PLATE_LEFT * W * sx);  pz_y0 = int(_PLATE_TOP  * H * sy)
    pz_x1 = int(_PLATE_RIGHT* W * sx);  pz_y1 = int(_PLATE_BOTTOM*H * sy)

    # Zone fills (subtle)
    draw.rectangle([az_x0, az_y0, az_x1, az_y1],
                   fill=(*C_WARN, _ALPHA_FILL), outline=(*C_WARN, _ALPHA_OUTLINE), width=1)
    draw.rectangle([pz_x0, pz_y0, pz_x1, pz_y1],
                   fill=(*C_OK, _ALPHA_FILL), outline=(*C_OK, _ALPHA_OUTLINE), width=1)

    # Hot-pixel heatmap overlay on air zone
    air_full = frame_rgb[int(_AIR_TOP*H):int(_AIR_BOTTOM*H), int(_AIR_LEFT*W):int(_AIR_RIGHT*W)]
    az_tw = az_x1 - az_x0
    az_th = az_y1 - az_y0
    if az_tw > 0 and az_th > 0:
        air_sm = np.array(Image.fromarray(air_full).resize((az_tw, az_th), Image.LANCZOS))
        brightness = air_sm.mean(axis=2)  # (az_th, az_tw)
        norm = np.clip(brightness / 255.0, 0, 1)

        heat = np.zeros((az_th, az_tw, 4), dtype=np.uint8)
        for y in range(az_th):
            for x in range(az_tw):
                b = float(norm[y, x])
                if b > 0.47:   # only overlay bright pixels
                    c = _brightness_ramp(b)
                    heat[y, x] = (*c, _ALPHA_HEATMAP)

        heat_img = Image.fromarray(heat, "RGBA")
        img.paste(heat_img, (az_x0, az_y0), heat_img)

    # Score inset panel — top-right corner
    _draw_score_inset(draw, img, tw, th, verdict, score, hot_pct, strand_score,
                      edge_density, diff_score, reference_age_s, layer, total_layers)

    return _encode_png(img.convert("RGB"))


def _draw_score_inset(
    draw: ImageDraw.ImageDraw,
    img: Image.Image,
    tw: int,
    th: int,
    verdict: str,
    score: float,
    hot_pct: float,
    strand_score: float,
    edge_density: float,
    diff_score: Optional[float],
    reference_age_s: Optional[float],
    layer: int,
    total_layers: int,
) -> None:
    """Draw score inset panel (top-right) directly onto img."""
    pad = 8
    panel_w = 130
    panel_h = 90
    x0 = tw - panel_w - 8
    y0 = 8

    badge = _VERDICT_BADGE.get(verdict, _VERDICT_BADGE["clean"])
    fn10 = _font(10)
    fn11 = _font(11)
    fn12 = _font(12)
    fn14 = _font(14)

    # Panel background
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay, "RGBA")
    od.rounded_rectangle([x0, y0, x0+panel_w, y0+panel_h],
                          radius=6, fill=C_BG_PANEL, outline=(*C_BORDER[:3], C_BORDER[3]), width=1)
    img.paste(overlay, (0, 0), overlay)
    draw = ImageDraw.Draw(img, "RGBA")

    # Verdict badge row
    bx = x0 + pad; by = y0 + 6
    bw = panel_w - pad * 2; bh = 18
    draw.rounded_rectangle([bx, by, bx+bw, by+bh], radius=3,
                            fill=badge["bg"], outline=badge["bg"], width=1)
    label = verdict.upper()
    draw.text((bx + bw//2, by + bh//2), label, fill=badge["fg"],
              font=fn12, anchor="mm")

    # Score
    sy = by + bh + 4
    draw.text((x0+pad, sy), "SCORE", fill=C_LBL, font=fn10)
    draw.text((x0+panel_w-pad, sy), f"{score:.3f}", fill=C_VAL, font=fn12, anchor="ra")

    # Separator
    sep_y = sy + 16
    draw.line([(x0+4, sep_y), (x0+panel_w-4, sep_y)], fill=(*C_SEP[:3], 40), width=1)

    # Metric bars
    metrics = [
        ("HOT",    hot_pct,     0.15, C_HOT),
        ("STRAND", strand_score, 0.20, C_INFO),
        ("EDGE",   edge_density, 0.15, C_OK),
    ]
    my = sep_y + 4
    bar_w = panel_w - pad*2 - 28
    for lbl, val, mx, col in metrics:
        draw.text((x0+pad, my+1), lbl, fill=C_LBL, font=fn10)
        track_x = x0 + pad + 28
        draw.rectangle([track_x, my+3, track_x+bar_w, my+7], fill=C_DIM)
        fill_w = int(bar_w * min(1.0, val / max(mx, 0.001)))
        if fill_w > 0:
            draw.rectangle([track_x, my+3, track_x+fill_w, my+7], fill=col)
        my += 12

    # Footer
    sep_y2 = my + 2
    draw.line([(x0+4, sep_y2), (x0+panel_w-4, sep_y2)], fill=(*C_SEP[:3], 40), width=1)
    if layer and total_layers:
        footer = f"L{layer}/{total_layers}"
    else:
        footer = ""
    if reference_age_s is not None:
        mins = int(reference_age_s // 60); secs = int(reference_age_s % 60)
        footer += f"  ref {mins}m{secs:02d}s"
    draw.text((x0+pad, sep_y2+4), footer.strip(), fill=C_LBL, font=fn10)


def _build_heat_png(
    frame_rgb: np.ndarray,
    W: int,
    H: int,
    tw: int,
    th: int,
    strand_score: float,
    verdict: str,
    score: float,
) -> bytes:
    """
    A2 — 2D risk heatmap. Air zone only, enlarged to tier.
    Hue = raw brightness; Saturation = strand-likelihood from directional kernels.
    """
    y0, y1 = int(_AIR_TOP * H), int(_AIR_BOTTOM * H)
    x0, x1 = int(_AIR_LEFT * W), int(_AIR_RIGHT * W)
    air = frame_rgb[y0:y1, x0:x1]

    air_img  = Image.fromarray(air)
    gray_pil = air_img.convert("L")

    r_h = _apply_kernel(gray_pil, _K_HORIZ)
    r_v = _apply_kernel(gray_pil, _K_VERT)
    r_d = _apply_kernel(gray_pil, _K_DIAG45)
    r_e = _apply_kernel(gray_pil, _K_DIAG135)
    strand_map = np.maximum(np.maximum(r_h, r_v), np.maximum(r_d, r_e))

    brightness = np.array(gray_pil, dtype=np.float32) / 255.0

    az_h, az_w = air.shape[:2]
    result = np.zeros((az_h, az_w, 3), dtype=np.uint8)

    for y in range(az_h):
        for x in range(az_w):
            b = float(brightness[y, x])
            s = float(strand_map[y, x])
            base = _brightness_ramp(b)
            # Desaturate toward grey based on (1 - strand_score)
            desaturate = 1.0 - min(1.0, s * 5.0)
            grey = int(b * 200)
            r = int(base[0] * (1 - desaturate) + grey * desaturate)
            g = int(base[1] * (1 - desaturate) + grey * desaturate)
            bl = int(base[2] * (1 - desaturate) + grey * desaturate)
            result[y, x] = (r, g, bl)

    img = Image.fromarray(result).resize((tw, th), Image.LANCZOS)
    draw = ImageDraw.Draw(img)

    # Air zone border
    draw.rectangle([0, 0, tw-1, th-1], outline=C_WARN, width=2)

    # Score annotation
    fn10 = _font(10)
    badge = _VERDICT_BADGE.get(verdict, _VERDICT_BADGE["clean"])
    draw.text((4, 4), verdict.upper(), fill=badge["fg"], font=fn10)
    draw.text((4, 16), f"score {score:.3f}", fill=C_LBL, font=fn10)

    return _encode_png(img)


def _build_edge_png(
    frame_rgb: np.ndarray,
    W: int,
    H: int,
    tw: int,
    th: int,
) -> bytes:
    """
    A3 — 4-direction edge map. Air zone only, enlarged to tier.
    Red=horizontal, Green=vertical(droop), Blue=45°diag.
    Brightness of each channel = kernel response magnitude.
    """
    y0, y1 = int(_AIR_TOP * H), int(_AIR_BOTTOM * H)
    x0, x1 = int(_AIR_LEFT * W), int(_AIR_RIGHT * W)
    air  = frame_rgb[y0:y1, x0:x1]
    gray = Image.fromarray(air).convert("L")

    r_h = (_apply_kernel(gray, _K_HORIZ)   * 255).astype(np.uint8)
    r_v = (_apply_kernel(gray, _K_VERT)    * 255).astype(np.uint8)
    r_d = (_apply_kernel(gray, _K_DIAG45)  * 255).astype(np.uint8)
    r_e = (_apply_kernel(gray, _K_DIAG135) * 255).astype(np.uint8)

    # Combine: R=horiz, G=vert+droop, B=diag (135 blended into luminance)
    combined = np.stack([r_h, r_v, np.maximum(r_d, r_e)], axis=2)
    img = Image.fromarray(combined, "RGB").resize((tw, th), Image.LANCZOS)

    draw = ImageDraw.Draw(img)
    fn10 = _font(10)
    draw.text((4, 4),  "H=horiz", fill=C_CRIT,  font=fn10)
    draw.text((4, 16), "G=vert",  fill=C_OK,    font=fn10)
    draw.text((4, 28), "B=diag",  fill=C_INFO,  font=fn10)

    return _encode_png(img)


def _build_diff_png(
    frame_rgb: np.ndarray,
    ref_rgb: np.ndarray,
    W: int,
    H: int,
    tw: int,
    th: int,
    reference_age_s: Optional[float],
) -> bytes:
    """
    C2/A4 — Temporal diff map.
    Brightness = change magnitude; Hue = direction (warming→C_CRIT red, cooling→C_INFO blue).
    """
    delta = frame_rgb.astype(np.int16) - ref_rgb.astype(np.int16)  # signed
    mag   = np.abs(delta).mean(axis=2).astype(np.float32) / 255.0  # 0→1
    sign  = delta.mean(axis=2)                                       # signed mean

    h, w = frame_rgb.shape[:2]
    result = np.zeros((h, w, 3), dtype=np.uint8)

    for y in range(h):
        for x in range(w):
            m = float(mag[y, x])
            s = float(sign[y, x]) / 255.0
            # signed: positive=warming, negative=cooling
            col = _diff_ramp(s * m * 3.0)
            brightness = min(255, int(m * 2.0 * 255))
            result[y, x] = tuple(min(255, int(c * m * 3.0 + (1 - m * 3.0) * C_DIM[i]))
                                  for i, c in enumerate(col))

    img = Image.fromarray(result).resize((tw, th), Image.LANCZOS)
    draw = ImageDraw.Draw(img)
    fn10 = _font(10)
    draw.text((4, 4), "DELTA", fill=C_VAL, font=fn10)
    if reference_age_s is not None:
        mins = int(reference_age_s // 60); secs = int(reference_age_s % 60)
        draw.text((4, 16), f"ref {mins}m{secs:02d}s", fill=C_LBL, font=fn10)

    return _encode_png(img)


def _build_health_panel_png(
    tw: int,
    verdict: str,
    score: float,
    printer_context: dict,
) -> bytes:
    """
    H1 — Print health strip. Full-width, fixed height 48px.
    Sections: verdict badge | HMS | detectors | temps | fans | AMS humidity
    """
    ph = 48
    img = Image.new("RGBA", (tw, ph), C_BG_PANEL)
    draw = ImageDraw.Draw(img, "RGBA")
    draw.rectangle([0, 0, tw-1, ph-1], outline=(*C_BORDER[:3], C_BORDER[3]), width=1)

    fn10 = _font(10)
    fn11 = _font(11)
    badge = _VERDICT_BADGE.get(verdict, _VERDICT_BADGE["clean"])
    x = 6

    # Verdict badge
    draw.rounded_rectangle([x, 6, x+54, 26], radius=3, fill=badge["bg"])
    draw.text((x+27, 16), verdict.upper(), fill=badge["fg"], font=fn10, anchor="mm")
    draw.text((x+27, 34), f"{score:.3f}", fill=badge["fg"], font=fn10, anchor="mm")
    x += 62

    # Separator
    draw.line([(x, 8), (x, ph-8)], fill=(*C_SEP[:3], 60), width=1)
    x += 8

    # HMS
    hms = printer_context.get("hms_errors", [])
    active = [e for e in hms if e.get("is_critical")]
    if active:
        draw.text((x, 8),  "HMS", fill=C_LBL, font=fn10)
        draw.text((x, 20), str(len(active)), fill=C_CRIT, font=fn11)
        draw.text((x, 32), "ERR", fill=C_CRIT, font=fn10)
    else:
        draw.text((x, 8),  "HMS", fill=C_LBL, font=fn10)
        draw.text((x, 20), "●", fill=C_OK, font=fn11)
        draw.text((x, 32), "OK",  fill=C_OK, font=fn10)
    x += 32

    draw.line([(x, 8), (x, ph-8)], fill=(*C_SEP[:3], 60), width=1)
    x += 8

    # Detectors (spaghetti + nozzle)
    det = printer_context.get("detectors", {})
    sp_det = det.get("spaghetti_detector", {})
    nb_det = det.get("nozzleclumping_detector", {})
    sp_on = sp_det.get("enabled", False)
    nb_on = nb_det.get("enabled", False)
    sp_col = C_OK if sp_on else C_DIM
    nb_col = C_OK if nb_on else C_DIM
    draw.text((x, 8),  "SPAG", fill=C_LBL, font=fn10)
    draw.text((x, 20), "ON" if sp_on else "OFF", fill=sp_col, font=fn10)
    draw.text((x, 32), sp_det.get("sensitivity", "")[:3].upper(), fill=C_LBL, font=fn10)
    x += 30
    draw.text((x, 8),  "BLOB", fill=C_LBL, font=fn10)
    draw.text((x, 20), "ON" if nb_on else "OFF", fill=nb_col, font=fn10)
    x += 28

    draw.line([(x, 8), (x, ph-8)], fill=(*C_SEP[:3], 60), width=1)
    x += 8

    # Temperatures
    nozzle = printer_context.get("nozzle_temp", 0)
    nozzle_t = printer_context.get("nozzle_target", 0)
    bed    = printer_context.get("bed_temp", 0)
    bed_t  = printer_context.get("bed_target", 0)
    chamber= printer_context.get("chamber_temp", 0)
    draw.text((x, 6),  "NOZZLE", fill=C_LBL, font=fn10)
    draw.text((x, 18), f"{nozzle:.0f}°", fill=C_HOT, font=fn11)
    draw.text((x, 30), f"/{nozzle_t:.0f}°", fill=C_LBL, font=fn10)
    x += 38
    draw.text((x, 6),  "BED", fill=C_LBL, font=fn10)
    draw.text((x, 18), f"{bed:.0f}°", fill=C_INFO, font=fn11)
    draw.text((x, 30), f"/{bed_t:.0f}°", fill=C_LBL, font=fn10)
    x += 32
    draw.text((x, 6),  "CHM", fill=C_LBL, font=fn10)
    draw.text((x, 18), f"{chamber:.0f}°", fill=C_VAL, font=fn11)
    x += 30

    draw.line([(x, 8), (x, ph-8)], fill=(*C_SEP[:3], 60), width=1)
    x += 8

    # Fans
    pf = printer_context.get("part_fan_pct", 0)
    af = printer_context.get("aux_fan_pct", 0)
    ef = printer_context.get("exhaust_fan_pct", 0)
    draw.text((x, 6),  "FAN P", fill=C_LBL, font=fn10)
    draw.text((x, 18), f"{pf:.0f}%", fill=C_VAL, font=fn10)
    draw.text((x, 30), f"A:{af:.0f}", fill=C_LBL, font=fn10)
    x += 34

    draw.line([(x, 8), (x, ph-8)], fill=(*C_SEP[:3], 60), width=1)
    x += 8

    # AMS humidity
    hum = printer_context.get("ams_humidity", 0)
    if hum == 0:
        hum_col = C_DIM
        hum_lbl = "N/A"
    elif hum <= 2:
        hum_col = C_CRIT
        hum_lbl = str(hum)
    elif hum <= 3:
        hum_col = C_WARN
        hum_lbl = str(hum)
    else:
        hum_col = C_OK
        hum_lbl = str(hum)
    draw.text((x, 6),  "AMS", fill=C_LBL, font=fn10)
    draw.text((x, 18), "HUM", fill=C_LBL, font=fn10)
    draw.text((x, 30), hum_lbl, fill=hum_col, font=fn11)

    return _encode_png(img.convert("RGB"))


def _panel_label(draw: ImageDraw.ImageDraw, x: int, y: int, text: str) -> None:
    """Draw a 10px C_DIM uppercase panel label in the bottom-left of a panel."""
    fn10 = _font(10)
    draw.text((x + 4, y - 14), text, fill=C_DIM, font=fn10)


def _build_composite_png(
    project_thumbnail_png: Optional[bytes],
    project_layout_png: Optional[bytes],
    raw_png: bytes,
    diff_png: Optional[bytes],
    annotated_png: bytes,
    heat_png: bytes,
    health_panel_png: bytes,
    tw: int,
    th: int,
    job_name: str,
    progress_pct: int,
    layer: int,
    total_layers: int,
    remaining_minutes: int,
    verdict: str,
) -> bytes:
    """
    X1 — Master 3×2 dashboard.
    Row 1: Project thumbnail | Plate layout
    Row 2: Raw camera       | Temporal diff (or placeholder)
    Row 3: Annotated detect | Risk heatmap
    Footer: health panel (full width)
    Header: job name, progress bar, layer, ETA
    """
    HEADER_H = 32
    FOOTER_H = 48
    SEP = 2
    cols = 2
    total_w = tw * cols + SEP
    total_h = HEADER_H + th * 3 + SEP * 2 + FOOTER_H

    canvas = Image.new("RGB", (total_w, total_h), C_BG_PAGE)
    draw   = ImageDraw.Draw(canvas)

    fn10 = _font(10)
    fn12 = _font(12)
    fn14 = _font(14)

    # --- Header ---
    badge = _VERDICT_BADGE.get(verdict, _VERDICT_BADGE["clean"])
    # Job name (C_GOLD, truncated)
    name_str = (job_name[:26] + "…") if len(job_name) > 27 else job_name
    draw.text((8, 8), name_str, fill=C_GOLD, font=fn12)
    # Progress bar
    bar_x = 8; bar_y = 24; bar_w = total_w - 16; bar_h = 4
    draw.rectangle([bar_x, bar_y, bar_x+bar_w, bar_y+bar_h], fill=C_DIM)
    fill_w = int(bar_w * progress_pct / 100) if progress_pct else 0
    if fill_w > 0:
        draw.rectangle([bar_x, bar_y, bar_x+fill_w, bar_y+bar_h], fill=badge["fg"])
    # Layer + ETA text overlaid right-side of header
    layer_str = f"L{layer}/{total_layers}" if total_layers else ""
    eta_str   = f"{remaining_minutes}m" if remaining_minutes else ""
    header_right = f"{layer_str}  {eta_str}".strip()
    draw.text((total_w - 8, 8), header_right, fill=C_LBL, font=fn10, anchor="ra")

    def _paste_panel(png_data: Optional[bytes], col: int, row: int, label: str) -> None:
        px = col * (tw + SEP)
        py = HEADER_H + row * (th + SEP)
        if png_data:
            try:
                pimg = Image.open(io.BytesIO(png_data)).convert("RGB").resize((tw, th), Image.LANCZOS)
                canvas.paste(pimg, (px, py))
            except Exception:
                pass
        else:
            # Placeholder
            pdraw = ImageDraw.Draw(canvas)
            pdraw.rectangle([px, py, px+tw-1, py+th-1], fill=(20, 20, 20))
            pdraw.text((px+tw//2, py+th//2), "N/A", fill=C_DIM, font=fn12, anchor="mm")
        # Panel label
        draw.text((px + 4, py + th - 14), label, fill=C_DIM, font=fn10)

    _paste_panel(project_thumbnail_png, 0, 0, "PROJECT")
    _paste_panel(project_layout_png,    1, 0, "LAYOUT")
    _paste_panel(raw_png,               0, 1, "CAMERA")
    _paste_panel(diff_png,              1, 1, "DELTA")
    _paste_panel(annotated_png,         0, 2, "DETECTION")
    _paste_panel(heat_png,              1, 2, "HEATMAP")

    # Panel separators
    for r in range(1, 3):
        sep_y = HEADER_H + r * (th + SEP) - SEP
        draw.rectangle([0, sep_y, total_w, sep_y+SEP], fill=C_DIM)
    sep_x = tw + SEP // 2
    draw.rectangle([sep_x, HEADER_H, sep_x+SEP, HEADER_H + 3*th + 2*SEP], fill=C_DIM)

    # Footer — health panel
    footer_y = HEADER_H + 3 * th + 2 * SEP
    draw.rectangle([0, footer_y, total_w, footer_y + SEP], fill=C_DIM)
    if health_panel_png:
        try:
            hp = Image.open(io.BytesIO(health_panel_png)).convert("RGB")
            hp = hp.resize((total_w, FOOTER_H), Image.LANCZOS)
            canvas.paste(hp, (0, footer_y + SEP))
        except Exception:
            pass

    return _encode_png(canvas)


# ---------------------------------------------------------------------------
# Project asset helpers
# ---------------------------------------------------------------------------
def _decode_data_uri_png(data_uri: str) -> Optional[bytes]:
    """Convert a base64 data URI to raw PNG bytes."""
    try:
        _, encoded = data_uri.split(",", 1)
        return base64.b64decode(encoded)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
def analyze(
    frame_jpeg: bytes,
    printer_context: dict,
    reference_jpeg: Optional[bytes] = None,
    reference_age_s: Optional[float] = None,
    quality: str = "auto",
    project_thumbnail_uri: Optional[str] = None,
    project_layout_uri: Optional[str] = None,
) -> JobStateReport:
    """
    Produce a JobStateReport from a raw JPEG frame.

    Args:
        frame_jpeg        : raw JPEG bytes from camera capture
        printer_context   : dict with keys: nozzle_temp, nozzle_target, bed_temp,
                            bed_target, chamber_temp, part_fan_pct, aux_fan_pct,
                            exhaust_fan_pct, ams_humidity, hms_errors (list),
                            detectors (dict), layer, total_layers, progress_pct,
                            remaining_minutes, job_name, gcode_state
        reference_jpeg    : prior captured frame for diff computation (optional)
        reference_age_s   : seconds since reference was captured (optional)
        quality           : "auto" | "preview" | "standard" | "full"
        project_thumbnail_uri : base64 data URI for plate isometric thumbnail (optional)
        project_layout_uri    : base64 data URI for annotated plate layout (optional)
    """
    log.debug("analyze: frame=%d bytes quality=%s", len(frame_jpeg), quality)

    # Decode frame
    img_pil  = Image.open(io.BytesIO(frame_jpeg)).convert("RGB")
    W, H     = img_pil.size
    frame_rgb = np.array(img_pil)

    # Decode reference
    ref_rgb: Optional[np.ndarray] = None
    if reference_jpeg:
        try:
            ref_pil = Image.open(io.BytesIO(reference_jpeg)).convert("RGB").resize((W, H), Image.LANCZOS)
            ref_rgb = np.array(ref_pil)
        except Exception as e:
            log.warning("analyze: failed to decode reference frame: %s", e)

    # Spaghetti sub-module
    score, hot_pct, strand_score, edge_density, diff_score = _analyse_spaghetti(
        frame_rgb, ref_rgb, W, H
    )

    # YOLO additive layer (purely additive — never raises).
    try:
        from camera.yolo_detector import detect as _yolo_detect
        yolo_detections, yolo_boost, yolo_available = _yolo_detect(frame_jpeg)
        score = min(score + yolo_boost, 1.0)
    except Exception:
        yolo_detections, yolo_boost, yolo_available = [], 0.0, False

    # Resolve verdict and quality
    if score < _THRESH_WARN:
        verdict = "clean"
    elif score < _THRESH_CRIT:
        verdict = "warning"
    else:
        verdict = "critical"

    resolved_quality = _resolve_quality(verdict, quality)
    tw, th = _tier_dims(resolved_quality, W, H)

    log.debug("analyze: verdict=%s score=%.3f quality=%s dims=%dx%d", verdict, score, resolved_quality, tw, th)

    # Extract context fields
    layer           = printer_context.get("layer", 0) or 0
    total_layers    = printer_context.get("total_layers", 0) or 0
    progress_pct    = printer_context.get("progress_pct", 0) or 0
    remaining_min   = printer_context.get("remaining_minutes", 0) or 0
    job_name        = printer_context.get("job_name", "") or ""

    # Build all assets
    raw_png       = _build_raw_png(frame_rgb, tw, th)
    air_zone_png  = _build_air_zone_png(frame_rgb, W, H, tw, th)
    mask_png      = _build_mask_png(frame_rgb, W, H, tw, th)
    annotated_png = _build_annotated_png(
        frame_rgb, W, H, tw, th, verdict, score, hot_pct, strand_score,
        edge_density, diff_score, reference_age_s, layer, total_layers,
    )
    heat_png  = _build_heat_png(frame_rgb, W, H, tw, th, strand_score, verdict, score)
    edge_png  = _build_edge_png(frame_rgb, W, H, tw, th)

    diff_png: Optional[bytes] = None
    if ref_rgb is not None:
        diff_png = _build_diff_png(frame_rgb, ref_rgb, W, H, tw, th, reference_age_s)

    health_panel_png = _build_health_panel_png(tw * 2 + 2, verdict, score, printer_context)

    # Project identity
    project_thumbnail_png = _decode_data_uri_png(project_thumbnail_uri) if project_thumbnail_uri else None
    project_layout_png    = _decode_data_uri_png(project_layout_uri)    if project_layout_uri    else None

    composite_png = _build_composite_png(
        project_thumbnail_png, project_layout_png,
        raw_png, diff_png,
        annotated_png, heat_png,
        health_panel_png,
        tw, th,
        job_name, progress_pct, layer, total_layers, remaining_min, verdict,
    )

    return JobStateReport(
        verdict=verdict,
        score=score,
        hot_pct=hot_pct,
        strand_score=strand_score,
        edge_density=edge_density,
        diff_score=diff_score,
        reference_age_s=reference_age_s,
        quality=resolved_quality,
        yolo_detections=yolo_detections,
        yolo_boost=yolo_boost,
        yolo_available=yolo_available,
        project_thumbnail_png=project_thumbnail_png,
        project_layout_png=project_layout_png,
        raw_png=raw_png,
        diff_png=diff_png,
        air_zone_png=air_zone_png,
        mask_png=mask_png,
        annotated_png=annotated_png,
        heat_png=heat_png,
        edge_png=edge_png,
        health_panel_png=health_panel_png,
        job_state_composite_png=composite_png,
    )
