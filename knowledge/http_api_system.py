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

Log verbosity is controlled by two env vars in `~/.copilot/mcp-config.json`:
- `BAMBU_MCP_LOG_LEVEL` — `DEBUG`/`INFO`/`WARNING` (default `WARNING`). Controls what reaches the log file.
- `BAMBU_MCP_BPM_VERBOSE=1` — enables raw MQTT payload logging via bpm's `_on_message` debug output.
  **Requires `BAMBU_MCP_LOG_LEVEL=DEBUG`** — bpm emits MQTT data at debug level; lower log levels silently suppress it.

### DELETE /api/truncate_log

⚠️ WRITE OPERATION — requires explicit user confirmation before calling (same guard as MCP tools with `user_permission=True`).

Truncate the bambu-mcp server log.

Clears the log file on disk. Returns `{"success": true}`.
Use before a test sequence to get a clean log capture.

---

## Printer Discovery

### GET /api/printers

Return all configured printers and their current connection status.

No printer parameter required — this is server-level discovery data.

Response fields:
- `printers`: list of dicts with `name`, `connected`, `session_active`
- `total`: count of configured printers

Use this route to enumerate all available printer names before making targeted requests.
Note: `connected_printers` in `/api/default_printer` lists only currently-connected printers;
this route includes all configured printers regardless of connection state.

Equivalent MCP tool: `get_configured_printers()`

---

## Reference Data

### GET /api/filament_catalog

Return the full filament profile catalog as a JSON array.

No printer parameter required — this is static reference data from BPM.

Each entry contains: `tray_info_idx`, `name`, `vendor`, `filament_type`,
`nozzle_temp_min`, `nozzle_temp_max`, `hot_plate_temp`.

Use `tray_info_idx` (e.g. `GFA00`) to identify Bambu Lab filament profiles when
calling `set_ams_filament_setting` or cross-referencing spool catalog codes.

---

## Camera & Print Health Analysis

### GET /api/analyze_active_job

Capture the live camera frame and produce a full active job state report.

Query parameters:
- `printer` (required) — printer name
- `store_reference` — `"true"` to store the current frame as the diff baseline (in-memory)
- `quality` — `"auto"` | `"preview"` | `"standard"` | `"full"` (default: `"auto"`)
- `categories` — comma-separated asset category letters (default `"X"` — composite only):
  - `P` = project thumbnail + plate layout images
  - `C` = raw camera frame + diff frame
  - `D` = anomaly detection overlays (air zone, mask, annotated, heat map, edge)
  - `H` = print health panel
  - `X` = job state composite (default primary output)

Response fields (always present when a print is active):
- `verdict` — `"clean"` | `"warning"` | `"critical"`
- `anomaly_score` — composite anomaly score (0–1)
- `success_probability` — Bayesian print health score (0–1; 1.0 = fully healthy)
- `decision_confidence` — agent ability to assess failure given current data (0–1)
- `stable_verdict` — consensus verdict from recent analysis window, or null
- `stage` / `stage_name` — current printer stage code and human-readable name
- `layer` — current layer number
- Image keys (base64 data URIs) present when the corresponding category is requested.

Error responses: `{"error": "no_active_job"}`, `{"error": "no_camera"}`, `{"error": "not_connected"}`.

**Note:** `store_reference=true` has an in-memory side effect (stores a reference frame for
diff analysis). This is transient analysis state — not printer state — and is lost on server
restart. No `user_permission` guard is required for this operation.

Equivalent MCP tool: `analyze_active_job()`
"""
