"""
behavioral_rules_job_analysis.py — analyze_active_job usage rules sub-topic.

Sub-topic of behavioral_rules. Access via get_knowledge_topic('behavioral_rules/job_analysis').
"""

from __future__ import annotations

BEHAVIORAL_RULES_JOB_ANALYSIS_TEXT: str = """
# analyze_active_job — Usage Rules

Sub-topic of behavioral_rules/camera.
For camera tool selection guidance, see `behavioral_rules/camera`.

---

## analyze_active_job — Background Monitor

`analyze_active_job(name, store_as_reference=False, quality="auto", categories=["X"])`

Retrieves the latest result from the **background job monitor daemon**, which automatically:
- Captures a reference frame when a print job starts (no manual `store_as_reference` needed)
- Runs full analysis every 60 seconds while stage == 255 (printing)
- Runs a lightweight pre-check every 10 seconds
- Accumulates a 5-sample confidence window; `stable_verdict` is reliable after 3 cycles
- Skips analysis when the stage is not 255 (filament change, pause, heating, etc.)

### When to call it
- User asks "check the print", "is anything wrong?", "spaghetti?", "job health"
- Proactively when `get_hms_errors()` returns active errors during a print
- Do NOT call it when gcode_state is IDLE, FINISH, or FAILED — returns `{"error": "no_active_job"}`

### Interpreting the result

**PRIMARY FIELDS — use these to make decisions:**

**`print_health`** — the single number to watch. 0.0–1.0 scale where **1.0 = fully healthy, 0.0 = print is likely failing**.
  Higher is better. Treat like a health percentage: 0.95 = healthy, 0.60 = concerning, 0.30 = likely failing.
  This is `1 - failure_probability` — it exists so you never have to invert anything.

**`decision_confidence`** — how much to trust `print_health` right now. 0.0–1.0 scale where 1.0 = high confidence.
  - < 0.40 → insufficient data; treat `print_health` as a rough estimate, not a reliable verdict
  - 0.40–0.70 → moderate confidence; `print_health` is directionally useful
  - > 0.70 → high confidence; `print_health` is reliable enough to act on
  Low values are normal early in a print — they rise automatically as more data accumulates.
  Low `decision_confidence` is NOT a warning about the print; it is a warning about the estimate.

**Recommended action thresholds:**
  - `print_health < 0.30` and `decision_confidence > 0.60` → consider pausing and inspecting
  - `print_health < 0.50` and `decision_confidence > 0.70` → report concern to user, suggest camera check
  - `print_health > 0.70` → print is healthy; no action needed

---

**SECONDARY FIELDS — implementation detail; feeds into print_health:**

**`verdict`** — single-frame heuristic result: "clean" | "warning" | "critical"
  Thresholds (Obico-derived): clean < 0.08, warning 0.08–0.20, critical ≥ 0.20

**`stable_verdict`** — statistical mode of last 5 verdicts; None for first 2 cycles.
  When stable_verdict is None, report "still building confidence (N/5 samples)".

**`failure_probability`** — inverse of print_health (= 1 - print_health). Do not use this directly;
  prefer `print_health` for all agent-facing logic. Retained for backwards compatibility.

**`yolo_available`** — True if YOLOv11s ONNX model is loaded. False = no ML layer, heuristic only.
**`yolo_boost`** — score addend from YOLO spaghetti detections (max 0.3 per detection above 0.5 conf).
**`yolo_detections`** — list of {class, confidence, bbox} — raw ONNX output, not re-derived.

**`stage_gated`** — True when analysis was skipped due to stage != 255. No score or images.

### Categories parameter

Default `categories=["X"]` returns only the composite image (~25 KB at standard).
Request more when needed:

| categories | Content | Approx size (standard) |
|------------|---------|------------------------|
| `["X"]` | Composite image (camera + overlays + health strip) | ~25 KB |
| `["H"]` | Health panel (HMS, temps, fans, AMS) | ~8 KB |
| `["C"]` | Raw camera frame + diff frame | ~35 KB |
| `["D"]` | All anomaly detection images | ~80 KB |
| `["P"]` | Project thumbnail + plate layout | ~20 KB |
| all | Full suite | ~160 KB |

**Never request all categories from an AI agent context** — the total exceeds MCP size limits
at standard quality. Only the MJPEG stream browser endpoint requests all categories safely.

### Response field note

The composite image is returned as `job_state_composite_jpg` (JPEG, not PNG).
PNG assets use `*_png` suffix. This is intentional — JPEG encoding reduces composite
size from ~600 KB to ~22 KB at standard quality.
"""
