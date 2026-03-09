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

Tools returning images accept a `quality` parameter:

| Tier | Size | Use |
|---|---|---|
| `"preview"` | ~5 KB | Quick overview, multiple plates |
| `"standard"` | ~16 KB | Default — renders cleanly inline |
| `"full"` | ~71 KB | When pixel detail is required |

Applies to: `get_snapshot`, `get_plate_thumbnail`, `get_plate_topview`.

---

## Compressed Response Protocol

Some tool responses are gzip+base64 compressed when they exceed the response size
threshold. A compressed response has this shape:

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

Tools that may return compressed responses: `get_monitoring_series`,
`list_sdcard_files`, `get_printer_state`.

### `MAX_MCP_OUTPUT_TOKENS` configuration

The Copilot CLI truncates MCP tool results at `MAX_MCP_OUTPUT_TOKENS × 4` characters
(default 25,000 tokens = 100,000 chars). `compress_if_large()` reads the same env var
to compress before truncation — thresholds stay in sync automatically.

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
"""
