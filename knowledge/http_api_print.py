"""
http_api_print.py — Print control routes for the bambu-mcp HTTP REST API.

Sub-topic of http_api. Access via get_knowledge_topic('http_api/print').
"""

from __future__ import annotations

HTTP_API_PRINT_TEXT: str = """
# HTTP API — Print Control Routes

Base URL: `http://localhost:{api_port}` — call `get_server_info()` or `GET /api/server_info`
All routes: GET. All accept `?printer=<name>` to select the target printer.

---

## GET /api/print_3mf

Start printing a .3mf file from SD card.

Query parameters:
- `filename` (required) — full SD card path to the .3mf file
- `platenum` (required) — plate number to print (integer, 1-based)
- `bedtype` (optional) — `auto` | `cool_plate` | `eng_plate` | `hot_plate` | `textured_plate`
- `flow_calibration` (optional) — `true` | `false` (default false)
- `bed_leveling` (optional) — `true` | `false` (default true)
- `timelapse` (optional) — `true` | `false` (default false)
- `use_ams` (optional) — `true` | `false` (default true)
- `ams_mapping` (optional) — JSON array of tray_ids, e.g. `[1,-1,-1,-1]`

⚠️ Irreversible physical action. Present a confirmation summary to the user before calling.

---

## GET /api/pause_printing

Pause the current print job.

The printer finishes the current move before stopping. Resume with `/api/resume_printing`.
Returns `{"success": true}`.

---

## GET /api/resume_printing

Resume a paused print job.

No effect if the printer is not paused. Returns `{"success": true}`.

---

## GET /api/stop_printing

Stop (cancel) the current print job.

⚠️ Irreversible — the print cannot be resumed after stopping.
Returns `{"success": true}`.

---

## GET /api/set_speed_level

Set the print speed profile.

Query parameters:
- `level` (required) — `quiet` | `standard` | `sport` | `ludicrous`

Speed profiles: Quiet = reduced speed/acceleration; Standard = default; Sport = faster;
Ludicrous = maximum speed. Returns `{"success": true}`.

---

## GET /api/skip_objects

Skip one or more objects during the current print job.

Query parameters:
- `objects` (required) — comma-separated list of identify_id integers, e.g. `1,3,5`

Only works while a print is actively running (gcode_state=RUNNING). Objects cannot be
un-skipped once skipped. identify_id values come from `get_project_info()` bbox_objects.
Returns `{"success": true}`.

---

## GET /api/send_gcode

Send raw G-code commands to the printer.

Query parameters:
- `gcode` (required) — G-code command string; use `|` as newline separator for multiple
  commands, e.g. `M104 S0|M140 S0` to turn off both heaters

⚠️ Bypasses all safety checks. Incorrect commands can crash the toolhead or trigger faults.
Returns `{"success": true}`.

---

## GET /api/set_print_option

Set a print option flag.

Query parameters:
- `option` (required) — `AUTO_RECOVERY` | `SOUND_ENABLE` | `FILAMENT_TANGLE_DETECT` |
  `AUTO_SWITCH_FILAMENT` | `NOZZLE_BLOB_DETECT` | `AIR_PRINT_DETECT`
- `enabled` (required) — `true` | `false`

Returns `{"success": true}`.
"""
