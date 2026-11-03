# bambu-mcp — Bambu Lab MCP Server

> **Archetype:** MCP server + REST API daemon
> **Language:** Python 3.12+ · setuptools · `.venv/` local install
> **Interfaces:** MCP (stdio/streamable-http) · HTTP REST (port pool) · camera streams (fMP4/MSE)
> **Depends on:** bambu-printer-manager — MQTT, FTPS, state

## Build / Validate

- Per-file: `python -m py_compile <file>.py`
- Tests: each `test_*.py` runs directly, `.venv/bin/python3 <file>`, no pytest needed. Smoke test: `.venv/bin/python3 smoke_test.py`
- Published docs: after any tool, docstring, route or `docs/site/overview.md` change, run `.venv/bin/python3 gen_docs.py [OUT_DIR]` (default: the `BAMBU_MCP_DOCS_OUT` setting, else `docs/site/generated`). It also refreshes the README Tools block. `.venv/bin/python3 test_gen_docs.py` checks the generator.
- Daemon administration (supervision, restart, log levels, debug logging): `docs/operators-guide.md`. Do NOT rely on `bambu-mcp-daemon.sh restart` for env changes.

## Architecture

- 104 MCP tools, 87 HTTP routes (via `gen_docs.py`), 1 system prompt. Domain knowledge is not served by this MCP.
- All printer ops route through the bambu-printer-manager library via `session_manager.get_printer(name)`
- The library is considered stable — do not modify it to solve MCP-layer problems
- No tool may open its own direct FTPS/MQTT/socket/HTTP connection (camera streaming excepted)
- REST API server, camera stream servers and their relay children draw ports from a shared `PortPool` (default 49152-49251)
- Port discovery: `get_server_info()` MCP tool or `GET /api/server_info` — never hardcode ports
- Camera subsystem: `camera/mse_relay.py` runs bambu-mse-stream (vendored at a pinned commit as `camera/mse_relay_server.py`) as one child per streaming printer; `camera/mjpeg_server.py` serves the HUD page and proxies the relay's fMP4. RTSPS (H2D/X1) + TCP-TLS (A1/P1) single-frame clients remain for snapshots with no stream running; anomaly detection
- Secrets: `~/.bambu-mcp/secrets.enc` (AES-256-GCM)
- Console script entry point: `bambu-mcp = "server:main"`
- 24 runtime dependencies (Flask, mcp SDK, av, numpy, PIL, zeroconf)

### Settings

Everything environment-specific is read from the process environment, never from source:
`BAMBU_MCP_PORT` (daemon port, default 25099), `BAMBU_PORT_POOL_START` / `BAMBU_PORT_POOL_END`,
`BAMBU_API_PORT`, `BAMBU_API_PRINTER`, `BAMBU_MCP_FTPS_TIMEOUT`, `MAX_MCP_OUTPUT_TOKENS`,
`BAMBU_MCP_RULES_GLOBAL` / `_PRINTER_APP` / `_PRINTER_MANAGER` / `_BAMBU_MCP` (files served as
`bambu://rules/*`, unset means not configured), `BAMBU_MCP_REDACT_HOST_PATTERN` (extra hostname
regex to redact), `BAMBU_MCP_DOCS_OUT`, `BAMBU_THERMAL_OUT`. A daemon gets them from its service
definition.

### HTTP Route Reference

`api_server.py` route docstrings are the Swagger/OpenAPI source of truth, served at `/api/docs`.

### Filesystem Persistence

Pattern: `~/.bambu-mcp/<feature>_<printer_name>.<ext>` — save on state change, load at startup, clear on new job start.

### Live Printer State Access

- **MCP context**: Use MCP tools (`get_temperatures`, etc.)
- **Bash/scripts**: Use the local REST API `http://localhost:<api_port>/api/...`; read the port from `GET /api/server_info`
- **Container API**: `curl -sk -H "Authorization: Basic $AUTH" "https://$CONTAINER_HOST/api/printer"`
- Never instantiate `BambuPrinter` directly for read-only queries — use existing container sessions

## Versioning and dependencies

- Never update/add/remove dependencies without explicit user approval
- Version of record: `__version__` in `server.py`, plain semver, bumped by hand with every change to a serving file (`SERVING` in `hooks/version_bump_gate.py`). `pyproject.toml` `[project] version` must equal it. See the README Versioning section.
- A pre-commit gate blocks a serving change with no bump. Install it once per clone: `python3 hooks/version_bump_gate.py --install`
- Release steps, the post-restart `serverInfo.version` readback and what makes them stale: `docs/operators-guide.md` — Release a change. The commit subject ends with the version

## Secrets

None are kept in the tree. `$AUTH` for the container API comes from the environment or a secret store outside the repo.
