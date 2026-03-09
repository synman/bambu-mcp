"""
http_api_ams.py — AMS and filament routes for the bambu-mcp HTTP REST API.

Sub-topic of http_api. Access via get_knowledge_topic('http_api/ams').
"""

from __future__ import annotations

HTTP_API_AMS_TEXT: str = """
# HTTP API — AMS & Filament Routes

Base URL: `http://localhost:{api_port}` — call `get_server_info()` or `GET /api/server_info`
All routes: GET. All accept `?printer=<name>` to select the target printer.

---

## GET /api/load_filament

Load filament from an AMS slot into the extruder.

Query parameters:
- `slot` (required) — AMS slot index 0–3, or `254` for external spool holder

The AMS unit is determined by the printer's current AMS configuration. To load from a
specific unit+slot, use the MCP `load_filament(name, unit_id, slot_id)` tool instead.
Returns `{"success": true}`.

---

## GET /api/unload_filament

Unload the currently loaded filament back to AMS.

Returns `{"success": true}`.

---

## GET /api/refresh_spool_rfid

Trigger an RFID re-scan on an AMS slot to update remaining filament data.

Query parameters:
- `slot_id` (required) — slot index 0–3
- `ams_id` (required) — AMS unit index 0–3

Only works for RFID-equipped Bambu Lab spools. Returns `{"success": true}`.

---

## GET /api/set_spool_details

Update filament metadata for an AMS slot.

Query parameters:
- `tray_id` (required) — absolute tray id (ams_unit_index × 4 + slot_index)
- `tray_info_idx` (optional) — Bambu filament catalog ID, e.g. `GFA00`
- `tray_color` (optional) — hex color string, e.g. `FF0000`
- `nozzle_temp_min` (optional) — integer °C
- `nozzle_temp_max` (optional) — integer °C
- `tray_type` (optional) — filament type string, e.g. `ABS`

⚠️ Pass ALL relevant fields in a single call — empty values clear existing slot metadata.
Returns `{"success": true}`.

---

## GET /api/set_spool_k_factor

Set extrusion calibration k-factor for a spool. (Stub — returns not-implemented.)

This endpoint exists for API compatibility but is not yet implemented.
Returns `{"success": false, "error": "not implemented"}`.

---

## GET /api/set_ams_user_setting

Set an AMS user preference.

Query parameters:
- `setting` (required) — `CALIBRATE_REMAIN_FLAG` | `STARTUP_READ_OPTION` | `TRAY_READ_OPTION`
- `value` (required) — `true` | `false`

Settings: `CALIBRATE_REMAIN_FLAG` = spool-weight remaining estimation; `STARTUP_READ_OPTION`
= RFID scan on power-on; `TRAY_READ_OPTION` = RFID scan on spool insert.
Returns `{"success": true}`.

---

## GET /api/send_ams_control_command

Send an AMS control command.

Query parameters:
- `cmd` (required) — `PAUSE` | `RESUME` | `RESET`

Returns `{"success": true}`.
"""
