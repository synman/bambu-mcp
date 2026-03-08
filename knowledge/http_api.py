"""
http_api.py — HTTP REST API knowledge summary for bambu-mcp agents.

Top-level topic: get_knowledge_topic('http_api')

This is the agent-facing reference for the bambu-mcp HTTP REST API (api_server.py).
It covers when to use the HTTP API, connection details, and a route category index.
For individual route details, call the sub-topics listed below.

Developer reference: see api_server.py module docstring.
"""

from __future__ import annotations

HTTP_API_TEXT: str = """
# bambu-mcp HTTP REST API

---

## When to use the HTTP API

bambu-mcp exposes 51 REST routes alongside the MCP tools. Use the HTTP API
when:
- An MCP tool does not exist for the required action (the REST API has broader coverage)
- An external client (script, browser, automation tool) needs to interact with the printer
  without an MCP connection
- You need to debug or inspect state via a plain HTTP call

The MCP tools are the preferred interface for AI agents. The HTTP API is the fallback when
MCP coverage is insufficient. When in doubt, check MCP tools first.

---

## Connection details

| Field | Value |
|---|---|
| Base URL | `http://localhost:{api_port}/api` — port is **dynamically assigned** |
| Discover port | Call `get_server_info()` MCP tool or `GET /api/server_info` HTTP route |
| Port range | 49152–49251 by default (IANA RFC 6335 ephemeral range) |
| Port override | `BAMBU_API_PORT` env var — sets a preferred port hint (rotates if taken) |
| Authentication | None — local LAN only |
| Default printer | `BAMBU_API_PRINTER` env var, or `?printer=<name>` query param |
| Content type | All responses are `application/json` |
| Swagger UI | `GET /api/docs` — interactive API explorer |
| OpenAPI spec | `GET /api/openapi.json` — machine-readable spec |

**Always call `get_server_info()` first to discover the actual port before constructing
any HTTP request URL.**  Do not assume port 8080 or any fixed port.

The `printer` query parameter selects which configured printer to use. If omitted, the
server uses the `BAMBU_API_PRINTER` env var default. All routes accept `?printer=<name>`.

---

## Route categories

51 routes across 7 categories. Call a sub-topic for full route details:

| Category | Routes | Sub-topic key |
|---|---|---|
| Printer state | 6 | `get_knowledge_topic('http_api/printer')` |
| Print control | 8 | `get_knowledge_topic('http_api/print')` |
| AMS & filament | 7 | `get_knowledge_topic('http_api/ams')` |
| Climate & lighting | 7 | `get_knowledge_topic('http_api/climate')` |
| Hardware & AI detectors | 8 | `get_knowledge_topic('http_api/hardware')` |
| File management | 12 | `get_knowledge_topic('http_api/files')` |
| System & session | 6 | `get_knowledge_topic('http_api/system')` |

---

## Common patterns

All routes use `GET`. A few file routes also accept `POST` (multipart upload).

Query parameters are URL-encoded. Boolean params: `true` / `false` strings.
Temperature params: integer °C. Fan speed params: integer 0–100 (percent).

Error responses use HTTP 400/500 with `{"error": "<message>"}` body.
Success responses include a `"success": true` key plus any returned data.

Example — discover port then pause print:
```
info = get_server_info()   # MCP tool → {api_port: 49152, ...}
GET http://localhost:{info.api_port}/api/pause_printing?printer=MyPrinter
```

Example: set nozzle temperature to 220°C:
```
GET http://localhost:{api_port}/api/set_tool_target_temp?temp=220&printer=MyPrinter
```
"""
