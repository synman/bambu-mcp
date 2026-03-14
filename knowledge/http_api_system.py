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

Opens a browser-friendly interface listing all 56 routes with request parameters,
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

## mDNS / Zeroconf Service Discovery

At startup, bambu-mcp registers a Zeroconf/Bonjour mDNS service so non-MCP clients
(Home Assistant integrations, scripts, external tools) can discover the API port without
polling or hard-coding it.

Service type: `_bambu-mcp._tcp.local.`
Instance name: `bambu-mcp._bambu-mcp._tcp.local.`

TXT record fields:

| Key | Example value | Description |
|-----|--------------|-------------|
| `version` | `0.8.0` | bambu-mcp package version |
| `api_url` | `http://localhost:49152/api` | Full REST API base URL including port |
| `printers` | `H2D,A1` | Comma-separated names of all configured printers (empty string if none) |

The service is deregistered automatically when `stop()` is called (MCP server shutdown).

To browse the service from the command line:
```bash
dns-sd -B _bambu-mcp._tcp local.    # macOS
avahi-browse _bambu-mcp._tcp        # Linux
```

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

## Monitoring Data

### GET /api/monitoring_data

Return all telemetry history fields for a printer as raw JSON.

Query params:
- `printer` (required): printer name

Response: the full rolling 60-minute telemetry object including `fields` (dict of field
→ collection), `gcode_state_durations`, and metadata. Each collection contains ~1440
data points sampled every ~2.5 seconds. This is the same payload returned by the
`get_monitoring_data` MCP tool (which redirects here via URL factory).

Fields: tool, tool_1, bed, chamber, part_fan, aux_fan, exhaust_fan, heatbreak_fan.

Equivalent MCP tool: `get_monitoring_data()` — returns `{"url": "..."}` pointing here.

### GET /api/monitoring_history

Return telemetry history: summary statistics (default) or full raw time-series.

Query params:
- `printer` (required): printer name
- `raw` (optional, default `false`): `true` = full time-series; `false` = statistics summary

When `raw=false`: returns lightweight summary with `{min, max, avg, last, count}` for
each of the 8 telemetry fields, plus `gcode_state_durations`.

When `raw=true`: returns the complete rolling 60-minute time-series for all 8 fields
(~1440 data points each, ~280K chars). Use `get_monitoring_series` for a single field.

Equivalent MCP tool: `get_monitoring_history()` — returns `{"url": "..."}` pointing here.

### GET /api/monitoring_series

Return the rolling 60-min time-series for a single telemetry field.

Query params:
- `printer` (required): printer name
- `field` (required): one of `tool`, `tool_1`, `bed`, `chamber`, `part_fan`,
  `aux_fan`, `exhaust_fan`, `heatbreak_fan`

Response: `{"field": "{field}", "series": [{ts, val}, ...]}` (~1440 data points).
Use this instead of `monitoring_history?raw=true` when you only need one metric.

Equivalent MCP tool: `get_monitoring_series()` — returns `{"url": "..."}` pointing here.

---

### GET /api/charts

Render and return the full telemetry dashboard as an HTML page.

Query params:
- `printer` (required): printer name

Response: `text/html` — dark-themed multi-panel dashboard with 6 SVG chart sections.
SVG panels are refreshed every 30 s via `fetch()` calls to `/api/charts_panels`
(no page reload). Equivalent MCP tool: `open_charts()` — opens this URL in the browser.

### GET /api/charts_panels

Return refreshed SVG panels as JSON for AJAX updates.

Query params:
- `printer` (required): printer name

Response: `{"panels": ["<svg>…</svg>", ...], "ts": "YYYY-MM-DD HH:MM:SS"}`.
`panels` is a 6-element array in section order:
  0 Temperature History, 1 Fan Speeds, 2 Anomaly & Health,
  3 Failure Analysis, 4 Camera Calibration, 5 AMS Filament.
Used by the `/api/charts` page to swap SVG content without reloading.

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

### GET /api/snapshot

Capture a single still frame from the printer camera.

Query parameters:
- `printer` (required) — printer name
- `resolution` — `"native"` | `"1080p"` | `"720p"` | `"480p"` | `"360p"` | `"180p"` (default: `"native"`)
- `quality` — JPEG quality integer 1–100 (default: `85`)
- `include_status` — `"true"` to include live print telemetry in the response

Named profiles (agent guidance — no `profile` param):
| Profile | `resolution` | `quality` | Approx payload | When to use |
|---|---|---|---|---|
| native | `"native"` | `85` | 1–4 MB | Calibration, max fidelity |
| high | `"1080p"` | `85` | ~500KB–2MB | Anomaly detection |
| standard | `"720p"` | `75` | ~200–400KB | Routine AI analysis ⚠️ default for agents |
| low | `"480p"` | `65` | ~80–150KB | Quick status |
| preview | `"180p"` | `55` | ~20–40KB | Thumbnails |

Response fields: `data_uri`, `width`, `height`, `resolution`, `quality`, `protocol`, `timestamp`, optionally `status`.

Error responses: `{"error": "no_camera"}`, `{"error": "not_connected"}`, `{"error": "stream_failed"}`.

⚠️ Native resolution in polling loops can reach 4 MB/call — significant token cost. Default to standard for routine agents.

Equivalent MCP tool: `get_snapshot()`

---

### GET /api/stream_url

Return camera stream URL information without starting a server or connecting to the camera.

Query parameters:
- `printer` (required) — printer name

Response fields: `protocol`, `rtsps_url` (password redacted), `local_mjpeg_url`, `streaming` (bool).

Equivalent MCP tool: `get_stream_url()`

---

### POST /api/start_stream ⚠️

Start the local MJPEG camera stream server for a printer. Always runs at native resolution;
per-client quality is applied via URL query params when clients connect.

Request body (JSON):
- `printer` (required) — printer name
- `port` — optional preferred port integer

Response fields: `url` (base stream URL), `port`, `protocol`.

Write guard: this is a POST route because starting a stream creates a background server and
occupies a port (state-changing operation).

Equivalent MCP tool: `start_stream()`

---

### POST /api/stop_stream ⚠️

Stop the local MJPEG camera stream server for a printer.

Request body (JSON):
- `printer` (required) — printer name

Response fields: `stopped` (bool), `name`.

Equivalent MCP tool: `stop_stream()`

---

### POST /api/view_stream ⚠️

Start the MJPEG stream server (if not already running) and open a browser tab at the
requested resolution/quality. Multiple calls with different settings open independent tabs
sharing one server port.

Request body (JSON):
- `printer` (required) — printer name
- `resolution` — `"native"` | `"1080p"` | `"720p"` | `"480p"` | `"360p"` | `"180p"` (default: `"native"`)
- `quality` — JPEG quality integer 1–100 (default: `85`)

See `/api/snapshot` for named profiles. Profiles for streams default to native/85 (browser renders full quality).

Response fields: `url` (parameterized client URL), `port`, `protocol`, `opened` (bool), `overlay_active` (bool).

Equivalent MCP tool: `view_stream()`

---

### GET /api/analyze_active_job

Capture the live camera frame and produce a full active job state report.

Query parameters:
- `printer` (required) — printer name
- `store_reference` — `"true"` to store the current frame as the diff baseline (in-memory)
- `quality` — `"auto"` | `"preview"` | `"standard"` | `"full"` (default: `"auto"`)
  Note: this controls composite **output** image size, not capture resolution.
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
