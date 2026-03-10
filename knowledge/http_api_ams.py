"""
http_api_ams.py — AMS and filament routes for the bambu-mcp HTTP REST API.

Sub-topic of http_api. Access via get_knowledge_topic('http_api/ams').
"""

from __future__ import annotations

HTTP_API_AMS_TEXT: str = """
# HTTP API — AMS & Filament Routes

Base URL: `http://localhost:{api_port}` — call `get_server_info()` or `GET /api/server_info`
Read routes: GET. Write routes: PATCH (partial resource updates), POST (actions/commands), DELETE (resource destruction) — all accept params as query string, form body, or JSON body.
All routes accept `?printer=<name>` (or `printer` in POST body) to select the target printer.

---

## POST /api/load_filament

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Load filament from an AMS slot into the extruder.

Query parameters:
- `slot` (required) — AMS slot index 0–3, or `254` for external spool holder

The AMS unit is determined by the printer's current AMS configuration. To load from a
specific unit+slot, use the MCP `load_filament(name, unit_id, slot_id)` tool instead.
Returns `{"success": true}`.

---

## POST /api/unload_filament

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Unload the currently loaded filament back to AMS.

Returns `{"success": true}`.

---

## POST /api/refresh_spool_rfid

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Trigger an RFID re-scan on an AMS slot to update remaining filament data.

Query parameters:
- `slot_id` (required) — slot index 0–3
- `ams_id` (required) — AMS unit index 0–3

Only works for RFID-equipped Bambu Lab spools. Returns `{"success": true}`.

---

## PATCH /api/set_spool_details

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

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

## POST /api/set_spool_k_factor

Set extrusion calibration k-factor for a spool. (Stub — returns not-implemented.)

This endpoint exists for API compatibility but is not yet implemented.
Returns `{"success": false, "error": "not implemented"}`.

---

## PATCH /api/set_ams_user_setting

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Set an AMS user preference.

Query parameters:
- `setting` (required) — `CALIBRATE_REMAIN_FLAG` | `STARTUP_READ_OPTION` | `TRAY_READ_OPTION`
- `value` (required) — `true` | `false`

Settings: `CALIBRATE_REMAIN_FLAG` = spool-weight remaining estimation; `STARTUP_READ_OPTION`
= RFID scan on power-on; `TRAY_READ_OPTION` = RFID scan on spool insert.
Returns `{"success": true}`.

---

## POST /api/send_ams_control_command

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Send an AMS control command.

Query parameters:
- `cmd` (required) — `PAUSE` | `RESUME` | `RESET`

Returns `{"success": true}`.

---

## POST /api/select_extrusion_calibration

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Select an extrusion calibration profile for a filament slot.

Query parameters:
- `tray_id` (required) — absolute tray ID: `ams_unit_index × 4 + slot` (0–3); external spool = `254`
- `cali_idx` (optional, default `-1`) — index of the saved calibration profile to activate;
  use `-1` to let the printer automatically select the best matching profile for the loaded filament

Returns `{"success": true}`.

---

## POST /api/turn_on_ams_dryer

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Start the AMS filament dryer on a unit.

Query parameters:
- `ams_id` (required) — internal AMS chip_id: AMS 2 Pro units start at `0`, AMS HT starts at `128`
- `target_temp` (optional, default `55`) — target drying temperature in °C
- `duration_hours` (optional, default `4`) — drying duration in hours

Only supported on AMS 2 Pro and AMS HT models. AMS Lite silently ignores this command.
Returns `{"success": true}`.

---

## POST /api/turn_off_ams_dryer

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Stop the AMS filament dryer on a unit.

Query parameters:
- `ams_id` (required) — internal AMS chip_id: AMS 2 Pro units start at `0`, AMS HT starts at `128`

Returns `{"success": true}`.
"""
