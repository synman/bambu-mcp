# open_charts() — Live Temperature & Fan Dashboard

## Background

User shared `chamber_heat_events2.png` — a polished H2D chamber heating chart with event annotations, trend projection, and an ETA marker. Request: expand to cover **all** temperature and fan monitoring fields.

Scale issue: nozzles operate at 200–250°C while bed peaks at ~120°C and chamber at ~80°C. Plotting them on the same linear axis buries the lower traces. Solution: **three separate panels**.

The monitoring infrastructure already exists:
- `data_collector` maintains 8 rolling 60-minute series sampled every ~2.5 s
- matplotlib 3.10.8 is in the venv

---

## Design — Three-panel figure (14×10 inches, dark theme)

**Panel 1 — Nozzle Temperatures** (~30% height)

| Curve | Color | Style |
|-------|-------|-------|
| Right Nozzle (`tool`) | `#FF8C00` orange | solid 1.5 pt |
| Left Nozzle (`tool_1`, H2D only) | `#FFD700` gold | solid 1.5 pt |

- Target lines: horizontal dashed, same color, 50% opacity, labeled `"Right target: N°C"` etc.
- Y axis: °C, auto-ranging (typically 20–280°C)
- "Now" marker: shared vertical dotted white line at last timestamp

**Panel 2 — Bed & Chamber Temperatures** (~35% height)

| Curve | Color | Style |
|-------|-------|-------|
| Bed (`bed`) | `#C8A97E` tan | dashed 1.5 pt |
| Chamber (`chamber`) | `#FF4D6D` red | solid 2 pt + fill |

- Target lines: same pattern as Panel 1
- **Chamber trend + ETA**: if `chamber_target > chamber_current` and ≥20 data points → linear regression on last 10 min → project to target → annotate `"+X min"` at projected intercept
- **Event annotations**: detect when any fan steps by >25% → vertical dotted line labeled with fan name + new value
- Shared "Now" marker

**Panel 3 — Fans** (~35% height)

| Curve | Color |
|-------|-------|
| Part cooling (`part_fan`) | `#00BCD4` cyan |
| Aux (`aux_fan`) | `#E040FB` magenta |
| Exhaust (`exhaust_fan`) | `#69F0AE` lime |
| Heatbreak (`heatbreak_fan`) | `#90A4AE` gray |

- Y axis: 0–100%, step-style (`drawstyle='steps-post'`), light fill
- Shared X axis with Panels 1 & 2

**Global style:**
- Background: `#0b0f1a` (dark navy)
- Text: `#c9d1d9`, grid: `#1a2035`
- X axis: HH:MM relative labels ("−60m", "−30m", "Now")
- Figure title: `"{printer} — Live Telemetry ({window} min)"`

---

## Files Changed

| File | Change | Risk |
|------|--------|------|
| `~/bambu-mcp/tools/charts.py` | **New** — `open_charts(name: str) -> dict` | Low — additive only |

`server.py` auto-registers all public functions from imported tool modules — no changes needed there.

---

## Implementation Steps

```
1. Validate name — get printer; return error if not connected
2. data_collector.get_all_data(name) → extract 8 series
3. Get current targets from printer.state (bed_temp_target, chamber_temp_target, nozzle targets)
4. Convert timestamps → relative minutes from last point
5. Detect fan step-change events (>25%) for cross-panel annotations
6. Build chamber trend + ETA if target > current
7. Render 3-panel matplotlib figure with Agg backend
8. Save to /tmp/bambu-{name}-charts-{ts}.png
9. subprocess.Popen(['open', path])
10. Return {"output_path": path, "fields_plotted": [...], "window_minutes": N}
```

H2D detection: include `tool_1` only if any data point > 0.

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|-----------|
| No data yet | Low | Return `{"error": "insufficient_data"}` |
| `tool_1` plotted as flat zero on A1 | Low | Skip if all values == 0 |
| ETA projection bad slope | Low | Only project if slope > 0.05°C/min; cap at 60 min |

---

## Lateral Impact Assessment

Additive new file only. No existing files modified. No rules changes. No lateral impact.
