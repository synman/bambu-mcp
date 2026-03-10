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

The complete result of `analyze()`. All image assets are `bytes` (raw PNG/JPEG).
When returned through the MCP tool or monitor, image bytes are base64-encoded as data URIs.

```python
@dataclass
class JobStateReport:
    # Core detection signals
    verdict: str                    # "clean" | "warning" | "critical"
                                    # Thresholds (Obico-derived, sensitivity-calibrated):
                                    #   clean < thresh_warn, crit >= thresh_crit
    hot_pct: float                  # Fraction of air-zone pixels above brightness threshold
    strand_score: float             # Directional kernel response (strand-like structures)
    diff_score: float | None        # Mean absolute diff from reference frame (None if no ref)
    reference_age_s: float | None   # Seconds since reference was captured (None if no ref)
    quality: str                    # Resolved quality tier: "preview" | "standard" | "full"

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
    factors_radar_png: bytes | None # Failure Drivers radar chart (8-factor spider chart)

    # H — Print Health
    health_panel_png: bytes         # 120px arc gauge; includes confidence band + HMS status

    # X — Composite
    job_state_composite_png: bytes  # Full 3×2 dashboard (PNG bytes; MCP tool encodes as JPEG)
```

---

## Background monitor result dict

`job_monitor.get_latest_result(name)` returns a plain dict. All output paths
(`analyze_active_job()` MCP tool + `/job_state` endpoint + background monitor) share this schema.

### Primary health fields (use these for agent decisions)

| Field | Type | Range | Description |
|-------|------|-------|-------------|
| `success_probability` | float \| None | 0.0–1.0 | **The single number to watch.** 1.0 = very likely to succeed, 0.0 = likely failing. None before first analysis cycle or when stage-gated. |
| `decision_confidence` | float | 0.0–1.0 | **How much to trust success_probability.** < 0.4 = insufficient data; > 0.7 = reliable. Low early in a print — rises as data accumulates. |

The displayed composite value is `success_probability × decision_confidence`. A high
`success_probability` at low `decision_confidence` (e.g. 0.92 × 0.25 = 23%) correctly signals
"not enough data yet" rather than appearing to be a healthy, confident reading.

### Detection signals

| Field | Type | Description |
|-------|------|-------------|
| `verdict` | str | "clean" \| "warning" \| "critical" — threshold-bucketed from `anomaly_score` |
| `anomaly_score` | float | Raw weighted composite score (0.0–1.0) from spaghetti sub-module |
| `hot_pct` | float | Fraction of air-zone pixels above brightness threshold |
| `strand_score` | float | Directional kernel response magnitude |
| `diff_score` | float \| None | Frame delta vs reference; None if no reference stored |
| `reference_age_s` | float \| None | Seconds since reference frame was captured |

### Bayesian model output

| Field | Type | Description |
|-------|------|-------------|
| `factor_contributions` | dict \| None | Per-factor risk 0.0–1.0; keys: `material`, `platform`, `progress`, `anomaly`, `thermal`, `humidity`, `stability`, `settings`. Drives the Failure Drivers radar chart. None when stage-gated. |

### Stability and trend

| Field | Type | Description |
|-------|------|-------------|
| `stable_verdict` | str \| None | Mode of last 5 verdicts; None until ≥3 samples |
| `success_probability_trend` | str | "stable" \| "escalating" \| "improving" \| "building" |
| `success_probability_min` | float \| None | Worst (lowest) success_probability seen in rolling window |

### Stage context

| Field | Type | Description |
|-------|------|-------------|
| `stage` | int | Printer stage code at analysis time (255 = printing normally) |
| `stage_name` | str | Human-readable stage e.g. "printing", "filament change", "nozzle clean" |
| `stage_gated` | bool | True if analysis was skipped — non-printing stage; all health fields None |

### Image assets (data URIs)

| Field | Description |
|-------|-------------|
| `job_state_composite_png` | Full 3×2 dashboard (PNG; ~71 KB at standard quality) |
| `annotated_png` | Camera frame with detection overlays and score inset |
| `factors_radar_png` | 8-factor Failure Drivers spider chart |
| `health_panel_png` | 120px arc gauge panel with confidence band |
| `raw_png` | Unprocessed camera frame |
| `project_thumbnail_png` | 3MF isometric render |
| `project_layout_png` | Annotated plate top-down layout |

---

## decision_confidence factor breakdown

Weighted additive formula (factors sum to 1.0):

| Weight | Factor | Full credit condition |
|--------|--------|----------------------|
| 0.30 | Window fill | 5 of 5 analysis cycles complete |
| 0.25 | Camera live | stage not gated (stage == 255) |
| 0.15 | Print settings | .3mf slicer settings loaded |
| 0.10 | Humidity known | AMS humidity index 1–5 available |
| 0.10 | Past early noise | progress_pct ≥ 5% |
| 0.10 | Filament known | active filament type non-empty |

Approximate values: ~0.19 at print start with no data → 1.0 at full window + all context.

---

## stable_verdict semantics

Computed by `_stable_verdict(window: deque)` in `job_monitor.py`:
- Mode of window when len ≥ 3; None otherwise
- Tie-break: most severe wins (`clean=0, warning=1, critical=2`)
- Uses `collections.Counter` — reproducible across runs

---

## job_state_composite_png vs job_state_composite_jpg

- **`job_state_composite_png`** — raw PNG bytes in `JobStateReport` and monitor cache
- **`job_state_composite_jpg`** — JPEG re-encoding returned by `analyze_active_job()` MCP tool
  - Quality: preview→70, standard→78, full→85
  - ~25 KB at standard quality vs ~71 KB PNG at same resolution
  - MJPEG `/job_state` endpoint serves from cache (PNG)

---

## Stream Server Endpoints

Each MJPEG stream server (started by `start_stream()` / `view_stream()`) runs on its own
ephemeral port and serves the following HTTP endpoints. The base URL is the `url` field
returned by `start_stream()` (e.g. `http://localhost:49152`).

| Endpoint | Method | Content-Type | Description |
|----------|--------|--------------|-------------|
| `GET /` | GET | text/html | Serves the full MJPEG overlay page (HUD + health panel + image panels) |
| `GET /status` | GET | application/json | Live telemetry dict polled every 2 s by the HUD; see schema below |
| `GET /thumbnail` | GET | image/png | Current job isometric 3D render; 404 if no active job or no project data |
| `GET /layout` | GET | image/png | Current job annotated plate top-down layout; 404 if no active job or no project data |
| `GET /annotated` | GET | image/png | Anomaly-detection overlay from background monitor; 204 No Content if no data available |
| `GET /factors_radar` | GET | image/png | Failure Drivers 8-factor spider chart; 204 No Content if no data available |
| `GET /health_panel_img` | GET | image/png | Arc gauge health panel image (120 px); 204 No Content if no data available |
| `GET /snapshot` | GET | image/jpeg | Single live camera frame captured on demand |
| `GET /job_state` | GET | application/json | Full background monitor result dict (same schema as `analyze_active_job()` return value); polled every 8 s by the JOB HEALTH panel |
| `GET /open` | GET | text/html | Named-tab portal page; used by `view_stream()` to open the stream in a persistent browser tab (`bambu-{name}`) |

### `/status` response schema

```json
{
  "gcode_state": "RUNNING",
  "print_percentage": 42,
  "current_layer": 120,
  "total_layers": 280,
  "elapsed_minutes": 65,
  "remaining_minutes": 90,
  "stage_name": "Printing normally",
  "subtask_name": "my_print.gcode.3mf",
  "nozzles": [{"id": 0, "temp": 220.1, "target": 220}],
  "bed_temp": 60.0,
  "bed_temp_target": 60,
  "chamber_temp": 38.5,
  "chamber_temp_target": 45,
  "part_cooling_pct": 100,
  "aux_pct": 0,
  "exhaust_pct": 30,
  "heatbreak_pct": 75,
  "is_chamber_door_open": false,
  "is_chamber_lid_open": false,
  "active_filament": {"type": "PLA", "color": "#FF0000", "remaining_pct": 72},
  "ams_humidity_index": 4,
  "speed_level": 2,
  "wifi_signal": "-62",
  "active_error_count": 0,
  "hms_errors": [],
  "fps": 18,
  "fps_cap": 20
}
```

Fields of note:
- `heatbreak_pct` — heatbreak fan speed %; shown in the Fans row alongside part/aux/exhaust (zero-value fans are hidden)
- `is_chamber_door_open` / `is_chamber_lid_open` — trigger the `#door-warn` orange banner (H2D only)
- `speed_level` — integer (1=Quiet, 2=Standard, 3=Sport, 4=Ludicrous); drives the speed badge
- `fps` / `fps_cap` — live frame rate and configured cap; drives the top-right FPS counter
- `hms_errors` — list of active HMS error objects; rendered as clickable links in the HUD

---

## Disk Persistence

Several camera/monitor artifacts are persisted to `~/.bambu-mcp/` so they survive MCP server
restarts and remain available in FINISH/FAILED states when the print job is no longer active.

### Job health result

`~/.bambu-mcp/job_health_<name>.json`

Written by `job_monitor._save_result()` after each analysis cycle. Read back by
`job_monitor._load_result()` at startup. Contains the full background monitor result dict
including `success_probability`, `decision_confidence`, `stable_verdict`, and all score fields.

Lifecycle:
- Created/updated: every analysis cycle while a print is RUNNING
- Read on: MCP server startup (restores last known state)
- Cleared on: new print job starts (clears stale health from previous job)
- Also available: in FINISH and FAILED states — the last analysis result is retained

### Plate thumbnail and layout images

`~/.bambu-mcp/plate_thumb_<name>.png`  — isometric 3D render
`~/.bambu-mcp/plate_layout_<name>.png` — annotated top-down plate layout

Written by `tools/camera._save_plate_disk()` when a new job starts and project info is loaded.
Read back by `tools/camera._load_plate_disk()` — fills `project_thumbnail_png` and
`project_layout_png` in the job health result even after MCP restarts or in FINISH/FAILED.

Lifecycle:
- Created/updated: when a new print job starts and project metadata is available
- Read on: MCP server startup; any `analyze_active_job()` call when in-memory cache is empty
- Cleared on: `_clear_plate_disk()` — called when a new job replaces the old images
- Also available: in FINISH and FAILED states (user may inspect layout after print completes)
"""
