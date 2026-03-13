"""
http_api_climate.py — Climate and lighting routes for the bambu-mcp HTTP REST API.

Sub-topic of http_api. Access via get_knowledge_topic('http_api/climate').
"""

from __future__ import annotations

HTTP_API_CLIMATE_TEXT: str = """
# HTTP API — Climate & Lighting Routes

Base URL: `http://localhost:{api_port}` — call `get_server_info()` or `GET /api/server_info`
Read routes: GET. Write routes: PATCH (partial resource updates), POST (actions/commands), DELETE (resource destruction) — all accept params as query string, form body, or JSON body.
All routes accept `?printer=<name>` (or `printer` in POST body) to select the target printer.

---

## PATCH /api/set_bed_target_temp

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Set the heated bed temperature.

Query parameters:
- `temp` (required) — integer °C; use `0` to turn off bed heating

Returns `{"success": true}`.

---

## PATCH /api/set_chamber_target_temp

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Set the chamber temperature target.

Query parameters:
- `temp` (required) — integer °C

On printers with active chamber heating (H2D), sends MQTT to set and activate the target.
On printers without managed chamber heating (A1, P1S), stores the target value — useful for
external chamber management solutions that read the stored target and drive their own heating hardware.
Returns `{"success": true}`.

---

## PATCH /api/set_tool_target_temp

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Set the nozzle temperature for a specific extruder (or the active tool).

Body / query parameters:
- `temp` (required) — integer °C; use `0` to turn off nozzle heating
- `extruder` (optional) — `0`=right nozzle, `1`=left nozzle; defaults to the currently active tool if omitted

Camera scripts MUST use this route (Tier 1) instead of `send_gcode`/`M104`. A dedicated route exists
for temperature control; using `M104` via `/api/send_gcode` is a Tier 2 escalation violation.
This route is the correct way for scripts to set either nozzle independently on dual-extruder (H2D) printers.
Returns `{"success": true}`.

---

## PATCH /api/set_fan_speed_target

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Set the part-cooling fan speed.

Query parameters:
- `percent` (required) — integer 0–100

The part-cooling fan blows directly on the printed part to cool it. Critical for PLA
and PETG; often set to 0 for ABS to prevent warping. Returns `{"success": true}`.

---

## PATCH /api/set_aux_fan_speed_target

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Set the auxiliary (recirculation) fan speed.

Query parameters:
- `percent` (required) — integer 0–100

The aux fan circulates chamber air and drives HEPA filtration on supported printers.
Returns `{"success": true}`.

---

## PATCH /api/set_exhaust_fan_speed_target

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Set the exhaust fan speed.

Query parameters:
- `percent` (required) — integer 0–100

The exhaust fan vents chamber air out of the printer. Used to expel fumes when
printing ABS, ASA, or other engineering filaments. Returns `{"success": true}`.

---

## PATCH /api/set_light_state

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Set the chamber light.

Query parameters:
- `state` (required) — `on` | `off`

Controls all available light nodes: chamber_light, chamber_light2, column_light.
Returns `{"success": true}`.
"""
