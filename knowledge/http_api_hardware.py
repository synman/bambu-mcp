"""
http_api_hardware.py — Nozzle and AI detector routes for the bambu-mcp HTTP REST API.

Sub-topic of http_api. Access via get_knowledge_topic('http_api/hardware').
"""

from __future__ import annotations

HTTP_API_HARDWARE_TEXT: str = """
# HTTP API — Hardware & AI Detector Routes

Base URL: `http://localhost:{api_port}` — call `get_server_info()` or `GET /api/server_info`
All routes: GET. All accept `?printer=<name>` to select the target printer.

---

## Nozzle Configuration

### GET /api/set_nozzle_details

Inform the printer of the installed nozzle diameter and material type.

Query parameters:
- `nozzle_diameter` (required) — `0.2` | `0.4` | `0.6` | `0.8`
- `nozzle_type` (required) — `stainless_steel` | `hardened_steel` | `tungsten_carbide` |
  `brass` | `e3d`

Updates the printer's nozzle profile. Returns `{"success": true}`.

### GET /api/refresh_nozzles

Trigger nozzle hardware re-read.

Sends a REFRESH_NOZZLE command to the printer, which re-reads the installed nozzle
hardware. Use after physically swapping a nozzle on an H2D. Returns `{"success": true}`.

### GET /api/toggle_active_tool

Swap the active extruder between 0 (right) and 1 (left).

H2D dual-extruder only. Has no effect on single-extruder printers.
Returns `{"success": true, "active_tool": 0|1}`.

---

## AI Vision Detectors (X-Cam)

All detector routes accept:
- `enabled` (required) — `true` | `false`
- `sensitivity` (optional) — `low` | `medium` | `high` (default: `medium`)

All return `{"success": true}`.

### GET /api/set_airprinting_detector

Enable/disable the air-printing detector.

Detects when the nozzle extrudes into open air (indicates a clog or grinding condition).
When triggered, the printer halts the print.

### GET /api/set_buildplate_marker_detector

Enable/disable the buildplate ArUco marker detector.

Verifies the build plate type matches the sliced print settings before starting.
If the plate is incompatible, the printer pauses. Does not accept a sensitivity parameter.

### GET /api/set_nozzleclumping_detector

Enable/disable the nozzle clumping/blob detector.

Detects filament accumulating as a blob or clump around the nozzle tip. When triggered,
the printer halts the print to prevent toolhead damage.

### GET /api/set_purgechutepileup_detector

Enable/disable the purge chute pile-up detector.

Detects when purged filament waste accumulates in the purge chute to a level that could
block the toolhead. When triggered, the printer halts.

### GET /api/set_spaghetti_detector

Enable/disable the spaghetti/failed-print detector.

Detects loose spaghetti-like strands indicating a print failure. When triggered,
the printer halts.

---

## GET /api/get_detector_settings

Return the current state of all X-Cam AI detector settings on the printer.

Returns a JSON object with one key per detector. Each entry includes:
- `enabled` (bool) — whether the detector is currently enabled
- `sensitivity` (str) — `low` | `medium` | `high` (present on detectors that support it)
- `supported` (bool) — whether the printer hardware supports this detector

Detectors returned:
- `spaghetti_detector` — loose strand / print failure detection
- `buildplate_marker_detector` — build plate ArUco marker verification
- `airprinting_detector` — air-printing / clog detection
- `purgechutepileup_detector` — purge chute pile-up detection
- `nozzleclumping_detector` — nozzle blob/clump detection
- `nozzle_blob_detect` — legacy home_flag blob detector (no sensitivity)
- `air_print_detect` — legacy home_flag air-printing flag (no sensitivity)
"""
