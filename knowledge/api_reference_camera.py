"""
api_reference_camera.py — JobStateReport dataclass and background monitor result dict.

Sub-topic of api_reference. Access via get_knowledge_topic('api_reference/camera').
"""

from __future__ import annotations

API_REFERENCE_CAMERA_TEXT: str = r"""
# BambuPrinter API — Camera Analysis Dataclasses

---

## JobStateReport (dataclass)

Located at: bambu-mcp/camera/job_analyzer.py

The complete result of `analyze_active_job()`. All image assets are `bytes` (raw PNG/JPEG).
When returned through the MCP tool, image bytes are base64-encoded as data URIs.

```python
@dataclass
class JobStateReport:
    # Core spaghetti detection metrics
    verdict: str                    # "clean" | "warning" | "critical"
                                    # Thresholds (Obico-derived): clean<0.08, warn<0.20, crit≥0.20
    score: float                    # Composite heuristic score (0.0–1.0)
    hot_pct: float                  # Fraction of air-zone pixels above brightness threshold
    strand_score: float             # Directional kernel response (strand-like structures)
    edge_density: float             # Mean magnitude across 4-direction edge kernels
    diff_score: float | None        # Mean absolute diff from reference frame (None if no ref)
    reference_age_s: float | None   # Seconds since reference was captured (None if no ref)
    quality: str                    # Resolved quality tier: "preview" | "standard" | "full"

    # YOLO additive layer (YOLOv11s, HuggingFace ApatheticWithoutTheA, mAP@50-95=0.82)
    yolo_detections: list           # [{class, confidence, bbox:[x1,y1,x2,y2]}] raw ONNX output
    yolo_boost: float               # Score addend: spaghetti detections > 0.5 conf (× 0.3)
    yolo_available: bool            # True only if model loaded and inference ran successfully

    # P — Project Identity
    project_thumbnail_png: bytes | None   # 3MF isometric render (from project cache)
    project_layout_png: bytes | None      # Annotated top-down plate layout

    # C — Live Camera
    raw_png: bytes                  # Unprocessed camera frame — no overlays
    diff_png: bytes | None          # Temporal diff magnitude × direction (with reference only)

    # D — Anomaly Detection
    air_zone_png: bytes             # Air zone crop enlarged to tier resolution (no overlays)
    mask_png: bytes                 # Binary threshold mask — algorithm transparency
    annotated_png: bytes            # Multi-layer detection overlay + score inset panel
    heat_png: bytes                 # Brightness × strand-likelihood heatmap (air zone)
    edge_png: bytes                 # Multi-orientation edge/direction map (air zone)

    # H — Print Health
    health_panel_png: bytes         # HMS errors, detector states, temps, fans, AMS

    # X — Composite
    job_state_composite_png: bytes  # Full 3×2 dashboard (PNG bytes; MCP tool encodes as JPEG)
```

---

## Background monitor result dict

`job_monitor.get_latest_result(name)` returns a plain dict that extends JobStateReport fields
with background monitor metadata. Key additions:

| Field | Type | Description |
|-------|------|-------------|
| `stable_verdict` | str \| None | Mode of last 5 verdicts; None until ≥3 samples |
| `confidence_window` | list[str] | Raw per-cycle verdicts (up to 5) |
| `stage` | int | Printer stage code at analysis time |
| `stage_name` | str | Human-readable stage (e.g. "printing", "filament_change") |
| `stage_gated` | bool | True if analysis was skipped due to non-printing stage |
| `precheck_triggered` | bool | True if pre-check hot_pct triggered early analysis |
| `analyzed_at` | float | Unix timestamp of analysis |

### stable_verdict semantics

Computed by `_stable_verdict(window: deque)` in `job_monitor.py`:
- Mode of window when len ≥ 3; None otherwise
- Tie-break: most severe wins (`clean=0, warning=1, critical=2`)
- Uses `collections.Counter` — reproducible across runs

### yolo_boost formula

`score += confidence × 0.3` only when:
- `class == "spaghetti"` AND `confidence > 0.5`

Sourced from Obico multi-frame weighting design. YOLO is purely additive.
If model unavailable: `yolo_available=False`, score unchanged.

### job_state_composite_png vs job_state_composite_jpg

- **`job_state_composite_png`** — raw PNG bytes stored in `JobStateReport` and the monitor cache
- **`job_state_composite_jpg`** — JPEG-encoded version returned by `analyze_active_job()` MCP tool
  - Quality: preview→70, standard→78, full→85
  - ~25 KB at standard quality; ~71 KB PNG at same resolution
  - MJPEG `/job_state` endpoint serves full result from cache (always PNG)
"""
