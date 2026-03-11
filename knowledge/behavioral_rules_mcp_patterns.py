"""
behavioral_rules_mcp_patterns.py — MCP array patterns, multi-level hierarchy, compressed responses.

Sub-topic of behavioral_rules. Access via get_knowledge_topic('behavioral_rules/mcp_patterns').
"""

from __future__ import annotations

BEHAVIORAL_RULES_MCP_PATTERNS_TEXT: str = """
# Behavioral Rules — MCP Patterns

---

## MCP Array Parameter Pattern

When a tool parameter logically accepts an array (e.g. `ams_mapping`, object lists),
type it as `list | str | None` — never `str | None` alone.

**Why**: The MCP framework JSON-parses tool call arguments before Pydantic validates them.
If a client sends `[2, -1, -1]`, the framework delivers it as a Python `list`. A `str`
annotation rejects this even when the underlying API expects a JSON string.

**Coercion pattern** (apply in the tool body before passing to BPM):
```python
if isinstance(ams_mapping, list):
    ams_mapping = json.dumps(ams_mapping)
```

This bridges MCP clients (send lists naturally) → BambuPrinter methods (expect JSON strings).

---

## Multi-Level Call Hierarchy

Several tools are designed to be called in sequence — each level returns an index that
tells you what sub-calls are available at the next level. **Do not fetch payload data you
don't need — stop at the level that answers your question.**

### Recognizing an index response

An index response contains a list of navigational keys rather than payload data:
- `plates: [1, 2, ..., 14]` — plate numbers to call next
- `summary: {field: {min, max, avg, last, count}}` — field names to call series for
- `contents: {children: [...]}` — directory names to drill into

### Tool hierarchies

**Project file (3 levels)**:
```
Level 1 — get_project_info(name, file, 1)    → {plates:[1..N], ...}  (index)
Level 2 — get_project_info(name, file, N)    → per-plate bbox_objects, filament_used
Level 3 — get_plate_thumbnail(name, file, N) → just the isometric image
           get_plate_topview(name, file, N)   → just the top-down image
```
Images are omitted by default from `get_project_info` — use the dedicated image tools on
demand, only for plates you actually need to view.

**Telemetry history (2 levels)**:
```
Level 1 — get_monitoring_history(name)         → {summary:{field:{min,max,avg,last},...}}
Level 2 — get_monitoring_series(name, "tool")  → full time-series for nozzle temp
           get_monitoring_series(name, "bed")   → full time-series for bed temp
```
Always call `get_monitoring_history()` first to see which fields have meaningful activity
before requesting a full series.

**SD card files (N levels, directory depth)**:
```
Level 1 — list_sdcard_files(name)             → top-level tree (or full tree)
Level 2 — list_sdcard_files(name, "/cache")   → files in /cache only
Level N — list_sdcard_files(name, "/a/b/c")   → arbitrarily deep subtree
```

### Locating a finished job's 3mf file

`get_current_job_project_info()` returns `{"error": "no_active_job"}` when `gcode_state` is
`IDLE`, `FINISH`, or `FAILED`. The correct fallback is **not** a full SD card scan —
construct the path directly from the last job's metadata:

```
get_job_info(name) → subtask_name  →  /_jobs/{subtask_name}.gcode.3mf
```

Then call `get_project_info(name, "/_jobs/{subtask_name}.gcode.3mf", plate_num)`.
The `/_jobs/` prefix and `.gcode.3mf` suffix are fixed. This pattern is reliable for any
recently completed or failed job whose file is still on the SD card.

### Image quality tiers

Image tools use one of two quality systems depending on the tool:

**Camera tools** (`get_snapshot`, `view_stream`) use `resolution` (string) + `quality` (int):

| Profile | `resolution` | `quality` | Approx size | Use |
|---|---|---|---|---|
| native | `"native"` | `85` | 1–4 MB | Calibration, max fidelity |
| high | `"1080p"` | `85` | ~500KB–2MB | Anomaly detection, strand analysis |
| standard | `"720p"` | `75` | ~200–400KB | Routine AI analysis (agent default) |
| low | `"480p"` | `65` | ~80–150KB | Quick status checks |
| preview | `"180p"` | `55` | ~20–40KB | Thumbnails, minimal tokens |

**Plate/file tools** (`get_plate_thumbnail`, `get_plate_topview`) use a tier string `quality`:

| Tier | Size | Use |
|---|---|---|
| `"preview"` | ~5 KB | Quick overview, multiple plates |
| `"standard"` | ~16 KB | Default — renders cleanly inline |
| `"full"` | ~71 KB | When pixel detail is required |

**`analyze_active_job.quality`** uses the same tier strings as plate tools ("preview"/"standard"/"full"/"auto")
and controls composite **output** image size — it is an unrelated axis from camera snapshot quality.

Default to **standard** profile (`resolution="720p"`, `quality=75`) for routine `get_snapshot` calls.
Never use native in polling loops (4 MB × N calls = significant token burn).

---

## Compressed Response Protocol

Some tool responses are gzip+base64 compressed when they exceed 300 chars (the minimum
size at which gzip+base64 produces a smaller output than raw JSON). A compressed response
has this shape:

```json
{
  "compressed": true,
  "encoding": "gzip+base64",
  "original_size_bytes": 55000,
  "compressed_size_bytes": 22000,
  "data": "<base64-encoded gzip bytes>"
}
```

**Decompress (Python one-liner)**:
```python
import gzip, json, base64
data = json.loads(gzip.decompress(base64.b64decode(r["data"])))
```

Tools that may return compressed responses (any tool whose response exceeds 300 chars):
`get_ams_units`, `get_spool_info`, `get_hms_errors`, `get_capabilities`,
`get_ams_status`, `get_detector_settings`, `get_knowledge_topic`, `get_climate`,
`list_sdcard_files`, `get_printer_state`, `dump_log`, `get_monitoring_history` (raw=False),
`get_nozzle_info`, and others with multi-field responses.

Tools that always return raw (response < 300 chars): `get_temperatures`, `get_fan_speeds`,
`get_wifi_signal`, `get_chamber_light`, `get_print_progress`, `get_job_info`,
`get_printer_info`, `get_firmware_version`, `get_stream_url`, `get_server_info`,
`get_printer_connection_status`, `get_session_status`.

**URL factory tools are NOT compressed** — they return `{"url": "..."}` (< 100 chars).
Never try to decompress a URL factory response. See **URL Factory Pattern** below.

### `MAX_MCP_OUTPUT_TOKENS` configuration

**Dual-threshold model:**
- `_MIN_COMPRESS_SIZE` (300 chars) — compression trigger. Any non-binary response above
  this size is gzip+base64 encoded regardless of whether it would overflow the token budget.
- `_max_response_chars()` — ResponseSizeTracker overflow signal only. Used to detect when
  a response would overflow the MCP token budget and auto-tune MAX_MCP_OUTPUT_TOKENS in
  mcp-config.json. No longer drives the compression decision.

The Copilot CLI truncates MCP tool results at `MAX_MCP_OUTPUT_TOKENS × 4` characters
(default 25,000 tokens = 100,000 chars).

**Dynamic auto-tuning (ResponseSizeTracker):**

Every response that passes through `compress_if_large()` is measured. When a new maximum
is observed the tracker:
1. Writes `~/.bambu-mcp/response_size_tracker.json` with `max_chars_seen`, `recommended_tokens`, and `max_label`
2. Updates `~/.copilot/mcp-config.json` → `mcpServers.bambu-mcp.env.MAX_MCP_OUTPUT_TOKENS` = `ceil(max_chars_seen / 4)`

The in-session threshold never rises — the update takes effect on the next MCP server
restart. This prevents false "fits" signals during a session where the CLI still truncates
at the old value.

**Binary response exemption:**

Responses containing `data:` URI values (JPEG/PNG base64) are **never gzip'd**. Image data
is already compressed at capture time; gzip achieves only 0–5% reduction on base64-encoded
JPEG while adding CPU overhead. Binary responses are recorded in the size tracker and
returned as-is regardless of their size.

**Tuning options** (when large payloads are needed):

*Option A — shell (session-scoped):*
```bash
export MAX_MCP_OUTPUT_TOKENS=50000
gh copilot ...
```

*Option B — `mcp.json` `env` block (persistent, recommended):*
```json
{
  "mcpServers": {
    "bambu-mcp": {
      "env": { "MAX_MCP_OUTPUT_TOKENS": "50000" }
    }
  }
}
```

Both paths propagate to the bambu-mcp server. When to raise it: if a single-field
`get_monitoring_series` response is still too large for the client to handle.

The ResponseSizeTracker sets this automatically over time — after running tools that return
large text responses, the recommended value converges toward the actual maximum observed.

---

## URL Factory Pattern

Four tools return `{"url": "http://localhost:{port}/api/..."}` instead of data. These are
tools where the payload reliably exhausts the MCP token budget under normal use:

| Tool | Overflow condition | HTTP route |
|------|--------------------|------------|
| `get_snapshot` | ~4 MB during active prints at native resolution | `GET /api/snapshot?printer={name}&resolution={res}&quality={q}&include_status={bool}` |
| `get_monitoring_data` | ~280K chars (full time-series, 99% of budget) | `GET /api/monitoring_data?printer={name}` |
| `get_monitoring_history` | ~280K chars when raw=True | `GET /api/monitoring_history?printer={name}&raw={bool}` |
| `get_monitoring_series` | ~120K chars (42% of budget per field) | `GET /api/monitoring_series?printer={name}&field={field}` |

**Response shape:**
```json
{"url": "http://localhost:49152/api/snapshot?printer=H2D&resolution=720p&quality=75&include_status=false"}
```

**Agent action (mandatory — do not stop at the URL):**
Fetch the URL immediately via bash/curl. Pre-authorized — no human permission needed.
Same authorization level as HTTP escalation: local GET, read-only, not a scope expansion.

```bash
# Fetch camera frame
URL=$(mcp_call get_snapshot name=H2D | python3 -c "import json,sys; print(json.load(sys.stdin)['url'])")
curl -s "$URL"  # response: {"data_uri": "data:image/jpeg;base64,...", "width": ..., ...}

# Fetch monitoring history summary
URL=$(mcp_call get_monitoring_history name=H2D | python3 -c "import json,sys; print(json.load(sys.stdin)['url'])")
curl -s "$URL" | python3 -m json.tool | head -30
```

**Why URL factory instead of MCP data:** For these tools, P(overflow) is high under normal
use — the agent already falls back to HTTP when the MCP response is truncated. URL factory
eliminates the failed MCP round trip and returns control to the agent immediately.

**URL factory ≠ compressed response:**
- Compressed response → `"compressed": true` key present → decompress with gzip+base64
- URL factory response → `"url": "http://..."` key present → fetch with curl
Never decompress a URL factory response. Never assume a `{"url": ...}` response is an error.

---

## Pre-Authorized HTTP API Escalation (Truncated Responses)

When the Copilot CLI truncates an MCP tool response (saving it to a temp file), switching
to the HTTP REST API is **pre-authorized** — no human permission is needed. This is NOT a
Tier 2 escalation in the normal sense; it is a mechanical fallback for a client rendering
limitation, not a scope expansion.

### When to escalate

- The CLI truncates the response and reports it saved to a temp file, OR
- A tool response contains a `data_uri` field that the CLI cannot render inline, OR
- A compressed response (`compressed: true`) was decompressed but is still too large to
  display (extremely rare — the compressed envelope itself is always small)

### How to escalate

1. **Find the api_port** — call `get_server_info()` or read it from any prior response.
   The REST API is always at `http://localhost:{api_port}/api`.
2. **Look up the equivalent HTTP route** — call `get_knowledge_topic('http_api/<module>')`
   for the relevant module (printer, files, system, ams, climate, hardware, print).
3. **Call the endpoint directly** using bash/curl — no user permission needed.

```bash
# Example: retrieve full printer state via HTTP when get_printer_state was truncated
PORT=$(curl -s http://localhost:49152/api/server_info | python3 -c "import json,sys; print(json.load(sys.stdin)['api_port'])")
curl -s "http://localhost:$PORT/api/printer?printer=H2D" | python3 -m json.tool
```

### Key properties of HTTP escalation

- **Local only** — all HTTP routes are on `localhost`. No external network access.
- **Read-only routes are always safe** — GET routes read state, never modify it.
- **Write routes require the same human permission as MCP tools** — do not call POST/PATCH/DELETE
  routes as an escalation path without the same `user_permission=True` authorization.
- **Not a premium action** — curl to localhost is not a web search, not Tier 3, and does
  not require a Premium Requests `ask_user` gate.
- **Not a scope expansion** — the HTTP API exposes the same operations as the MCP tools.
  Using it to retrieve data that an MCP tool returned but the CLI couldn't display is
  transparent substitution, not a new capability.

### Inherently large responses (image data_uri)

Some tools always return large data regardless of compression because they contain JPEG or
PNG image bytes (already compressed — gzip would not reduce them further):

| Tool | HTTP equivalent | Notes |
|------|----------------|-------|
| `get_snapshot` | `GET /api/snapshot?printer={name}&resolution={resolution}&quality={quality}` | Returns URL via url_factory — fetch directly |
| `get_plate_thumbnail` | No direct HTTP route — use `get_plate_thumbnail()` MCP tool at lower quality | |
| `get_plate_topview` | No direct HTTP route — use `get_plate_topview()` MCP tool at lower quality | |
| `get_project_info(include_images=True)` | `GET /api/get_3mf_props_for_file?printer=P&file=F&plate=N` | |
| `get_current_job_project_info(include_images=True)` | `GET /api/get_current_3mf_props?printer=P` | |
| `analyze_active_job` | `GET /api/analyze_active_job?printer=P&categories=X` | |

`get_snapshot` returns `{"url": "..."}` — see **URL Factory Pattern** above. Fetch the URL
directly; do not treat the URL factory response as an image payload.

For non-snapshot image tools with no direct HTTP equivalent, use a lower `quality` parameter
(`"preview"` or `"standard"`) to reduce response size before escalating.
"""
