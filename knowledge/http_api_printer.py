"""
http_api_printer.py — Printer state routes for the bambu-mcp HTTP REST API.

Sub-topic of http_api. Access via get_knowledge_topic('http_api/printer').
"""

from __future__ import annotations

HTTP_API_PRINTER_TEXT: str = """
# HTTP API — Printer State Routes

Base URL: `http://localhost:8080`
All routes: GET. All accept `?printer=<name>` to select the target printer.

---

## GET /api/printer

Return full printer state as JSON.

Returns the complete BambuState dict: temperatures, fans, AMS units, spools, HMS errors,
job info, print progress, climate, nozzle info, and all other telemetry fields.

Response shape (abbreviated):
```json
{
  "printer_name": "...",
  "gcode_state": "RUNNING",
  "print_percentage": 42,
  "nozzle_temp": 220.0,
  "bed_temp": 90.0,
  "hms_errors": [...],
  ...
}
```

Use this as the primary state snapshot. Equivalent to the MCP `get_printer_state` tool.

---

## GET /api/health_check

Return health status and full printer state.

Same state data as `/api/printer`, wrapped in a health envelope:
```json
{
  "status": "ok",
  "printer": { ... }
}
```

`status` is always `"ok"` when the MQTT session is connected. Returns HTTP 500 with
`"status": "error"` if the printer session is not active.

---

## GET /api/toggle_session

Pause or resume the MQTT session for the printer.

If the session is connected, pauses it (stops telemetry). If paused, resumes it.
Returns the new session state.

```json
{ "session_active": false }
```

Use sparingly — pausing the session stops all telemetry updates.

---

## GET /api/trigger_printer_refresh

Force the printer to re-broadcast its full state.

Sends ANNOUNCE_VERSION and ANNOUNCE_PUSH via MQTT. The printer responds with a full
push_status containing all current telemetry. Useful when state appears stale.

Returns `{"success": true}`.

---

## GET /api/dump_log

Return the bambu-mcp server log.

Returns the current contents of the server log file as plain text.
Useful for diagnosing connection issues, MQTT errors, or unexpected behavior.

---

## GET /api/truncate_log

Truncate the bambu-mcp server log.

Clears the log file. Returns `{"success": true}`.
"""
