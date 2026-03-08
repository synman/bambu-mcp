"""
http_api_climate.py — Climate and lighting routes for the bambu-mcp HTTP REST API.

Sub-topic of http_api. Access via get_knowledge_topic('http_api/climate').
"""

from __future__ import annotations

HTTP_API_CLIMATE_TEXT: str = """
# HTTP API — Climate & Lighting Routes

Base URL: `http://localhost:8080`
All routes: GET. All accept `?printer=<name>` to select the target printer.

---

## GET /api/set_bed_target_temp

Set the heated bed temperature.

Query parameters:
- `temp` (required) — integer °C; use `0` to turn off bed heating

Returns `{"success": true}`.

---

## GET /api/set_chamber_target_temp

Set the chamber temperature target.

Query parameters:
- `temp` (required) — integer °C

On printers with active chamber heating (H2D), sets the target. On printers without
managed chamber heating (A1, P1S), records the ambient temperature for firmware use.
Returns `{"success": true}`.

---

## GET /api/set_tool_target_temp

Set the nozzle temperature for the active tool.

Query parameters:
- `temp` (required) — integer °C; use `0` to turn off nozzle heating

Applies to the currently active extruder. For H2D dual-extruder, use the MCP
`set_nozzle_temp(name, temp, extruder=0|1)` tool to target a specific nozzle.
Returns `{"success": true}`.

---

## GET /api/set_fan_speed_target

Set the part-cooling fan speed.

Query parameters:
- `percent` (required) — integer 0–100

The part-cooling fan blows directly on the printed part to cool it. Critical for PLA
and PETG; often set to 0 for ABS to prevent warping. Returns `{"success": true}`.

---

## GET /api/set_aux_fan_speed_target

Set the auxiliary (recirculation) fan speed.

Query parameters:
- `percent` (required) — integer 0–100

The aux fan circulates chamber air and drives HEPA filtration on supported printers.
Returns `{"success": true}`.

---

## GET /api/set_exhaust_fan_speed_target

Set the exhaust fan speed.

Query parameters:
- `percent` (required) — integer 0–100

The exhaust fan vents chamber air out of the printer. Used to expel fumes when
printing ABS, ASA, or other engineering filaments. Returns `{"success": true}`.

---

## GET /api/set_light_state

Set the chamber light.

Query parameters:
- `state` (required) — `on` | `off`

Controls all available light nodes: chamber_light, chamber_light2, column_light.
Returns `{"success": true}`.
"""
