# 🤖 bambu-mcp — Bambu Lab MCP Server

> forge ❯ bambu ❯ **mcp** · application · `claude-forge-bambu-mcp`
>
> **Archetype:** MCP server + REST API daemon
> **Language:** Python 3.12+ · setuptools · `.venv/` local install
> **Interfaces:** MCP (stdio/SSE) · HTTP REST (49152+) · MJPEG streams
> **Depends on:** 📦 bambu-printer-manager — MQTT, FTPS, state
> **Shared rules:** `~/ai/forge/bambu/.claude/rules/bambu-ecosystem.md`

## Build / Validate

- Per-file: `python -m py_compile <file>.py`
- Smoke test: `.venv/bin/python3 smoke_test.py`
- Reload: `~/bin/mcp-reload`
- Full restart sequence: see shared rules — MCP Server Restart

## Architecture

- 83 MCP tools, 79 HTTP routes, 1 system prompt (domain knowledge lives in node-kb-mcp `bambu-*` articles)
- All printer ops route through BPM library via `session_manager.get_printer(name)`
- BPM is considered stable — do not modify it to solve MCP-layer problems
- No tool may open its own direct FTPS/MQTT/socket/HTTP connection (camera streaming excepted)
- REST API server and MJPEG camera streams draw ports from a shared `PortPool` (default 49152-49251)
- Port discovery: `get_server_info()` MCP tool or `GET /api/server_info` — never hardcode ports
- Camera subsystem: RTSPS (H2D/X1) + TCP-TLS (A1/P1), MJPEG encoding, ONNX anomaly detection
- Secrets: `~/.bambu-mcp/secrets.enc` (AES-256-GCM, separate from isaac vault)
- Console script entry point: `bambu-mcp = "server:main"`
- 25 runtime dependencies (Flask, mcp SDK, av, onnxruntime, numpy, PIL, zeroconf)

### HTTP Route Reference

`api_server.py` route docstrings are the Swagger/OpenAPI source of truth. The agent-facing
mirror is the node-kb-mcp `bambu-http-*` articles (tagged `source: derived:api_server.py`);
regenerate them from the routes when the API changes.

### Filesystem Persistence

Pattern: `~/.bambu-mcp/<feature>_<printer_name>.<ext>` — save on state change, load at startup, clear on new job start.

### Live Printer State Access

- **MCP context**: Use MCP tools (`bambu-mcp-get_temperatures`, etc.)
- **Bash/scripts**: Use local REST API `http://localhost:49152/api/...` (probe ports 49152-49158)
- **Container API**: `curl -sk -H "Authorization: Basic $AUTH" "https://bambu-h2d.shellware.com/api/printer"`
- Never instantiate `BambuPrinter` directly for read-only queries — use existing container sessions

## Git Policy

Agent-managed — full git lifecycle authorized (stage, commit, push). Descriptive commit messages, push after each logical unit.

- Never update/add/remove dependencies without explicit user approval
- Version in one place: `pyproject.toml` `[project] version`
- After version bump: `pip install -e .` then `python make.py version-sync`

## Credential Registry

All project secrets are stored in the workspace vault (`~/.claude/secrets.vault`).

| Secret | Vault Service | Expires | Notes |
|--------|--------------|---------|-------|
| Container API auth | `bpm_api_auth` | 90d default | Basic auth for BPM container REST API |
