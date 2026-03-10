"""
http_api_system.py — System and session management routes for the bambu-mcp HTTP REST API.

Sub-topic of http_api. Access via get_knowledge_topic('http_api/system').
"""

from __future__ import annotations

HTTP_API_SYSTEM_TEXT: str = """
# HTTP API — System & Session Routes

Base URL: `http://localhost:{api_port}` — call `get_server_info()` or `GET /api/server_info`
to discover the actual port at runtime (pool default: 49152–49251).
Read routes: GET. Write routes: PATCH (partial resource updates), POST (actions/commands), DELETE (resource destruction) — all accept params as query string, form body, or JSON body.
All routes accept `?printer=<name>` (or `printer` in POST body) to select the target printer.

---

## API Documentation

### GET /api/docs  (or /api/docs/)

Serve the Swagger UI for interactive API exploration.

Opens a browser-friendly interface listing all 51 routes with request parameters,
example responses, and a "Try it out" button for live testing. No authentication required.

### GET /api/openapi.json

Return the OpenAPI 3.0 specification for this API.

Machine-readable JSON spec, auto-generated from route decorators and docstrings.
Compatible with any OpenAPI 3.0 client tool (Postman, Insomnia, code generators, etc.).

---

## Server Info

### GET /api/server_info

Return runtime port pool state for the bambu-mcp server.

No printer parameter required — this is server-level state.

Returns:
  api_port     — TCP port the REST API is currently bound to
  pool_start   — first port in the shared ephemeral pool (default 49152)
  pool_end     — last port in the shared ephemeral pool inclusive (default 49251)
  pool_claimed — sorted list of all currently claimed port numbers
                 (includes REST API port + all active MJPEG stream ports)

Use this route to construct the correct REST API base URL:
  base_url = f"http://localhost:{response['api_port']}/api"

Equivalent MCP tool: `get_server_info()`

---

## Session Management

### PATCH /api/rename_printer

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Rename the printer device on the printer's own firmware.

Query parameters:
- `new_name` (required) — new display name for the printer (shown on touchscreen and in Bambu Studio)

Changes the name stored on the printer itself, not the local MCP identifier.
Returns `{"success": true}`.

Equivalent MCP tool: `rename_printer()`

### PATCH /api/toggle_session

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Pause or resume the MQTT session for the named printer.

If the session is currently connected, this pauses it (stops all telemetry updates).
If already paused, this resumes it (reconnects and restarts telemetry).

Returns `{"session_active": true|false}` reflecting the new state.

Equivalent to the MCP `pause_mqtt_session` / `resume_mqtt_session` tools.

### POST /api/trigger_printer_refresh

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Force the printer to re-broadcast its full state.

Sends ANNOUNCE_VERSION and ANNOUNCE_PUSH via MQTT. The printer responds with a complete
push_status containing all current telemetry. Useful when telemetry appears stale or
fields are missing after reconnection. Returns `{"success": true}`.

---

## Diagnostics

### GET /api/dump_log

Return the bambu-mcp server log.

Returns the full contents of the server log as plain text. Includes MQTT connection
events, command sends, HMS error callbacks, and camera connection activity.
Useful for diagnosing connection issues or unexpected printer behavior.

### DELETE /api/truncate_log

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Truncate the bambu-mcp server log.

Clears the log file on disk. Returns `{"success": true}`.
Use before a test sequence to get a clean log capture.
"""
