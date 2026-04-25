# open_charts v3 — Telemetry Dashboard + Health History Extension

## Background

`open_charts()` is a new MCP tool that renders a multi-panel matplotlib telemetry
dashboard and opens it in the default browser. The user expanded scope through
multiple iterations to include:

- **Temperature / fan charts** from the rolling 60-minute `data_collector` history
- **Anomaly signal timeline** (hot_pct, strand_score, diff_score over time)
- **Print health timeline** (success_pct + confidence over time)
- **Spider chart with legend** (8-factor failure-driver breakdown, reusing `_build_radar_png`)
- **Print state breakdown** (pie of `gcode_state_durations`)
- **AMS spool remaining** (horizontal bars per slot/unit)
- **Camera calibration status** (SYNBOT→SHELL corner convergence, inspired by h2d_converge.png)

The user's two critical directives:
1. Health/anomaly time-series data exposed through **existing** `get_monitoring_history()`
   and `get_monitoring_series()` infrastructure — no new endpoints
2. `mjpeg_server.py` is **completely out of scope** — zero changes there

---

## Infrastructure Extension

### `camera/job_monitor.py` — `_health_history` deque

**Current state:** `_fp_history: deque(maxlen=10)` — stores raw `float` fp values only.

**Change:** Replace with `_health_history: deque(maxlen=60)` — structured records per analysis cycle:

```python
_health_history: deque = deque(maxlen=60)   # ~1-hour window at 60s intervals
```

Each record appended at analysis time (alongside existing result build at line ~516):

```python
{
    "ts":            time.time(),
    "success_pct":   ph,          # float | None
    "confidence":    dc,          # float
    "hot_pct":       round(report.hot_pct, 4),
    "strand_score":  round(report.strand_score, 4),
    "diff_score":    round(report.diff_score, 4) if report.diff_score is not None else None,
    "remaining_min": printer_context.get("remaining_minutes", 0),
    "factors":       factors,     # dict of 8 factor floats | None
}
```

**Backward compat:** `fp_trend` and `fp_peak` compute from the last 10 `success_pct`
values in `_health_history` (no external callers of `_fp_history` found).

**New accessor:**
```python
def get_health_history(printer_name: str) -> list[dict]:
    """Return list of health history records for the named printer, newest last."""
```

### `tools/system.py` — Extend `get_monitoring_history()` and `get_monitoring_series()`

`get_monitoring_history()` appends a `health` key to its response:
- `raw=False` (summary): `health = {field: {min, max, avg, last, count}}` for each health field
- `raw=True`: `health = {"series": [...records...]}` — full history list

`get_monitoring_series(field)` routes health field names to `job_monitor.get_health_history()`:
- Health field names: `success_pct`, `confidence`, `hot_pct`, `strand_score`, `diff_score`, `remaining_min`
- Any of these → fetch health history, extract that field as `[{"t": ts, "v": value}, ...]`
- Docstrings updated to document the new fields

### `api_server.py` — Same extension on HTTP routes

`/api/monitoring_history` and `/api/monitoring_series` both extended with the same
health-field routing via `job_monitor.get_health_history()`.

---

## Panel Layout

**Figure:** 16 × 28 in, 100 DPI → 1600 × 2800 px PNG, dark `#0d1117` background.

```
GridSpec(6 rows, 2 cols) with height_ratios=[3, 2, 2.5, 3.5, 3.5, 1.5]

Row 0  │ Nozzle Temps               │ Bed & Chamber Temps        │
Row 1  │ ─── Fans (full width) ─────────────────────────────── │
Row 2  │ Anomaly Signals History    │ Print Health Timeline       │
Row 3  │ Spider Chart + Legend      │ Print State Breakdown (pie) │
Row 4  │ ─── Camera Calibration Status (full width) ─────────── │
Row 5  │ ─── AMS Spool Remaining (full width) ──────────────── │
```

---

## Panel Specifications

### Row 0 — Left: Nozzle Temps
- Source: `data_collector.get_collection(name, "tool")` + `"tool_1"` (H2D)
- X-axis: rolling time (minutes ago, negative = past)
- Single-extruder: one line labeled "Nozzle"; H2D: "Right Nozzle" + "Left Nozzle" (T0 left of T1 physically)
- Dashed horizontal band at ±5°C around last known target value
- Colors: `#ff6b6b` (Right/single), `#ffa94d` (Left)

### Row 0 — Right: Bed & Chamber Temps
- Source: `"bed"` + `"chamber"` series
- Two lines: bed `#74c0fc`, chamber `#63e6be`
- Same target-band treatment if targets available from live state

### Row 1 — Fans (full width)
- Source: `"part_fan"`, `"aux_fan"`, `"exhaust_fan"`, `"heatbreak_fan"` series
- Step chart (`drawstyle='steps-post'`), Y-axis 0–100%
- Colors: `#a9e34b`, `#4dabf7`, `#ff8787`, `#da77f2`; zero-value fans shown as flat line

### Row 2 — Left: Anomaly Signals History
- Source: `job_monitor.get_health_history(name)` → `hot_pct`, `strand_score`, `diff_score`
- Three lines on shared Y 0–1; threshold bands at 0.08 (warning) and 0.20 (critical)
- Empty when no health history (prints "No health data — awaiting first analysis cycle")

### Row 2 — Right: Print Health Timeline
- Source: `job_monitor.get_health_history(name)` → `success_pct`, `confidence`
- `success_pct`: filled area under curve, green→yellow→red gradient via `LinearSegmentedColormap`
- `confidence`: dashed line overlay, secondary Y-axis 0–1
- `remaining_min`: tertiary X-top-axis if present (time remaining labels)

### Row 3 — Left: Spider Chart + 8-Row Legend
- Spider: embed `_build_radar_png()` bytes via `PIL.Image.open(io.BytesIO(b))` → `ax.imshow()`
- Legend table below spider (or alongside via nested GridSpec):

| Factor | Description |
|--------|-------------|
| Material | Base failure rate by filament type (PLA=low, PC/PA=high) |
| Platform | Printer series risk modifier (H2D=best, A1=worst) |
| Progress | Survival hazard remaining (most failures before 15%) |
| Anomaly | Camera AI detection signal strength (spaghetti/air printing) |
| Thermal | Env temp risk (door open, nozzle/bed drift, chamber mismatch) |
| Humidity | Hygroscopic penalty × AMS moisture (material tier × index) |
| Stability | Signal consistency trend (sustained clean lowers score) |
| Settings | Slicer config risk (brim, infill, wall count, supports) |

- Legend rendered as `ax.table()` on a separate hidden-spine axis below the image axis

### Row 3 — Right: Print State Breakdown (Pie)
- Source: `data_collector.get_summary(name)["gcode_state_durations"]`
- Explode RUNNING slice (0.05); colors per state:
  `RUNNING=#40c057`, `PAUSE=#ffd43b`, `PREPARE=#74c0fc`, `FAILED=#ff6b6b`, `FINISH=#63e6be`, `IDLE=#868e96`
- Auto-percentage labels; legend on right side
- Title: "Print State Distribution (rolling window)"

### Row 4 — Camera Calibration Status (full width, inspired by h2d_converge.png)
- Data: `coord_transform.py` — `SYNBOT`, `SHELL`, `OFFSETS` dicts (static import, no live data)
- Two filled polygons (NL, NR, FR, FL order):
  - SYNBOT: orange, alpha=0.12 fill, solid border
  - SHELL: cyan, alpha=0.12 fill, dashed border; note FL/FR as extrapolated (grey annotation)
- Dashed connector lines per corner (SYNBOT[k] → SHELL[k]):
  - FL: `#ff9f43`, NL: `#26de81`, NR: `#45aaf2`, FR: `#fd79a8`
- Diamond markers (size=100) at SYNBOT corners, circle markers at SHELL corners
- Distance label at each line midpoint: `"729px"` etc from `OFFSETS[k]["dist"]`
- Corner labels (FL, FR, NL, NR) at each point, offset to avoid overlap
- Annotation box (top-right): `"Reproj: 8.62px · 5 inliers · 2026-03-13"`
- Y-axis clipped to visible range of in-frame corners (NL, NR); FL/FR shown with note
- Title: "Camera Calibration Status — SYNBOT → SHELL Corner Convergence"

### Row 5 — AMS Spool Remaining (full width)
- Source: `get_spool_info(name)` → all spools list
- One horizontal bar per spool; bar color = spool hex color (ARGB stripped to RGB)
- Label: `"AMS 0 Slot 2 · Bambu PLA Basic (darkorange)"` style
- Sorted by unit_id, then slot_id
- Empty bar shows as light grey with "empty" label
- X-axis: 0–100%; vertical reference line at 20% (low-filament warning)
- Title: "AMS Filament Remaining"

---

## File Change Table

| File | Change | Risk |
|------|--------|------|
| `tools/charts.py` | **NEW** — `open_charts(name)` tool, 9 panels, ~350 lines | Low — new file |
| `server.py` | 2 lines: import + `_TOOL_MODULES` entry | Very low |
| `camera/job_monitor.py` | Replace `_fp_history` with `_health_history`; update `fp_trend`/`fp_peak` derivation; add `get_health_history()` accessor | Low — internal only |
| `tools/system.py` | Extend `get_monitoring_history()` + `get_monitoring_series()` to include health fields | Low — additive |
| `api_server.py` | Extend `/api/monitoring_history` + `/api/monitoring_series` with health routing | Low — additive |
| `mjpeg_server.py` | **NO CHANGES** | — |

---

## Risk Assessment

| Area | Risk | Mitigation |
|------|------|-----------|
| `_health_history` replace breaks `fp_trend`/`fp_peak` | Medium | Derive these from `success_pct` field in new records — same math |
| AMS `get_spool_info()` call inside chart function | Low | Wrap in try/except; show "AMS data unavailable" if fails |
| Calibration panel SYNBOT/SHELL coordinate range is huge (Y: -2178 to 2226) | Low | Clip Y-axis to in-frame corners; show FL/FR as out-of-frame annotations only |
| `_build_radar_png()` requires active job result | Low | Fall back to uniform 0.5 radar if no result cached |
| Chart takes long to render (matplotlib) | Low | All data is local/in-memory; 100 DPI should render in <2s |

---

## Open Questions

None — all design decisions confirmed by user through iteration.

---

## Lateral Impact Assessment

| Rules Section | Impact | Action |
|---------------|--------|--------|
| Knowledge Completeness Obligation | `get_monitoring_history()` and `get_monitoring_series()` docstrings document new `health` field | Covered by docstring updates in implementation |
| MCP↔HTTP contract parity | Both MCP tools and HTTP routes extended identically | Covered — same data path |
| `mjpeg_server.py` out of scope | Confirmed by user: "there are no changes to the view stream page" | No action needed |
| BPM Write Scope Lock | No bpm changes | N/A |
