"""
behavioral_rules_alerts.py — Push alert types, semantics, and recommended actions.

Sub-topic of behavioral_rules. Access via get_knowledge_topic('behavioral_rules/alerts').
"""

from __future__ import annotations

BEHAVIORAL_RULES_ALERTS_TEXT: str = """
# State Change Push Alerts — bambu-mcp

---

## Overview

The MCP server monitors every printer state update and automatically queues
structured alerts when high-visibility state transitions are detected. Alerts
are queued per-printer and consumed by the AI agent on the next turn via
`get_pending_alerts()` or via MCP resource subscription.

**When to call `get_pending_alerts()`:**
- At the start of any printer interaction session (clear=False to peek)
- Any time the user asks "is anything happening?", "any updates?", "what changed?"
- After receiving a `notifications/resources/updated` resource event for
  `bambu://alerts/{name}`

**Push model (MCP resource subscriptions):**
When the MCP client supports `notifications/resources/updated`, the server pushes
an out-of-band notification to the client whenever a new alert is queued. The
client re-reads `bambu://alerts/{name}` at the next available turn and injects
the content into context. The AI agent then surfaces the alert proactively.

If the client does not support resource subscriptions, `get_pending_alerts()`
is the explicit poll path — it works in all clients and is the recommended approach.

**Alert schema:**
```json
{
  "type":      "job_failed",
  "printer":   "H2D",
  "timestamp": "2026-03-10T03:41:00Z",
  "severity":  "high",
  "payload":   { ... type-specific fields ... }
}
```

**Severity levels:**
- `high`   — requires immediate attention or human decision. Surface proactively.
- `medium` — noteworthy state change. Surface if the user is available.
- `low`    — informational. Mention briefly or hold for next natural interaction.

---

## Alert Type Reference

### `job_started`
**Severity:** high
**Trigger:** `gcode_state` transitions to `RUNNING` from `IDLE`, `FINISH`, or `FAILED`.
**Meaning:** A new print job has begun. The printer is actively printing.

**Payload fields:**
| Field | Type | Description |
|---|---|---|
| `subtask_name` | str or null | Job display name from the 3mf project |
| `gcode_file` | str or null | SD card path to the active gcode file |
| `plate_num` | int or null | Plate number within the project |

**Recommended actions to propose:**
- "Your print **{subtask_name}** has started. Would you like me to open the camera stream?"
- Offer `view_stream()` so the user can watch the first layers.
- No action required unless the user asks.

---

### `job_finished`
**Severity:** high
**Trigger:** `gcode_state` transitions to `FINISH` from `RUNNING` or `PAUSE`.
**Meaning:** The print completed successfully. The part is ready to remove.

**Payload fields:**
| Field | Type | Description |
|---|---|---|
| `subtask_name` | str or null | Job display name |
| `gcode_file` | str or null | SD card path |
| `plate_num` | int or null | Plate number |
| `elapsed_min` | int or null | Total print time in minutes |
| `layer_num` | int or null | Final layer count |

**Recommended actions to propose:**
- "Your print **{subtask_name}** is finished after {elapsed_min} minutes. The bed is still hot — wait a few minutes before removing the part."
- Offer `get_temperatures()` to check if the bed has cooled.
- If next print is planned, offer to start it.

---

### `job_failed`
**Severity:** high
**Trigger:** `gcode_state` transitions to `FAILED` (from any prior state).
**Meaning:** The print job was cancelled by the printer due to a fault, or by
the user. This may follow an HMS error. The printer is now idle.

Note: `gcode_state = FAILED` means the *last* job failed — the printer is
idle and ready for a new print. It does NOT mean the printer is currently broken.

**Payload fields:**
| Field | Type | Description |
|---|---|---|
| `subtask_name` | str or null | Job that failed |
| `gcode_file` | str or null | SD card path |
| `plate_num` | int or null | Plate number |

**Recommended actions to propose:**
1. Call `get_hms_errors()` to check for active fault codes — a fault is often the cause.
2. If a `print_error` is set, call `clear_print_error()` before starting a new job.
3. Inspect the bed with `get_snapshot()` or `view_stream()` to check for debris or adhesion failure.
4. Example: "Print **{subtask_name}** failed. Checking for fault codes... [call get_hms_errors()] — {result}. Would you like me to clear the error and try again?"

---

### `job_paused`
**Severity:** medium
**Trigger:** `gcode_state` transitions to `PAUSE` from `RUNNING`.
**Meaning:** The printer has stopped mid-print. The pause may be user-initiated,
AMS-triggered (filament runout), or caused by a sensor condition.

**Payload fields:**
| Field | Type | Description |
|---|---|---|
| `subtask_name` | str or null | Active job name |
| `gcode_file` | str or null | SD card path |
| `plate_num` | int or null | Plate number |
| `stage_id` | int or null | Current stage code at pause time |
| `stage_name` | str | Human-readable stage name (see Stage Map below) |

**Recommended actions by pause cause (use `stage_id` to disambiguate):**
- `stage_id = 17` (paused by user): "Your print is paused. Call `resume_print()` when ready to continue."
- `stage_id = 7` (filament runout): "The printer is paused — filament runout detected. Load a new spool with `load_filament()`, then call `send_ams_control_command(RESUME)` to resume."
- `stage_id = 6` (M400 GCode pause): "The print paused for a GCode M400 wait. Call `resume_print()` to continue."
- `stage_id = 18` (front cover removed): "The printer paused because the front cover was removed. Replace the cover, then call `resume_print()`."
- `stage_id = 20` or `21` (temp malfunction): "The printer paused due to a temperature fault. Check `get_temperatures()` and `get_hms_errors()` before resuming."
- AMS fault pauses: use `send_ams_control_command(RESUME)` — it unblocks the AMS feed AND resumes the print.

See `behavioral_rules/print_state` for the full pause-cause decision table.

---

### `job_resumed`
**Severity:** low
**Trigger:** `gcode_state` transitions to `RUNNING` from `PAUSE`.
**Meaning:** The print has resumed after a pause.

**Payload fields:**
| Field | Type | Description |
|---|---|---|
| `subtask_name` | str or null | Active job name |
| `gcode_file` | str or null | SD card path |
| `plate_num` | int or null | Plate number |

**Recommended actions:** None required. Mention briefly: "Print **{subtask_name}** has resumed."

---

### `stage_change`
**Severity:** medium
**Trigger:** `stg_cur` changes to a notable stage (any stage except 0=idle and
255=printing normally). Debounced: the same stage does not re-fire within 30 seconds.
**Meaning:** The printer entered a specific pre-print calibration phase, a
filament operation, or a pause state.

**Payload fields:**
| Field | Type | Description |
|---|---|---|
| `stage_id` | int | New stage code |
| `stage_name` | str | Human-readable name |
| `prev_stage_id` | int | Prior stage code |
| `prev_stage_name` | str | Prior human-readable name |

**Stage Name Map (all 22 known codes):**
| Code | Name | Action if paused? |
|---|---|---|
| 0 | idle | — |
| 1 | auto-leveling | Wait; normal pre-print step |
| 2 | heatbed preheating | Wait; normal pre-print step |
| 3 | sweeping XY mech | Wait; normal pre-print step |
| 4 | changing filament | Wait; AMS loading/unloading |
| 6 | M400 pause | `resume_print()` |
| 7 | paused: filament runout | Load spool + `send_ams_control_command(RESUME)` |
| 8 | heating nozzle | Wait; normal operation |
| 9 | calibrating extrusion | Wait; normal pre-print step |
| 10 | scanning bed surface | Wait; normal pre-print step |
| 11 | inspecting first layer | Wait; LiDAR first-layer check |
| 12 | identifying build plate | Wait; plate marker detection |
| 13 | calibrating micro lidar | Wait; normal pre-print step |
| 14 | homing toolhead | Wait; normal pre-print step |
| 15 | cleaning nozzle | Wait; normal operation |
| 16 | checking extruder temp | Wait; normal operation |
| 17 | paused by user | `resume_print()` |
| 18 | paused: front cover removed | Replace cover + `resume_print()` |
| 19 | calibrating extrusion flow | Wait; normal pre-print step |
| 20 | paused: nozzle temp malfunction | Check `get_hms_errors()` before resuming |
| 21 | paused: heat bed temp malfunction | Check `get_hms_errors()` before resuming |
| 255 | printing normally | — |

**Recommended actions:**
- Calibration stages (1–3, 8–14, 16, 19): informational only — no action needed.
- Pause stages (6, 7, 17, 18, 20, 21): surface to the user with the appropriate resume guidance above.
- Stage 4 (changing filament): normal during multi-color prints — no action needed.

---

### `hms_error_new`
**Severity:** high
**Trigger:** One or more new active HMS fault codes appear in the printer's error state.
**Meaning:** The printer has a hardware fault. Depending on severity, it may pause the
print or require operator intervention before a new print can start.

Note: Only *actively faulted* errors (with both `device_hms` and `device_error` entries)
are tracked for push. Historical/cleared errors are excluded.

**Payload fields:**
| Field | Type | Description |
|---|---|---|
| `errors` | list[dict] | List of `{code, description}` for each new fault |

**Recommended actions:**
1. Surface each error with its human-readable description from the payload.
2. Call `get_hms_errors()` for full details (severity, is_critical flag).
3. For `is_critical=True` faults: "This error is blocking your print. Check the printer."
4. Provide the Bambu Lab error lookup URL if known: `https://wiki.bambulab.com/en/hms/`.
5. Example response: "⚠️ HMS fault: **{description}** (code {code}). Call `get_hms_errors()` for details and next steps."

---

### `hms_error_cleared`
**Severity:** medium
**Trigger:** All previously active HMS fault codes are gone from the printer state.
**Meaning:** The fault condition has been resolved (either by the printer automatically
or by the user clearing it). The printer may now be ready to accept a new print.

**Payload fields:**
| Field | Type | Description |
|---|---|---|
| `prev_error_count` | int | Number of fault codes that were previously active |

**Recommended actions:**
- "All HMS faults have cleared. The printer appears healthy."
- If `gcode_state = FAILED`, offer to clear the print_error with `clear_print_error()` before starting a new job.

---

### `health_escalated`
**Severity:** high
**Trigger:** The background job health monitor's `stable_verdict` moves to a worse tier:
`clean → warning` or `warning → critical`. Debounced: same tier does not re-fire within 60 seconds.
**Meaning:** The AI vision system has detected a degradation in print quality or an
anomaly (spaghetti, air printing, clumping, etc.). The print may be failing.

`stable_verdict` is a smoothed verdict requiring sustained evidence before changing —
it filters out single-frame noise. A `critical` verdict indicates a high-confidence
failure that almost certainly warrants intervention.

**Payload fields:**
| Field | Type | Description |
|---|---|---|
| `from_verdict` | str | Prior verdict (clean, warning, standby) |
| `to_verdict` | str | New verdict (warning, critical) |
| `score` | float or null | Composite anomaly score (0.0–1.0; >0.20 = critical) |

**Score thresholds (Obico-derived):**
- `< 0.08` = clean
- `0.08 – 0.20` = warning
- `≥ 0.20` = critical

**Recommended actions:**
- `warning`: "Print health has degraded to WARNING (score: {score:.2f}). Consider checking the camera. Call `analyze_active_job()` for a diagnostic frame."
- `critical`: "🚨 Print health is CRITICAL (score: {score:.2f}). The AI detector suspects a print failure. Check now with `view_stream()` or `analyze_active_job()`. If the print has detached or is spaghetting, call `stop_print(user_permission=True)`."
- Offer `view_stream()` for the user to visually confirm before stopping.
- Do NOT stop the print autonomously — always surface to the human first.

---

### `health_recovered`
**Severity:** medium
**Trigger:** The background job health monitor's `stable_verdict` improves:
`critical → warning` or `warning → clean`. Debounced: 60 seconds.
**Meaning:** The anomaly condition that triggered the prior escalation has resolved.
This may happen after a brief first-layer adhesion issue that self-corrected, or
after the AI detector resets when the print stabilizes.

**Payload fields:**
| Field | Type | Description |
|---|---|---|
| `from_verdict` | str | Prior verdict (critical, warning) |
| `to_verdict` | str | New verdict (warning, clean) |
| `score` | float or null | New composite score |

**Recommended actions:**
- "Print health has recovered to **{to_verdict}** (score: {score:.2f}). The print appears to be proceeding normally."
- If the prior escalation was critical, mention: "The earlier critical alert has cleared — watch closely for the next few layers."

---

## Alert Lifecycle

1. **Created:** when `check_and_emit()` detects a qualifying state transition.
2. **Queued:** stored in a per-printer deque (max 50). Oldest alerts are dropped if
   the queue fills (unlikely in practice).
3. **Consumed:** `get_pending_alerts(name, clear=True)` returns all queued alerts
   and empties the queue. Use `clear=False` to peek without consuming.
4. **Push notification:** when a client subscribes to `bambu://alerts/{name}`, the
   server sends `notifications/resources/updated` so the client re-reads the resource.
   The tool path works regardless of subscription support.

**Queue persistence:** Alerts queue in memory. They are NOT persisted across MCP
server restarts. If the server restarts mid-print, the queue starts fresh.

---

## Related Tools

| Tool | Purpose |
|---|---|
| `get_pending_alerts(name)` | Retrieve + clear pending alerts (primary consumption path) |
| `get_hms_errors(name)` | Full details on active + historical HMS faults |
| `clear_print_error(name)` | Dismiss a lingering FAILED/cancelled error |
| `get_print_progress(name)` | Current gcode_state, stage, percentage, time remaining |
| `get_job_info(name)` | Subtask name, gcode file, plate, layer counts |
| `analyze_active_job(name)` | AI diagnostic snapshot (camera + anomaly detection) |
| `view_stream(name)` | Open live camera stream in browser |
| `resume_print(name)` | Resume a user-paused or M400-paused print |
| `send_ams_control_command(name, RESUME)` | Resume after AMS fault or filament runout |
| `stop_print(name, user_permission=True)` | Cancel the print (irreversible) |
"""
