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

## How Print Health is Measured

The system answers one question: **"What is the probability this print succeeds?"**

It does this in three layers.

**Layer 1 — Camera signals.** The camera watches the air zone above the print — the space where
failures like spaghetti and strands appear first. Four signals are extracted: an overall anomaly
score, how much of the zone is anomalously bright, how strand-like the visible features are, and
how much the frame has changed since a stored reference. These collapse into a verdict: clean,
warning, or critical.

**Layer 2 — Failure probability model.** A multi-factor Bayesian model combines the verdict with
printer telemetry: what material is loaded and how failure-prone it is, what printer series is
running, how far through the print we are (early layers are riskiest), environmental conditions
(bed temperature delta from target, chamber temperature vs. what the material requires), how wet
the filament is from AMS humidity, whether the verdict has been stable over time, and what the
slicer settings were. The result is `success_probability` — a single 0–1 score.

**Layer 3 — Decision confidence.** A separate weight expresses how much to trust the current
estimate, based on how many frames have been analyzed, whether the printer is actually printing
(not leveling or warming up), and whether the necessary context is available (slicer settings,
filament type, humidity reading). The **displayed value** is:

    composite = success_probability × decision_confidence

A high probability at low confidence is not actionable. Multiplying proportionally penalises it —
92% probability at 20% confidence displays as 18%, correctly signalling "not enough data yet."

**Model boundary.** The printer's own detection systems (HMS errors, xcam spaghetti/blob/air-print
detectors) are deliberately excluded from this model. Those are fully managed by firmware — the
printer already halts when their thresholds are exceeded. This model targets what firmware doesn't
cover: gradual environmental drift, material-specific failure risk, and early anomaly accumulation
that may require human judgment rather than an automatic halt.

**Stage gating.** Analysis is skipped entirely when `stage ≠ 255` (auto-leveling, bed warming,
filament changes, calibration). Pre-print stages produce meaningless anomaly scores — material
has not yet been deposited. A STANDBY badge is shown instead.

---

## analyze_active_job — Background Monitor

`analyze_active_job(name, store_as_reference=False, quality="auto", categories=["X"])`

Retrieves the latest result from the **background job monitor daemon**, which automatically:
- Captures a reference frame when a print job starts (no manual `store_as_reference` needed)
- Runs full analysis every 60 seconds while stage == 255 (printing)
- Runs a lightweight pre-check every 10 seconds
- Accumulates a 5-sample confidence window; `stable_verdict` is reliable after 3 cycles
- Skips analysis when the stage is not 255 (filament change, pause, heating, etc.)

---

## Tool Selection Heuristic — analyze_active_job vs get_print_progress

**The key decision**: Does the printer have a camera and is a print active?

### When to use get_print_progress
- **Programmatic checks**: Dashboard-style queries, automation, or numeric data extraction
- **No camera available**: Printer model has no camera (`has_camera=False`)
- **Specific numeric queries**: "percentage", "ETA", "time remaining", "what layer", "is it done", "is it still running"
- **Quick status checks**: When you only need basic progress metrics

**What get_print_progress provides:**
- `gcode_state`, `print_percentage`, `current_layer`, `total_layers`
- `elapsed_minutes`, `remaining_minutes`, `stage_name`, `subtask_name`
- `skipped_objects` list
- Fast, lightweight, no camera dependency

### When to use analyze_active_job
- **Human-facing queries**: Any conversational print status request when camera is present
- **Print health assessment**: "how is it going", "how's the print", "check on it", "any issues", "is it ok"
- **Quality inspection**: "does it look good", "any problems", "spaghetti check", "print health"
- **Visual analysis needed**: When the user wants to understand what's actually happening

**What analyze_active_job provides:**
- **Verdict + stable verdict** (clean / warning / critical)
- **Success probability** (0-1 scale, Bayesian model)
- **Decision confidence** (how much to trust the assessment)
- **Factor contributions** (material, platform, progress, anomaly, thermal, humidity, stability, settings)
- **Anomaly scores** (strand, diff, hot pixel percentage) with threshold context
- **Live camera composite image** with health overlay
- **Health panel image** with gauges and trends

**Recommended invocation pattern for human queries:**
```
analyze_active_job(name, categories=["X","H"], quality="standard")
→ then open_job_state(name) to display results to user
```

### Print Status Display Tiers — Progressive Disclosure Model

Use this model to select the right tool and depth for any print status query.

**Tier 1 — Status check**
- **Trigger**: Numeric/programmatic queries ("percentage", "ETA", "time remaining", "what layer",
  "is it done", "is it still running") OR `has_camera=False`
- **Tool**: `get_print_progress(name)`
- **Nudge to next tier** *(only when `has_camera=True` and print is active)*:
  "Want a full health check with live camera view?"

**Tier 2 — Health check** *(default for human-facing queries when camera is present)*
- **Trigger**: Any conversational print status request when `has_camera=True` and print is active
  ("how is it going", "how's the print", "check on it", "any issues", "is it ok", "progress update")
- **Tool**: `analyze_active_job(name, categories=["X","H"])` → then `open_job_state(name)` to display
- **Nudge to next tier**: "Want a closer look with the live camera feed and diff?"

**Tier 3 — Deep look**
- **Trigger**: Explicit depth signal or dissatisfaction with tier 2
  ("deeper", "more detail", "show me", "boring", "is that all", "worried", "closer look", "anything wrong")
- **Tool**: `analyze_active_job(name, categories=["X","H","C"])` → then `open_job_state(name)`
- **Nudge to next tier**: "Want to see what the anomaly detector is actually looking at internally?"

**Tier 4 — Debug / diagnostic**
- **Trigger**: Explicit request to understand detector internals
  ("how does detection work", "what does the AI see", "scoring inputs", "debug", "diagnostic",
  "false positive", "why did it flag")
- **Tool**: `analyze_active_job(name, categories=["X","H","C","D","P"])` → then `open_job_state(name)`
- **Nudge to external**: "For deeper print diagnostics, the Bambu Lab community forums and the
  bpm/bpa documentation at synman.github.io/bambu-printer-manager are the next stop."

**Orphan asset gating**: `air_zone_png`, `mask_png`, and `heat_png` (Category D) are
AI-internal scoring artifacts — intermediate inputs used to derive `anomaly_score`,
`strand_score`, and `hot_pct`. They have no human-facing display path via `open_job_state()`.
Only request Category D at Tier 4 on explicit user ask. Never include in Tier 1–3 responses.

---

### When to call it
- Tier 2+ trigger words above, or user asks "check the print", "is anything wrong?", "spaghetti?", "job health"
- Proactively when `get_hms_errors()` returns active errors during a print
- Do NOT call it when gcode_state is IDLE, FINISH, or FAILED — returns `{"error": "no_active_job"}`

### Interpreting the result

**PRIMARY FIELDS — use these to make decisions:**

**`success_probability`** — the single number to watch. 0.0–1.0 scale where **1.0 = print will
  almost certainly succeed, 0.0 = print is likely failing**. Higher is always better.
  Treat like a success percentage: 0.95 = healthy, 0.60 = concerning, 0.30 = likely failing.

**`decision_confidence`** — how much to trust `success_probability` right now. 0.0–1.0 scale.
  - < 0.40 → insufficient data; treat `success_probability` as a rough estimate only
  - 0.40–0.70 → moderate confidence; directionally useful
  - > 0.70 → high confidence; reliable enough to act on
  Low values are normal early in a print — they rise automatically as data accumulates.
  Low `decision_confidence` is NOT a warning about the print; it is a warning about the estimate.

**Recommended action thresholds (apply only when decision_confidence > 0.60):**
  - `success_probability < 0.30` → consider pausing and inspecting visually
  - `success_probability < 0.50` → report concern to user, suggest camera check
  - `success_probability > 0.70` → print is healthy; no action needed

---

**SECONDARY FIELDS — signal sources that feed into success_probability:**

**`verdict`** — single-frame verdict from camera signals: "clean" | "warning" | "critical"
  Thresholds (Obico-derived): clean < 0.08, warning 0.08–0.20, critical ≥ 0.20

**`stable_verdict`** — statistical mode of last 5 verdicts; None for first 2 cycles.
  When stable_verdict is None, report "still building confidence (N/5 samples)".

**`anomaly_score`** — raw air-zone anomaly score (0.0–1.0) from the camera frame.

**`hot_pct`** — fraction of air-zone pixels above the hot brightness threshold.

**`strand_score`** — kernel response score for strand-like linear features in the air zone.

**`diff_score`** — frame-to-frame difference score vs. stored reference frame.

**`success_probability_trend`** — rolling direction indicator; positive = improving.

**`success_probability_min`** — lowest `success_probability` recorded this print session.

**`stage_gated`** — True when analysis was skipped due to stage != 255. No score or images
  are produced. A STANDBY state is shown in the HUD.

---

### Categories parameter

Default `categories=["X"]` returns only the composite image (~25 KB at standard).
Request more when needed:

| categories | Content | Approx size (standard) |
|------------|---------|------------------------|
| `["X"]` | Composite image (camera + overlays + health strip) | ~25 KB |
| `["H"]` | Health panel (badge, detectors, AMS humidity) | ~8 KB |
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
