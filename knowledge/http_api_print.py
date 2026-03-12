"""
http_api_print.py — Print control routes for the bambu-mcp HTTP REST API.

Sub-topic of http_api. Access via get_knowledge_topic('http_api/print').
"""

from __future__ import annotations

HTTP_API_PRINT_TEXT: str = """
# HTTP API — Print Control Routes

Base URL: `http://localhost:{api_port}` — call `get_server_info()` or `GET /api/server_info`
Read routes: GET. Write routes: PATCH (partial resource updates), POST (actions/commands), DELETE (resource destruction) — all accept params as query string, form body, or JSON body.
All routes accept `?printer=<name>` (or `printer` in POST body) to select the target printer.

---

## POST /api/print_3mf

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

⛔ **BLOCKED during active prints** — returns HTTP 409 (Conflict) when `gcode_state` is
`RUNNING` or `PREPARE`. Defense-in-depth: firmware also rejects with "printer busy".

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

## POST /api/pause_printing

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Pause the current print job.

The printer finishes the current move before stopping. Resume with `/api/resume_printing`.
Returns `{"success": true}`.

---

## POST /api/resume_printing

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Resume a paused print job.

No effect if the printer is not paused. Returns `{"success": true}`.

---

## POST /api/stop_printing

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Stop (cancel) the current print job.

⚠️ Irreversible — the print cannot be resumed after stopping.
Returns `{"success": true}`.

---

## PATCH /api/set_speed_level

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Set the print speed profile.

Query parameters:
- `level` (required) — `quiet` | `standard` | `sport` | `ludicrous`

Speed profiles: Quiet = reduced speed/acceleration; Standard = default; Sport = faster;
Ludicrous = maximum speed. Returns `{"success": true}`.

---

## POST /api/skip_objects

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Skip one or more objects during the current print job.

Query parameters:
- `objects` (required) — comma-separated list of identify_id integers, e.g. `1,3,5`

Only works while a print is actively running (gcode_state=RUNNING). Objects cannot be
un-skipped once skipped. identify_id values come from `get_project_info()` bbox_objects.
Returns `{"success": true}`.

---

## POST /api/send_gcode

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

⛔ **BLOCKED during active prints** — returns HTTP 409 (Conflict) when `gcode_state` is
`RUNNING` or `PREPARE`. Firmware does NOT reject injected G-code; commands execute
immediately and can crash the toolhead or damage the active print.

Send raw G-code commands to the printer.

Query parameters:
- `gcode` (required) — G-code command string; use `|` as newline separator for multiple
  commands, e.g. `M104 S0|M140 S0` to turn off both heaters

⚠️ Bypasses all safety checks. Incorrect commands can crash the toolhead or trigger faults.
Returns `{"success": true}`.

---

## POST /api/send_mqtt_command

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Send a raw MQTT command JSON string directly to the printer's request topic.

Query parameters:
- `command_json` (required) — valid JSON string matching the Bambu Lab MQTT command schema

JSON is validated before sending; returns `{"error": "invalid JSON: ..."}` if malformed.
⚠️ LAST-RESORT TOOL. Bypasses all safety checks. Incorrect commands can damage prints,
trigger hardware faults, or put the printer into an unrecoverable state.
Returns `{"success": true}`.

---

## PATCH /api/set_print_option

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Set a print option flag.

Query parameters:
- `option` (required) — `AUTO_RECOVERY` | `SOUND_ENABLE` | `FILAMENT_TANGLE_DETECT` |
  `AUTO_SWITCH_FILAMENT` | `NOZZLE_BLOB_DETECT` | `AIR_PRINT_DETECT`
- `enabled` (required) — `true` | `false`

Returns `{"success": true}`.

---

## POST /api/clear_print_error

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Clear an active print_error on the printer.

Query parameters:
- `print_error` (optional, default `0`) — integer error code to clear; use `0` to clear any
  active error without specifying a code
- `subtask_id` (optional, default `""`) — subtask ID of the failed job from `get_job_info`;
  pass empty string if not known

Sends two commands matching the BambuStudio error-dialog dismissal protocol:
`clean_print_error` (clears the error value) followed by a `uiop` signal (acknowledges the
dialog). Without both, the printer may re-raise the error on the next push_status.
Returns `{"success": true}`.
"""
