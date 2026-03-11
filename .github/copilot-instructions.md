# bambu-mcp Project Instructions

## Git Flow (Agent-Managed)

The agent is authorized to manage the full git lifecycle for bambu-mcp: stage, commit, and push changes without waiting for per-commit user approval. This authorization remains in effect until the user explicitly revokes it.

**Commit standards:**
- Commit messages must be descriptive and scoped to the change
- Push to `origin` after each logical unit of work (don't batch unrelated changes into one push)

---

## ⚠️ DEPENDENCY UPDATES — HARD STOP, CONSULT USER FIRST

**Never update, add, remove, or reinstall any bambu-mcp dependency without explicit user approval in the current conversation turn.**

This applies to:
- `pyproject.toml` dependencies (adding, removing, or version-pinning)
- Direct `pip install` calls into the MCP venv (`.venv/`)
- Reinstalling existing dependencies (e.g. `pip install -e ...`)
- Any change to `requirements.txt` or lockfiles

**Hard requirements:**
- If a fix seems to require a dependency change, stop and present the problem to the user first.
- Only proceed with a dependency change when the user has explicitly approved it **and** no other solution path remains.
- `bpm` (bambu-printer-manager) is considered stable. Assume it works correctly. Do not modify it to work around MCP-layer problems.

---

## Versioning Policy (SemVer)

`bambu-mcp` follows **Semantic Versioning (SemVer)**: `MAJOR.MINOR.PATCH`.

| Component | When to bump |
|---|---|
| `MAJOR` | Breaking change — removes/renames a tool, changes a required parameter, changes return shape incompatibly |
| `MINOR` | New tool added, new optional parameter added (backwards-compatible addition) |
| `PATCH` | Bug fix, docstring fix, internal refactor — no tool signature or behavior change |

**Hard requirements:**
- Version lives in **one place**: `pyproject.toml` → `[project] version = "X.Y.Z"`.
- After any version bump: (1) run `pip install -e .` so `importlib.metadata` reflects the new version, then (2) run `python make.py version-sync` to propagate the version to `README.md` and `PLAN.md`.
- `server.py` reads the version via `importlib.metadata.version("bambu-mcp")` and sets it on `mcp._mcp_server.version`. **Do not hardcode the version string anywhere else.**
- Bump version in the same commit as the change that warrants it. Never bump speculatively.

---

## Dual-Role Model (Mandatory)

The agent simultaneously holds two contexts for bambu-mcp work. These are not modes and require no switching.

### Developer Context

Full read/write access to all workspace repos (`bambu-mcp`, `bambu-printer-manager`, `bambu-printer-app`, `bambu-fw-fetch`, `bambu-mqtt`, `webcamd`). Privileged knowledge of architecture, protocols, and implementation details. Author of the codebase and the rules it operates under. This context governs source editing, debugging, and quality judgment.

Developer source access does not bypass or replace the escalation hierarchy — it supplements Tiers 2–3. Check `get_knowledge_topic()` first; use direct source access only for gaps the knowledge modules don't cover.

### Consumer Context

When MCP tools are live, interact with bambu-mcp exactly as any other MCP client (Claude Desktop, CI runner, third-party agent) would. `bambu_system_context` is the mandatory entry point. `get_configured_printers()` is the first call. Native tool calls only — no direct HTTP, no curl fallback while tools are available.

The quality standard for every tool and docstring is: **can a cold agent with no workspace access complete a real print workflow from tool descriptions alone?** The developer context provides the judgment to evaluate and close gaps; the consumer context defines the bar.

### Escalation Policy — Binding for Both Contexts

The 3-tier escalation policy (`bambu://knowledge/fallback_strategy`) applies to all consumers of bambu-mcp without exception:

1. **Tier 1** — `get_knowledge_topic()` — baked-in knowledge modules. Always first.
2. **Tier 2** — `search_authoritative_sources()` — authoritative vendor and community repos. Developer direct source access enters here as a supplement.
3. **Tier 3** — Broad search. Flagged as lower reliability.

Neither developer context nor privileged source access authorizes skipping Tier 1. If `get_knowledge_topic()` answers the question, stop there.

**MCP tool behavior questions (mandatory Tier-1 check):** When the question is about an MCP tool's parameter semantics, resolution logic, or behavior (e.g. "what value will be passed?", "how is unit_id resolved?", "what does auto-derivation produce?"), the required Tier-1 sources are:
1. The tool's own docstring (visible via `get_knowledge_topic("api_reference/ams")` or the relevant api_reference topic)
2. Live state data from the relevant MCP state tool (e.g. `get_ams_units()` → unit_id=1 = second AMS → ams_id=128)

These two together are almost always sufficient. Reading `_resolve_ams_id` source when `unit_id` is documented as "0-based index" in knowledge and live state shows the unit mapping is a Tier-1 skip — not a Tier-2 escalation. State this explicitly before touching any source file.

**Tier 1 is exhausted when ALL of the following are true:**
- Every relevant `get_knowledge_topic()` topic has been called (not just one)
- The returned content was read in full and does not answer the question
- The agent can state *specifically* what is missing and why Tier 1 cannot fill the gap

**Permission gate — Tier 2 (mandatory):**
Before calling `search_authoritative_sources()` or any Tier 2 tool, the agent MUST use `ask_user`:
> *"Tier 1 knowledge is insufficient for X — I checked [topics]. May I search the authoritative repos (Tier 2)?"*

Wait for an affirmative answer. Plain-text asking does not satisfy this gate.

**Permission gate — Tier 3 (mandatory):**
Before any broad web search, the agent MUST demonstrate both Tier 1 and Tier 2 exhaustion and use `ask_user`:
> *"Tier 1 and Tier 2 are insufficient for X. May I do a web search (Tier 3)?"*

This gate is **in addition to** the Premium Requests `ask_user` requirement — both apply.

**Known bypass traps — these do NOT authorize tier advance:**
- Research/learning directives: "research this", "look it up", "find out about X", "go deep"
- Urgency phrases: "we need this fast", "just find it"
- Scope-expanding: "use whatever sources you need"
- Task delegation: "do whatever research you need"
- Availability: "the tool is right there" — reachability is not authorization

### Operational Access Path Tiers — MCP-Specific

A parallel escalation axis governs *how* the agent accesses the printer at runtime:

| Tier | Access Path | When to use |
|------|-------------|-------------|
| **Tier 1** | MCP tools (`bambu-mcp-*` functions) | Always first — purpose-built, validated, schema-documented |
| **Tier 2** | HTTP REST API via `bash`/`curl` (`http://localhost:PORT/api/`) | Only after Tier 1 exhausted + human permission granted |
| **Tier 3** | Raw MQTT via `send_mqtt_command` / direct bpm invocation | Last resort; highest risk; requires explicit human instruction |

The HTTP REST API is **always reachable** via bash — its availability does **not** authorize its use as a first resort. Falling back to curl instead of an MCP tool is Tier 2 escalation and requires the same gate: demonstrate which MCP tool(s) were tried and why they are insufficient, then use `ask_user` before issuing any `curl` call.

**Bypass trap:** *"The HTTP API is available so I'll use curl"* — availability is not authorization.

---

## Tool Self-Sufficiency Standard (Mandatory)

Every tool, its docstring, and `bambu_system_context` must collectively be sufficient for a cold agent to complete a full print workflow without external guidance.

Apply when authoring or reviewing tools and docstrings: if a naive agent could not determine when to call the tool, what to pass, or what the result means — the docstring needs improvement. The developer context provides the judgment to evaluate this; the consumer context defines the bar.

---

## Architecture

- **bambu-mcp** is an MCP server exposing Bambu Lab printer control as tools.
- All printer operations route exclusively through the **BPM library** (`bambu-printer-manager`) via `BambuPrinter` instances managed by `session_manager`.
- Access printers via `session_manager.get_printer(name)` → `BambuPrinter` instance.
- Use `printer.*` methods for all printer interactions.
- Import BPM library modules (`from bpm.*`) only for types, helpers, and project parsing.
- BPM is stable — do not modify it to solve MCP-layer problems.
- `bambu-printer-app` is a **knowledge reference only** — it must not be referenced or imported at runtime. **Never search, grep, read, or mention `bambu-printer-app` source during any coverage audit, baseline audit, or bpm→mcp traceability work.** It is entirely out of scope for these processes.
- No tool may open its own direct FTPS, MQTT, socket, or HTTP connection to a printer — **with one exception**: camera streaming.
- **Camera streaming exception**: the `camera/` module is explicitly permitted to open direct connections to the printer for video data only:
  - **TCP+TLS port 6000** `[VERIFIED: bambu-mcp camera/tcp_stream.py:76,121 — socket.create_connection((ip, 6000))]` — A1/P1 series camera protocol (`TCPFrameBuffer` in `camera/tcp_stream.py`)
  - **RTSPS** — H2D/X1 series camera protocol (`RTSPSFrameBuffer` in `camera/rtsps_stream.py`)
  - These are raw video transports that BPM does not expose. No other module may use this exception.


## Camera Streaming Architecture

**PyAV thread-safety**: `av.open()` and `container.decode()` are NOT thread-safe across concurrent callers. `ThreadingHTTPServer` spawns one thread per client — never call PyAV from HTTP handler threads. `RTSPSFrameBuffer` owns all PyAV calls in a single background thread; clients share frames via `threading.Condition` (same pattern as `TCPFrameBuffer` / webcamd).

**Buffer pattern**: Both `RTSPSFrameBuffer` and `TCPFrameBuffer` follow the webcamd `lastImage` model — one background reader thread, `_last_frame` shared buffer, `threading.Condition` for client notification, `wait_first_frame()` pre-warm before the server URL is returned to the caller.

**`iter_frames()` contract**: Must check `_last_frame is last` before calling `cond.wait()` — yields the already-buffered frame immediately on first call so browser receives data before any timeout.

**Safari MJPEG compatibility**: Safari intercepts `multipart/x-mixed-replace` responses at the WebKit network layer before JavaScript's `fetch()` can read the body. Fix: serve `/stream` as `Content-Type: application/octet-stream`. The HTML page uses a `fetch()`-based JS multipart parser that reads the raw stream, extracts JPEG frames by `Content-Length`, and sets `img.src` to blob URLs. This bypasses WebKit's broken MJPEG img loader entirely and works on all browsers.

**HTTP protocol**: `_StreamHandler` uses HTTP/1.0 (default — no `protocol_version` override). Do not switch to HTTP/1.1 without chunked encoding — malformed HTTP/1.1 breaks Safari. Raw writes after headers are correct for HTTP/1.0.

**`/snapshot` endpoint**: `GET /snapshot` returns a single JPEG frame (`Content-Type: image/jpeg`, `Content-Length` set, connection closed). It must NOT use the streaming generator — grab one frame with `next(iter(frame_factory()))` and return immediately.

## Stream Session Rules (Mandatory)

**One server per printer** — `MJPEGServer.start()` enforces a single server instance per printer name. A second `start_stream()` call returns the existing URL without spawning a new server. Multiple browser clients can connect to the same server concurrently; each gets an independent frame stream.

**Tab reuse on `view_stream()`** — each call must surface the stream in one tab, never accumulate additional tabs:
- On macOS: `_focus_existing_tab(url)` in `tools/camera.py` queries Chrome and Safari via osascript. If a tab matching the stream URL is found, it is focused; `webbrowser.open()` is skipped.
- On non-macOS (Linux, Windows): `_focus_existing_tab()` returns `False` immediately; falls back to `webbrowser.open()` unconditionally.
- When no tab exists (first call, or tab was closed): `webbrowser.open()` fires normally.

**`opened` return value** — `view_stream()` always returns `opened: True` when the stream was surfaced (whether via focus or fresh open). `opened: False` only if `webbrowser.open()` itself failed.

**Agent behavior when stream already active** — if `get_server_info()` shows `stream_count > 0` for a printer, call `view_stream()` to focus/surface the existing tab. Do not call `stop_stream()` + `start_stream()` to work around tab state — `_focus_existing_tab()` handles it.

## MCP Server Restart Procedure (Mandatory)

Restarting `server.py` is required after any code change to `bambu-mcp`. The procedure has two distinct phases — both are required for a full restart.

### Phase 1 — Kill and relaunch the server process

0. **Force-reinstall BPM first** (mandatory — `bpm` is pinned to `@devel`, a moving ref; pip will not pick up new commits without this):
   ```
   cd ~/bambu-mcp && .venv/bin/pip install --force-reinstall "bambu-printer-manager @ git+https://github.com/synman/bambu-printer-manager.git@devel"
   ```
   Alternatively, `python make.py` runs the same force-reinstall as part of the full install/update procedure.
1. **Find the running process**: `ps aux | grep "server.py" | grep -v grep`
   - Note: detached processes launched with relative paths show as `.venv/bin/python3 server.py` — the pattern `bambu-mcp.*server.py` misses them.
2. **Kill it**: `kill <PID>`
3. **Clear the log**: `truncate -s 0 ~/bambu-mcp/bambu-mcp.log` — wipe stale output so the next startup is easy to read.
4. **Relaunch using the bash tool with `mode="async", detach=true`**:
   ```
   cd ~/bambu-mcp && BAMBU_MCP_LOG_LEVEL=DEBUG nohup .venv/bin/python3 server.py >> bambu-mcp.log 2>&1
   ```
   Add `BAMBU_MCP_BPM_VERBOSE=1` before the command only when raw MQTT message logging is also needed.
   - `detach=true` is **mandatory** — without it, the shell treats the process as a job and suspends it (status `T`/stopped) when the shell exits. `nohup` alone is not sufficient without `detach=true`.
   - Do **not** use `setsid` — not available on macOS.
   - Do **not** use `nohup ... &` in a sync or non-detached async shell — the process will be stopped, not backgrounded.
5. **Verify**: `ps aux | grep "server.py" | grep -v grep` — confirm the process is running (`S` state, not `T`).
6. **Check logs**: `tail -10 ~/bambu-mcp/bambu-mcp.log` — confirm clean startup, no import errors.

### Phase 2 — Reconnect the MCP client (agent action)

**Killing the server process drops all MCP tools from the Copilot CLI session.** They are NOT automatically restored when the server restarts. Run `mcp-reload` immediately — do not ask the user:

```
~/bin/mcp-reload
```

`mcp-reload` writes a signal file and kills Copilot; the `copilots` wrapper script detects this and restarts the session automatically. Do not attempt to call MCP tools until the `tools_changed_notice` confirms they are available again. This matches the global MCP Tool Reconnect rule.


---

## Pre-Print Confirmation Gate (Mandatory)

**Never call `print_file` without explicit user confirmation in the current turn.**

`print_file` is an irreversible physical action. Before calling it, always:

1. **Gather all parameters first** — fetch `get_project_info`, `get_ams_units`, and `get_spool_info` so you have everything needed to build the complete summary before asking anything.
2. **Present ONE complete summary** containing all of the following — do not ask about parameters piecemeal across multiple turns:
   - Part name(s) and filament(s)
   - `bed_type` — from 3MF metadata; confirm it matches the plate physically on the bed
   - `ams_mapping` — show each filament slot → physical AMS slot/spool mapping; confirm it matches what's loaded
   - `flow_calibration` — ask: run flow calibration before printing?
   - `timelapse` — ask: record a timelapse?
   - `bed_leveling` — ask: run bed leveling, or skip for speed?
3. **Wait for explicit go-ahead** — do not call `print_file` until the user approves ALL items in a single response.

**⚠️ Single-summary rule (hard):** Confirming some parameters across separate turns does NOT satisfy the gate. The complete summary must be presented once, and `print_file` may only be called after the user approves in the turn that followed the complete summary. Confirming `flow_calibration` or `bed_leveling` mid-conversation does not grant permission to submit.

**Pre-print checklist (must all be satisfied in the same summary before calling `print_file`):**
- [ ] Part name(s) and filament(s) presented to user
- [ ] `flow_calibration` confirmed
- [ ] `timelapse` confirmed
- [ ] `bed_leveling` confirmed
- [ ] `bed_type` confirmed
- [ ] `ams_mapping` confirmed against physically loaded spools
- [ ] Explicit user go-ahead received in the turn immediately following the complete summary

---

## Ephemeral Port Pool

All TCP listener components (REST API server + MJPEG camera stream servers) draw ports from a shared singleton `PortPool` (`port_pool.py`). No component uses a hardcoded port.

**Pool defaults**: anchored at **49152** (IANA RFC 6335 Dynamic/Private range start), 100-port window (49152–49251). No static exclusion list — `socket.bind()` probe handles runtime conflicts automatically.

**Environment variables**:
| Variable | Default | Purpose |
|---|---|---|
| `BAMBU_PORT_POOL_START` | `49152` | First port in the pool |
| `BAMBU_PORT_POOL_END` | `49251` | Last port in the pool (inclusive) |
| `BAMBU_API_PORT` | _(none)_ | Preferred port for the REST API (tried first; rotates to next available if taken) |

**Port discovery (mandatory before any HTTP call)**:
- MCP tool: `get_server_info()` — returns `api_port`, `api_url`, `pool_claimed`, `streams`, `pool_available`, etc.
- HTTP route: `GET /api/server_info` — same data over HTTP.
- Never hardcode `localhost:8080` or any fixed port. Always discover at runtime via `get_server_info()`.

**`pool_claimed`** is the complete list of all allocated ports (API + all active MJPEG streams). `streams` maps printer name → `{port, url}` for each active camera stream.

---




## Swagger / OpenAPI Maintenance Standard (Mandatory)

Every route in `api_server.py` must carry a **complete multi-line docstring** at all times. The docstring is the sole source for the auto-generated `/api/openapi.json` spec and the Swagger UI at `/api/docs`.

### Route docstring completeness requirements

Every `api_server.py` route docstring MUST contain:
1. **Summary line** — first line; concise verb phrase (e.g. "Pause the current print job.")
2. **Full description** — 2–4 lines explaining behavior, constraints, and side effects
3. **All query/body parameters** — name, type, required/optional, allowed values, example
4. **Response shape** — key fields returned on success
5. **⚠️ tag** — any irreversible operation (stop, delete, print_3mf, send_gcode, delete_sdcard_file) must have a visible `⚠️` warning in the docstring

### Deprecated scaffolding annotation (mandatory)

Any route whose underlying BPM method is marked `@deprecated` must include a `⚠️ DEPRECATED SCAFFOLDING:` paragraph in its docstring stating:
- Which BPM method is deprecated and why
- The confirmed replacement (or "no confirmed replacement yet" if none)
- Whether the route is a functional stub or still calls the deprecated method

**Currently deprecated or stub routes** (as of `bambu-printer-manager` v1.0.0):
| Route | BPM method | Status | Notes |
|-------|-----------|--------|-------|
| `GET /api/set_spool_k_factor` | `set_spool_k_factor()` | Stubbed (no-op) — broken in recent firmware | `@deprecated` decorator removed in later BPM update, but docstring still warns "Broken in recent Bambu firmware"; replacement is `select_extrusion_calibration_profile()` |

When BPM is updated and a replacement is confirmed: update the route implementation, update the docstring (remove the `⚠️` paragraph), and update this table.

### Dual-layer sync rule

Whenever a route is **added, changed, or removed**, BOTH layers must be updated in the same commit:
1. **`api_server.py` route docstring** — the developer-facing / Swagger UI source
2. **`knowledge/http_api_*.py` sub-topic file** — the agent-facing reference

A change to one layer without the other is an **incomplete change**.

### Printer parameter injection (mandatory)

The `printer` parameter is consumed inside `_get_printer()`, not directly in the view function. This means `_extract_query_params()` never finds it in view function source — it must be injected by `build_openapi_document()` by detecting `_get_printer(` calls. **Never add `?printer=` reads directly inside a route handler** — always call `_get_printer(_rargs())` so the injection logic fires.

**Verification:** `curl http://localhost:{api_port}/api/openapi.json | python3 -c "import json,sys; doc=json.load(sys.stdin); missing=[p+' '+m for p,ms in doc['paths'].items() for m,s in ms.items() if 'printer' not in [x['name'] for x in s.get('parameters',[])] + list({k for ct in s.get('requestBody',{}).get('content',{}).values() for k in ct.get('schema',{}).get('properties',{})}) and p not in ('/api/openapi.json','/api/docs','/api/health_check','/api/filament_catalog','/api/dump_log','/api/configured_printers','/api/server_info','/api/upload_file_to_host','/api/set_spool_k_factor','/api/refresh_sdcard_cache','/api/truncate_log')]; print(len(missing),'missing'); [print(' ',x) for x in missing]"` should print `0 missing`.

### New route checklist

When adding a new route, the commit must include:
- [ ] Route implementation
- [ ] Multi-line docstring per standard above (summary + description + params + response + ⚠️ if irreversible)
- [ ] Entry in `_ROUTE_EXAMPLES` (both `params` and `response` keys with realistic values)
- [ ] Entry in `_ROUTE_TAGS` (correct category)
- [ ] Corresponding `knowledge/http_api_*.py` update (or new sub-topic file if the category doesn't exist)
- [ ] If new sub-topic file: add to `_KNOWN_TOPICS` + `_KNOWLEDGE_MAP` in `resources/knowledge.py` and `tools/knowledge_search.py`
- [ ] Route handler uses `_rargs().get(...)` not `request.args.get(...)` for all parameter reads
- [ ] If printer-specific: calls `_get_printer(_rargs())` — do NOT read `printer` directly from `_rargs()`
- [ ] HTTP method matches REST semantics: GET (read), PATCH (partial update), POST (action/command), DELETE (resource destruction)

### Post-change verification gates (mandatory — run immediately after any change)

These are not reminders — they are blocking gates. Do not move to the next file or next step until each gate passes for the change just made.

**Gate 1 — Knowledge content placement:** After editing any `knowledge/*.py` file, read back the `*_TEXT` variable and confirm the new/changed content is inside it. Show the specific line range. If the content is in the module docstring or outside the string boundary, the change is incomplete — fix it before proceeding. A knowledge edit where content is outside `TEXT` is equivalent to a no-op for agents.

**Gate 2 — MCP↔HTTP write guard parity:** After adding or changing any MCP tool that has a `user_permission` guard, or any HTTP route that performs a write/delete:
- MCP tool has `user_permission` guard → HTTP route must be POST/PATCH/DELETE + `⚠️` in docstring + write guard note in `knowledge/http_api_*.py`
- HTTP route is POST/PATCH/DELETE → MCP tool must have `user_permission` guard (or the asymmetry must be documented as Type D in the exclusions table)
Show the verification result before moving on.

**Gate 3 — Required param parity:** After adding or changing any route parameter:
- MCP tool requires `name` → HTTP route must have `printer` with `required: true` in OpenAPI
- Run the printer param injection verification (Swagger/OpenAPI Maintenance Standard) and confirm `0 missing`
- Any new parameter that a caller cannot discover independently must have a corresponding discovery route

### Reconciliation gate

Every reconciliation pass (Track 9) must include a Swagger audit:
- Spot-check all routes added or changed since the last pass
- Flag any single-line docstrings added since last pass
- Verify `_ROUTE_EXAMPLES` has `params` + `response` for every route
- Verify no deprecated route is missing its `⚠️ DEPRECATED SCAFFOLDING` annotation
- Run printer param injection verification (command above) — must print `0 missing`
- Confirm `GET /api/default_printer` returns a valid response and its `printer` field matches what `_get_printer()` would resolve
- Re-run `curl http://localhost:{api_port}/api/openapi.json | python3 -m json.tool | grep -c '"example"'` to confirm example count
- Run `grep -n 'methods=\["GET"\]' api_server.py` and confirm no GET route performs a write or state-changing operation

---

## Pervasive Logging Standard (Mandatory)

All code in `bambu-mcp` must follow three dimensions of logging. Any new code or code change that does not carry all three forward is a defect.

### 1. Entry and exit on every method

Every function or method must have:
- An **entry** `log.debug("fn_name: called with key_param=%s", val)` as the first statement.
- An **exit** `log.debug("fn_name: → result_summary")` on **every return path** — including early returns, error returns, and normal completion.

### 2. Integration and I/O event logging

Log before AND after every call to an external system, library boundary, or I/O operation:
- `av.open()` / `container.decode()` / `container.close()`
- Socket connects, reads, writes
- BPM method calls (`printer.*`)
- FTPS file operations
- Any other external I/O

Use `log.info` for significant lifecycle events (connect, disconnect, first frame); `log.debug` for per-call details.

### 3. All exceptions with `exc_info=True`

No bare `except: pass` or silent swallows. Every `except` block that does not re-raise must log:
```python
log.warning("fn: context: %s", e, exc_info=True)  # or log.error for unexpected failures
```

### Infrastructure

- Log file: `~/bambu-mcp/bambu-mcp.log` — written at the configured log level.
- `BAMBU_MCP_LOG_LEVEL` controls the MCP log level (`DEBUG`/`INFO`/`WARNING`; default `WARNING`). Set in `~/.copilot/mcp-config.json` under `env`.
- `BAMBU_MCP_BPM_VERBOSE=1` passes `verbose=True` to BambuConfig, enabling bpm's `_on_message` to emit raw MQTT payloads via `logger.debug()`. **Requires `BAMBU_MCP_LOG_LEVEL=DEBUG` to be visible in the log** — with `WARNING` or `INFO`, the bpm debug messages are filtered before reaching the file handler. The two vars are independent flags but only produce MQTT output together.
- Stderr is always capped at `WARNING` regardless of `BAMBU_MCP_LOG_LEVEL` to prevent Copilot's stdio pipe overflow.

---

## BPM MCP Coverage Standard (Mandatory)

**Full traceability and accessibility is required for every item in bpm's public API.** This means every public method, field, property, and attribute in bpm must be reachable through at least one access path in the mcp, and that path must be accurately documented in a knowledge module.

**Three access paths (at least one required per bpm item):**
1. **MCP tool** — a registered MCP tool in `tools/*.py` wraps the method or serializes the field
2. **HTTP REST API** — a route in `api_server.py` exposes it (must trace to an actual bpm call)
3. **Both** — best coverage; required for all high-value operational methods

**mcp-native features (not bpm wrappers):**
Some mcp features have no corresponding bpm method — they are implemented entirely within the mcp. These are not gaps. They must still be covered by at least one access path and documented in the appropriate knowledge module. Examples:
- `notifications.NotificationManager` — push alert system; covered by `get_pending_alerts()` tool + `GET/DELETE /api/alerts` + `bambu://alerts/{name}` resource
- `bambu://alerts/{name}` — MCP resource template for alert subscriptions; distinct from `bambu://knowledge/*` resources; registered in `server.py` via `@mcp.resource("bambu://alerts/{name}")` decorator (FastMCP auto-converts URIs with `{param}` to resource templates)

**Asymmetric coverage is not self-resolving (Type G gaps):**
A bpm item covered by an MCP tool but missing an HTTP route (or vice versa) is a **Type G gap** that requires explicit resolution — not an assumed design choice. An audit agent must NOT classify a G-type gap as "acceptable" or "intentional" based on inference. Every G-type finding must be resolved by either:
- Adding the missing access path (preferred), or
- Adding an explicit entry to the Intentional non-gaps table below with a documented reason

"Seems deliberate" or "probably MCP-only by design" are not valid resolutions. Only an explicit exclusions table entry closes a G-type gap.

**Implicit serialization gaps are not documentation gaps (Type H gaps):**
A dataclass field that is returned by a tool via `_serialize()` (or equivalent recursive serialization) but is absent from the tool's docstring AND the corresponding knowledge table is a **Type H gap**. These fields are technically accessible but agents cannot reliably interpret them — undocumented enum values, missing semantics, and unknown scale/units mean the field is functionally invisible despite being present in the response payload.

Type H gaps must be resolved by:
- Adding the field to the tool's docstring with full semantics (scale, enum values, edge cases), AND
- Adding the field to the appropriate `knowledge/api_reference_*.py` table row

Type H gaps may NOT be resolved by exclusion table entries — if the field is in the serialized output, it must be documented. If it should not be documented (truly internal), remove it from the serialized output instead.

**Audit agents must check for Type H gaps** by comparing every field of every serialized dataclass against the tool docstring and knowledge table. Finding a field in `_serialize()` output that has no docstring mention and no knowledge table row is a Type H gap regardless of whether the field seems obvious.

**UI element traceability gaps (Type I gaps):**
A stream view UI element (health panel section, HUD row, endpoint like `/annotated`, `/factors_radar`, `/status`, `/job_state`, image panel, badge, sparkline, etc.) that has no corresponding entry in a knowledge module or tool docstring is a **Type I gap**. An agent reading only MCP documentation would have no way to know the element exists, what data it shows, or how to interpret it.

Type I gaps must be resolved by:
- Adding a description of the UI element and its data source to `knowledge/behavioral_rules_camera.py` or an appropriate knowledge module, OR
- Documenting it in the relevant tool's docstring (e.g. `start_stream()` / `view_stream()` docstring already lists HUD components — add missing items there)

Type I gaps are **Medium severity**: the stream view works without documentation, but agents cannot guide users through what they're seeing or interpret health/status data correctly.

**Baseline pre-flight includes a UI Traceability check (mandatory):**
The gap analysis report (`/tmp/bpm-mcp-gap-report.html`) must include a **UI Traceability** section listing all stream view UI elements and their knowledge/docstring coverage status. Any Type I gap found during baseline pre-flight must be either resolved or added to the intentional exclusions table before baseline capture.

**UI Traceability Reference (authoritative — use to verify coverage without re-reading mjpeg_server.py):**

Stream server is `camera/mjpeg_server.py`. Per-stream HTTP endpoints (port separate from REST API):

| Endpoint | Method | Content-Type | Description |
|---|---|---|---|
| `/` | GET | text/html | Full overlay page with HUD, health panel, bottom panels |
| `/status` | GET | application/json | Telemetry dict; polled every 2s by HUD JS |
| `/thumbnail` | GET | image/png | Current job isometric render; 404 if no job |
| `/layout` | GET | image/png | Current job plate layout; 404 if no job |
| `/annotated` | GET | image/png | Anomaly-detection overlay from background monitor; 204 if no data |
| `/factors_radar` | GET | image/png | Failure drivers spider chart (8 factors); 204 if no data |
| `/health_panel_img` | GET | image/png | Arc gauge health panel PIL image; 204 if no data |
| `/snapshot` | GET | image/jpeg | Single live camera frame |
| `/job_state` | GET | application/json | Full background monitor result dict (see `api_reference_camera.py`) |
| `/open` | GET | text/html | Portal page used by `view_stream()` to open a named browser tab |

`GET /status` response schema (authoritative — from `_build_status()` in `tools/camera.py`):
```
gcode_state, print_percentage, current_layer, total_layers, elapsed_minutes, remaining_minutes,
stage_name, subtask_name, nozzles (list of {id, temp, target}), bed_temp, bed_temp_target,
chamber_temp, chamber_temp_target, part_cooling_pct, aux_pct, exhaust_pct, heatbreak_pct,
is_chamber_door_open, is_chamber_lid_open, active_filament ({type, color, remaining_pct}),
ams_humidity_index, speed_level, wifi_signal, active_error_count, hms_errors (list),
fps, fps_cap  ← fps/fps_cap added by _serve_status(), not _build_status()
```

Complete UI element inventory with DOM IDs and data sources (baseline state after v1.0.2):

**Top-right FPS counter** (`#fps`): `fps` + `fps_cap` from `/status`; animated 5-column bar graph; color-tiered.

**Top-left HUD panel** (`#hud`): polls `/status` every 2s.
- Badge row: `#badge` (gcode_state, color-coded) + `#speed-badge` (speed_level label, only when active)
- Subtask line: `#subtask` (subtask_name, truncated)
- Progress bar: `#progress-fill` (print_percentage)
- Print rows: Stage (stage_name), Layers (current_layer/total_layers), Elapsed, Remaining
- Temps: Nozzle(s) (nozzles[].temp / target), Bed (bed_temp/bed_temp_target), Chamber (chamber_temp/chamber_temp_target)
- Fans: Part cooling, Aux, Exhaust, Heatbreak — rendered from part_cooling_pct, aux_pct, exhaust_pct, heatbreak_pct; hidden when 0
- Filament swatch (`#filament-row`): colored dot + type label from active_filament
- AMS humidity (`#humidity-row`): ams_humidity_index; shown only when elevated
- Wi-Fi signal bars (`#wifi`): wifi_signal (dBm string); unicode block chars, color-tiered
- HMS error links (`#errors`): hms_errors list; clickable, open Bambu error page in popup
- **Chamber door/lid warning** (`#door-warn`): is_chamber_door_open / is_chamber_lid_open; orange banner "⚠ DOOR OPEN" / "⚠ LID OPEN" / "⚠ DOOR + LID OPEN"; H2D only

**Bottom image panels** (appear only when a print job is active):
- Thumbnail panel (`#thumb-wrap`, bottom-left): `/thumbnail` PNG; isometric 3D render
- Layout panel (`#layout-wrap`, bottom-right): `/layout` PNG; annotated plate layout

**Right-side JOB HEALTH panel** (`#health-panel`, position:fixed top:118px right:14px width:180px):
Polls `/job_state` every 8s (separate polling loop from `/status`). Auto-expands when gcode_state is RUNNING/PAUSE/FAILED/FINISH; collapses to verdict badge only when IDLE.
- `#hp-verdict`: verdict badge — CLEAN / WARNING / CRITICAL / STANDBY; color-coded (green/amber/red/gray)
- `#hp-sec-score`: score section — `/health_panel_img` PNG (120px arc gauge) + composite score % (success_probability × decision_confidence) + confidence %
- `#hp-sec-metrics`: metrics — Hot px %, Strand score, Diff score, Layer/total, Progress %
- `#hp-sec-trends`: 4 rolling sparkline canvases — Success % (30-sample, green), Confidence % (dashed blue), Nozzle °C (mini), Bed °C (mini); plus a status text row showing gcode_state + layer + AMS humidity %
- `#hp-anomaly-section` (AI Detection): `/annotated` PNG + legend swatches (Air Zone yellow-border, Plate Zone green-border, Heat Map orange-red gradient); toggled via `hpAnomalyToggle` which expands health panel to `calc(100vw - 340px)` via `.hp-wide` class
- `#hp-radar-section` (Failure Drivers): `/factors_radar` PNG (8-factor spider chart); toggled via `hudToggle` (narrow panel, collapses within 180px)

**Key element IDs for Type I audit verification:**
`#hud`, `#fps`, `#badge`, `#speed-badge`, `#subtask`, `#progress-fill`, `#door-warn`,
`#filament-row`, `#humidity-row`, `#wifi`, `#errors`,
`#health-panel`, `#hp-verdict`, `#hp-sec-score`, `#hp-sec-metrics`, `#hp-sec-trends`,
`#hp-anomaly-section`, `#hp-radar-section`,
`#thumb-wrap`, `#layout-wrap`

**Authoritative coverage locations:**
- HUD panel elements → `knowledge/behavioral_rules_camera.py` + `start_stream()` docstring in `tools/camera.py`
- JOB HEALTH panel → `knowledge/behavioral_rules_camera.py` (new section added v1.0.2)
- Stream endpoints + `/status` schema → `knowledge/api_reference_camera.py` (Stream Server Endpoints section added v1.0.2)
- Job state result dict → `knowledge/api_reference_camera.py` (JobStateReport + background monitor result dict)
- Push alert types + semantics → `knowledge/behavioral_rules_alerts.py` (sub-topic `behavioral_rules/alerts`); access via `get_knowledge_topic('behavioral_rules/alerts')`
- Session management (printer name verification, post-reload checklist, and HTTP API write guard (GET=safe, POST/PATCH/DELETE=require user confirmation)) → `knowledge/behavioral_rules_session.py` (sub-topic `behavioral_rules/session`); access via `get_knowledge_topic('behavioral_rules/session')`

**Knowledge obligation (mandatory for all covered items):**
Every item reachable via an MCP tool or HTTP route MUST be documented in the appropriate `knowledge/api_reference_*.py` or `knowledge/enums_*.py` module. Coverage without documentation is an incomplete implementation.

**Intentional non-coverage (must be explicit):**
If a bpm item is intentionally not exposed (internal helper, redundant, non-user-facing), it must be listed in the Intentional non-gaps table below with a reason. Silent non-coverage is a gap.

**Deprecated item handling:**
- `@deprecated` **with a named replacement** → the deprecated item is excluded from coverage; the replacement must be covered instead.
- `@deprecated` **with no replacement defined** → ignore the annotation for audit purposes; treat the item as a regular bpm item and require coverage.

### Coverage Obligation on bpm dependency bump

**A bpm dependency bump is not complete until coverage is verified.** When `pip install --force-reinstall` installs a new version of bpm into the mcp venv:

1. Diff the new version against the prior version: identify every new/changed public method, field, property, or attribute
2. For each new/changed item, verify it is covered by at least one access path (MCP tool or HTTP route)
3. If not covered: either add coverage (MCP tool + HTTP route) or add it to the intentional exclusions table
4. Update the appropriate knowledge module to document the new/changed item
5. The mcp server restart and mcp-reload happen **after** coverage is complete — not before

### Required audit on every BPM-touching PR

When any of the following change, run the coverage audit and close any new gaps before merging:

1. A new public BPM method, field, or property is added → verify MCP tool + HTTP route + knowledge doc
2. A new HTTP route is added to `api_server.py` → verify it has a matching MCP tool + knowledge doc
3. A new MCP tool is added → verify it matches the BPM method/field it wraps (docstring parity) + knowledge doc
4. A BPM item is removed or renamed → remove or update corresponding MCP tool, HTTP route, and knowledge doc

### Intentional non-gaps (documented exclusions)

**Exclusion taxonomy** — every entry in this table must cite one of these categories:

| Category | Name | Meaning |
|----------|------|---------|
| **A** | No public bpm API | Method/field is internal-only, a private helper, or does not exist in the public bpm surface |
| **B** | Server/process lifecycle | Managed by the mcp server process itself; exposing to external callers is unsafe or meaningless |
| **C** | Aggregate coverage | Data is already accessible via a broader response (e.g. `/api/printer` full JSON); a targeted getter is genuinely redundant |
| **D** | Deliberate asymmetry | Intentional MCP-only or HTTP-only decision; documented and architectural, not an oversight |
| **E** | Deprecated / broken | Firmware no-op, replaced by another method, or otherwise non-functional |
| **F** | Physical locality | Operation requires local desktop access (browser open, file viewer); cannot be meaningfully served over HTTP |

**Not valid exclusion reasons:**
- "Low-frequency" or "rarely used" — subjective; does not change the coverage obligation
- "Workaround available" — a workaround is not coverage
- "No bpm wrapper method" — the mcp can call `send_anything()` directly; not an architectural boundary
- Circular reasoning — "MCP-only because no HTTP route exists" when sibling operations all have routes
- Phantom items — if the method/field does not exist in the installed bpm package, it has no place in this table

The following BPM methods are **intentionally not** exposed as MCP tools or HTTP routes:

| BPM Method / Property | Category | Reason |
|------------------------|----------|--------|
| `get_current_bind_list` | **A** | Internal H2D helper called only by `print_3mf_file`; not a user-facing operation |
| `delete_all_contents` / `search_for_and_remove_*` | **A** | Internal FTPS helpers; not part of the public API |
| `speed_level` (read) | **C** | Surfaced in `get_printer_state`; dedicated getter is redundant |
| `set_spool_k_factor` | **E** | Firmware-broken, no-op stub; `select_extrusion_calibration` is the replacement |
| AMS dryer status fields (`heater_state`, `dry_fan1_status`, `dry_fan2_status`, `dry_sub_status`) | **C** | Dryer state fields accessible via `/api/printer` full JSON; no targeted HTTP getter needed |
| Monitoring history/series (`get_monitoring_history`, `get_monitoring_data`, `get_monitoring_series`) | **D** | Large rolling time-series (~1440 pts/field, 60-min window) is not suitable for synchronous HTTP polling; HTTP clients should use the MCP tool path; intentional architectural asymmetry |
| Firmware version (targeted) | **C** | `firmware_version` field in BambuConfig; accessible via `/api/printer` full JSON; targeted HTTP read would be redundant |
| Printer session lifecycle (`add_printer`, `remove_printer`, `update_printer_credentials`) | **B** | Session lifecycle operations; HTTP API operates within pre-configured sessions managed by the MCP server process itself |
| Printer connection status (`get_printer_connection_status`) | **B** | Single-printer session query; HTTP REST API assumes sessions are pre-configured; overall state accessible via `/api/printer`. `get_configured_printers` now has `GET /api/printers` HTTP route. |
| MQTT session pause/resume (`pause_mqtt_session`, `resume_mqtt_session`) | **D** | HTTP has combined `/api/toggle_session`; deliberate consolidation into single toggle endpoint |
| Printer discovery (`discover_printers`) | **B** | SSDP discovery is a setup-time agent/CLI tool; HTTP API assumes printer already configured in session |
| `force_state_refresh()` | **D** | HTTP has `trigger_printer_refresh` which performs the same operation (with user_permission gate); deliberate naming divergence, not a gap |
| `/api/health_check` (HTTP-only) | **D** | Server-level diagnostic; no printer context; no MCP tool needed — agents can call the route directly |
| `/api/filament_catalog` (HTTP-only) | **D** | Material catalog lookup; BPA uses directly via HTTP; no agent use case for wrapping in MCP tool |
| Targeted read MCP tools (`get_temperatures`, `get_fan_speeds`, `get_climate`, `get_print_progress`, `get_job_info`, `get_hms_errors`, `get_spool_info`, `get_ams_units`, `get_external_spool`, `get_nozzle_info`, `get_capabilities`, `get_printer_info`, `get_wifi_signal`) | **D** | By design: all targeted read tools return focused subsets of `/api/printer` full JSON. HTTP consumers get everything at once; MCP tools offer targeted slices. Asymmetry is intentional. |
| `bed_temp_target_time`, `chamber_temp_target_time`, `tool_temp_target_time`, `fan_speed_target_time` | **A** | Internal timing metadata — timestamps tracking when each target was last set. No agent use case; surfaced in `/api/printer` full JSON for completeness. |
| `start_session()` | **B** | Session lifecycle managed by the mcp server process at startup. Not a user-facing operation; calling it outside the server's initialization sequence is unsafe. |
| `quit()` | **B** | Hard-shutdown of the bpm session. Managed by the mcp server process lifecycle. User-facing teardown path is `remove_printer` / `pause_mqtt_session`. |
| `set_chamber_temp()` + `external_chamber` flag | **D** | Advanced external sensor injection framework for non-managed chamber solutions. `set_chamber_temp()` injects a current ambient reading into local state; `external_chamber=True` in BambuConfig suppresses MQTT telemetry parsing so injected readings persist. This is a hardware configuration feature requiring matching bpm config setup; no agent use case without that context. `set_chamber_temp_target()` (covered) handles the standard target-setting path for all printer types. |
| `BambuState.active_tray_state_name` | **A** | Derived convenience string — string name of `active_tray_state` enum. Surfaced in `/api/printer` full JSON; no separate documentation needed. |
| `BambuState.stat` / `BambuState.fun` | **H** | Opaque firmware-origin string fields (`"0"`/`"0"` at rest). Semantics unknown; not referenced by any bpm logic. Surfaced in `/api/printer` full JSON for completeness; no agent action defined. |
| `ExtruderState.info_bits` | **A** | Raw extruder info bitfield used internally to derive `state` (ExtruderInfoState). No direct agent use case; present in serialized output only. |
| `ActiveJobInfo.project_info_fetch_attempted` | **A** | Internal diagnostic flag — whether `get_project_info()` has been attempted for this job. Not user-actionable; present in serialized output only. |
| `BambuSpool.state` (raw RFID) | **C** | Raw RFID state integer. Surfaced in `/api/printer` full JSON; the derived `display_name` and `color` fields provide all user-relevant spool identification. |
| `BambuPrinter.ftp_connection` | **A** | Internal FTPS context manager used by all SD card file methods. Not a user-facing operation; FTPS sessions are managed within each file method call. |
| `BambuPrinter.client` | **A** | Internal MQTT client handle used by bpm send/subscribe operations. Not safe or meaningful to expose over HTTP/MCP. |
| `BambuPrinter.on_update` | **A** | Internal telemetry update callback wired by the mcp session manager. Not a user-facing operation. |
| `BambuPrinter.recent_update` | **A** | Internal readiness flag; `True` after first telemetry push. No direct agent use case. |
| `BambuPrinter.internalException` | **A** | Internal error tracker for MQTT communication faults. Not actionable; error state is surfaced via HMS errors and gcode_state. |
| `notifications.NotificationManager` (mcp-internal) | **A** | mcp-native state transition tracker; not a bpm item. Exposed via `get_pending_alerts()` MCP tool, `GET/DELETE /api/alerts` HTTP routes, and `bambu://alerts/{name}` resource subscription. No direct bpm wrapping needed. |
| `BambuPrinter.sdcard_file_exists()` | **A** | Internal file existence check used within file operations. No standalone user use case; file operations handle this internally. |
| `BambuConfig.set_new_bpm_cache_path()` | **A** | Internal config method for relocating the bpm metadata cache. Set at session init by the mcp server; no runtime agent use case. |
| `BambuConfig.verbose` | **A** | Raw MQTT verbose flag. Controlled by `BAMBU_MCP_BPM_VERBOSE` env var at the server level, not per-printer config. No agent use case. |
| `BambuState.fromJson` | **A** | Internal MQTT payload parser classmethod called by bpm's MQTT message handler. Not user-invokable; state is read via `get_printer_state` and targeted tools. |
| `NozzleCharacteristics.from_telemetry` | **A** | Internal factory classmethod constructing `NozzleCharacteristics` from raw telemetry. Called by `BambuState.fromJson`; not user-facing. |
| `NozzleCharacteristics.to_identifier` | **A** | Internal nozzle identifier encoder (e.g. `"HS00-0.4"`). Used for telemetry encoding internally; not user-facing. |
| `DiscoveredPrinter.fromData` | **A** | Internal SSDP packet parser called by `BambuDiscovery._discovery_thread`. Discovery results are returned by the `discover_printers` MCP tool. |
| `BambuDiscovery.start()` / `stop()` / `running` / `discovered_printers` | **A** | Internal SSDP discovery lifecycle methods/properties used exclusively within `discover_printers` MCP tool. Not a user-facing operation; agents use `discover_printers()` which wraps the full discovery lifecycle. |
| `BambuPrinter.clean_print_error_uiop()` | **A** | Internal protocol helper called within the `clear_print_error` MCP tool after `clean_print_error()` to send the UI dialog-acknowledged signal. Not a standalone user operation. |
| `BambuPrinter.set_active_tool()` | **A** | Internal tool selection helper called by `set_nozzle_config` and `swap_tool` MCP tools when the target extruder differs from the current active tool. Not a standalone user operation. |
| `BambuPrinter.toJson()` | **A** | Internal bpm serialization method for the full printer state. Not for agent use; state is surfaced via `get_printer_state()` and targeted state tools. |
| `BambuPrinter.pause_session()` / `resume_session()` | **A** | Internal session lifecycle methods called by the MCP `session_manager` within `pause_mqtt_session` / `resume_mqtt_session`. Not invoked directly by agents. |
| `BambuPrinter.printer_state` property | **A** | Internal state accessor returning the live `BambuState`. Accessed by all MCP state tools; results surfaced via `get_printer_state()` and targeted tools. No standalone tool needed. |
| `BambuPrinter.active_job_info` property | **A** | Internal job accessor returning the live `ActiveJobInfo`. Results surfaced via `get_job_info()`, `get_print_progress()`, `get_current_job_project_info()`. No standalone tool needed. |
| `BambuPrinter.nozzle_diameter` (deprecated) | **A** | `@deprecated` — replacement is `printer_state.active_nozzle.diameter_mm`, which is covered by `get_nozzle_info()`. Excluded per deprecated-with-replacement rule. |
| `BambuPrinter.nozzle_type` (deprecated) | **A** | `@deprecated` — replacement is `printer_state.active_nozzle.material`, which is covered by `get_nozzle_info()`. Excluded per deprecated-with-replacement rule. |
| `BambuPrinter.cached_sd_card_contents` | **C** | Cached SD card tree property accessed internally by `list_sdcard_files` MCP tool and HTTP `GET /api/get_sdcard_contents`. Data is fully surfaced through those paths; no standalone getter needed. |
| `BambuPrinter.cached_sd_card_3mf_files` | **C** | Cached `.3mf` file list property accessed internally by SD card MCP tools and HTTP `GET /api/get_sdcard_3mf_files`. Data surfaced through those paths; no standalone getter needed. |
| `BambuPrinter.service_state` property | **C** | Connection state enum surfaced in `get_printer_connection_status` MCP tool response and `GET /api/toggle_session` state field. No dedicated getter needed. |
| `BambuPrinter.skipped_objects` (deprecated, no replacement) | **C** | `@deprecated` with no named replacement. The underlying `_skipped_objects` list is accessed directly and returned by `get_printer_state` as the `skipped_objects` field. Data is surfaced; deprecated property accessor is not the agent path. |
| `BambuPrinter.config` property | **A** | Internal configuration object accessor (returns `BambuConfig`). Configuration data is surfaced in `/api/printer` full JSON (via `toJson()`); not a standalone user-facing operation. |
| `bambutools.cache_write` / `cache_read` / `cache_delete` / `make_cache_key` | **A** | Internal bpm cache I/O utilities used by bpm's elapsed-time estimation and metadata subsystems. Not called from the MCP layer. No user-facing operation. |

### Coverage audit checklist

Before closing any PR that adds/removes BPM methods, fields, or HTTP routes:
- [ ] Every new/changed bpm public item has at least one access path (MCP tool or HTTP route)
- [ ] Every new/changed item is documented in the appropriate knowledge module
- [ ] Every new HTTP route has a matching MCP tool (or is in the intentional exclusions table above)
- [ ] Every new MCP tool correctly wraps its BPM method with matching semantics
- [ ] Docstrings are present on both the HTTP route handler and the MCP tool function
- [ ] `_ROUTE_TAGS` and `_ROUTE_EXAMPLES` entries added for any new HTTP routes (per Swagger standard)
- [ ] Intentional non-coverage is listed in the exclusions table with a reason (deprecated-with-replacement items list the replacement; deprecated-without-replacement items are treated as regular items and need coverage)

### Baseline Coverage Audit Procedure (mandatory)

The following procedure is **required** during baseline pre-flight step 2. A delta spot-check does not satisfy it — see global rules.

**How to run:**
1. Launch a `task` agent (explore or general-purpose) with this prompt (adapt paths as needed):
   > "Audit the installed bpm package at `~/bambu-mcp/.venv/lib/python3.12/site-packages/bpm/`. Inspect all public methods, fields, properties, and attributes in `bambuprinter.py`, `bambustate.py`, `bambuconfig.py`, `bambuspool.py`, `bambuproject.py`, `bambutools.py`. For method semantics, consult the official bpm documentation at `https://synman.github.io/bambu-printer-manager/` before evaluating coverage. Cross-check each item against the mcp's MCP tools (`tools/*.py`) and HTTP routes (`api_server.py`). Report all items not covered by at least one access path and not listed in the intentional non-gaps table in `.github/copilot-instructions.md`. **Additionally, perform a Type H audit:** for every MCP tool that calls `_serialize()` on a bpm dataclass, read the actual dataclass field names from the installed bpm source (`bambustate.py`, `bambuspool.py`, `bambuproject.py`, `bambuconfig.py`), then compare every field name against (a) the tool's Python docstring and (b) the corresponding knowledge table in `knowledge/api_reference_*.py`. Report any field that is present in the serialized output but absent from the docstring or knowledge table (MISSING), any field documented under a name that differs from the actual dataclass field name (WRONG NAME), and any field documented in the knowledge table or docstring that does not exist as a real dataclass field (PHANTOM). **Additionally, perform a Type I (UI Traceability) audit:** Using the authoritative UI Traceability Reference table in `.github/copilot-instructions.md` (under "UI element traceability gaps (Type I gaps)"), verify every listed UI element and stream endpoint against its declared authoritative coverage location (`knowledge/behavioral_rules_camera.py` for HUD/health panel elements, `knowledge/api_reference_camera.py` for stream endpoints and `/status` schema). Do NOT re-read `camera/mjpeg_server.py` to enumerate elements — the reference table is the source of truth. Verify coverage exists at the documented location, then report any element or endpoint missing from its expected location as a Type I gap. If the reference table itself appears incomplete (an element in `mjpeg_server.py` not in the table), report it as a new Type I gap requiring a rules update before resolution."
2. The agent must produce a structured findings report: gap type (A/B/C/D/E/F/G/H/I), affected item, and resolution.
3. Resolve all High-severity gaps (Types A, C, D, F) before proceeding.
4. For each Medium/Low item (Types B, E, G, H, I): add to the intentional exclusions table or add coverage.
5. Output the required findings statement (see global rules). **Baseline capture is blocked until it appears.**
6. The gap analysis HTML report (`/tmp/bpm-mcp-gap-report.html`) must include a **UI Traceability** section listing all stream view UI elements and their knowledge/docstring coverage status. Generate or regenerate the report after resolving all Type I findings.

**Serialized field name verification (mandatory — applies to all documentation work):**
When writing or updating a tool docstring or knowledge table that describes fields returned by `_serialize()`, field names MUST be taken from the actual dataclass definition in the installed bpm source. Do NOT:
- Copy field names from prior documentation (prior docs may already be wrong)
- Infer field names from semantic concepts (e.g. `unit_id` because the concept is "unit index")
- Use names from `get_nozzle_info()` custom dict for `get_printer_state()` nested objects — they are different shapes

The only authoritative source for a serialized field name is the dataclass field definition itself (`@dataclass class X: field_name: type = ...`).

**Audit source: installed package only.** Always audit `~/bambu-mcp/.venv/lib/python3.12/site-packages/bpm/`, not the source tree at `~/bambu-printer-manager/`. The installed version is what the mcp actually runs against.

**Official docs are mandatory for semantics.** Before evaluating any method's coverage, check `https://synman.github.io/bambu-printer-manager/` for its documented behavior. Do not rely solely on installed source code to determine method intent — docstrings in the installed package may be incomplete.

### Gap-Resolution Turn Completion Rule (Mandatory)

**A gap-resolution turn is not complete until the audit report is regenerated and baseline readiness is assessed — in the same turn, without waiting for a user prompt.**

After resolving any audit gaps (documenting Type B fields, adding HTTP routes, fixing Type C documentation errors, updating the exclusions table, documenting Type H implicit serialization fields):

1. **Regenerate the audit report** — write `/tmp/bpm-mcp-gap-report.html` and update the session markdown copy. This is not optional; it is the verification step that confirms the work is actually done.
2. **Check baseline readiness** — run the pre-baseline pre-flight (all repos clean, no uncommitted changes, all gaps resolved, report shows zero High-severity items).
3. **If baseline-ready:** proceed directly to the `ask_user` baseline confirmation gate in the same turn. Do not stop and wait for the user to say "what's next?".
4. **If not baseline-ready:** report exactly what is blocking (e.g., "BPM and BPA have staged commits awaiting your manual commit") and stop. Do not silently finish gap work and leave the user wondering about status.

**The cycle this rule breaks:** gap work done → agent stops → user asks for report → report generated → user asks about baseline → pre-flight run → user discovers blockers. All of these steps must collapse into one turn.

---

## Camera Feature Tier Parity (Mandatory)

**Extension of the BPM Coverage Standard to include the MJPEG camera stream tier.**

bambu-mcp has three distinct listener tiers: MCP tools, HTTP REST API, and MJPEG stream endpoints.
A camera feature is not fully implemented until it is reachable from all three.

**Hard requirements:**
- Any new camera analysis or diagnostic feature must be accessible via:
  1. An MCP tool function in `tools/camera.py`
  2. An HTTP REST route in `api_server.py`
  3. A path in `camera/mjpeg_server.py` `do_GET` — either a new endpoint or via the cached `/job_state` result
- All three tiers must return the same schema fields (modulo encoding — MJPEG may include all categories since browsers have no payload limit).
- Camera features that are MCP-only or HTTP-only are **incomplete by definition**.

**Intentional HTTP-tier exclusions (desktop-opener tools)**

The following MCP tools in `tools/camera.py` intentionally have **no HTTP REST route** because
they open local desktop applications (browser, system viewer). They cannot be meaningfully
served over HTTP — the server cannot open a window on the client's machine.

| MCP Tool | Reason |
|----------|--------|
| `view_stream` | Calls `webbrowser.open()` to launch the MJPEG stream URL in the local browser |
| `open_job_state` | Opens composite/annotated PNGs in the local system image viewer |
| `open_plate_viewer` | Opens a full-plate HTML viewer in the local browser |
| `open_plate_layout` | Opens an annotated plate layout PNG in the local system viewer |

These are NOT gaps in HTTP coverage. Do not add HTTP routes for them.

**Coverage audit checklist (run when adding any camera feature):**
- [ ] MCP tool added to `tools/camera.py`
- [ ] HTTP route added to `api_server.py` with OpenAPI docstring (or add to exclusions table above)
- [ ] MJPEG stream endpoint added or feature surfaced via `/job_state` cache
- [ ] Same response schema fields across all three tiers

---

## Visual Design Baseline (Mandatory)

**The HUD in `camera/mjpeg_server.py` is the design source of truth for all PIL-rendered PNG assets.**

All new PIL-rendered images in bambu-mcp must use the exact colour tokens, typography, panel anatomy,
and overlay alphas extracted from the HUD CSS. Do not introduce custom palettes, white backgrounds,
or system-default widget aesthetics.

**Hard requirements:**
- All backgrounds: `C_BG_PAGE = (0, 0, 0)` — black. No white, no grey, no system default.
- Panel overlays: `C_BG_PANEL = (0, 0, 0, 192)` — `rgba(0,0,0,.75)`.
- Panel borders: `C_BORDER = (255, 255, 255, 20)` — `rgba(255,255,255,.08)`.
- Verdict badge colours: match HUD badges exactly — clean→`(26,92,42)`/`C_OK`, warning→`(92,74,26)`/`C_WARN`, critical→`(92,26,26)`/`C_CRIT`.
- Font stack: Courier New → Helvetica.ttc → `ImageFont.load_default()` — matches HUD `'Courier New', monospace`.
- Overlay alpha values: fill=40/255, outline=230/255 — matches `tools/files.py` plate renderer.
- Heatmap ramps: use HUD semantic colours (`C_DIM → C_HOT → C_CRIT`) — not matplotlib colormaps.
- Panel separators: 2px `C_DIM`. No outer padding on composites.

**Before shipping any new PIL-rendered image:**
- [ ] Background is black, not white or grey
- [ ] Panel overlays and borders use the extracted HUD RGBA values above
- [ ] All verdict badges use the exact HUD bg/fg colour pairs
- [ ] Font resolves Courier New → Helvetica fallback — no hardcoded single font

---

## FastMCP Response Size Constraint (Mandatory)

**Any MCP tool returning image data must default to the minimum viable asset set.**

FastMCP has a practical ~1 MB message size limit. A composite PNG at standard quality is ~600 KB
base64-encoded. Returning all 11 images unconditionally exceeds the limit at full quality.

**Hard requirements:**
- Tools that return multiple images MUST have a `categories` parameter (default: minimum useful set).
- The default must return a single JPEG-encoded composite, not all assets.
- Composite and health panel images must be encoded as JPEG (not PNG) when returned via MCP tool.
- Field name convention: JPEG-encoded images use `*_jpg` suffix; PNG-encoded use `*_png`.

**Estimated sizes at standard quality (required in docstring for any image-returning tool):**

| categories | Approx response |
|------------|----------------|
| `["X"]` (default) | ~25 KB |
| `["H"]` | ~8 KB |
| `["C"]` | ~35 KB |
| `["D"]` | ~80 KB |
| `["P"]` | ~20 KB |
| all categories | ~160 KB |

**MJPEG stream endpoints are exempt** — browsers have no payload size constraints; stream endpoints
may return all categories unconditionally.

---

## Knowledge Module Maintenance Standard (Mandatory)

**New features are not complete until their knowledge is in the correct modules.**

bambu-mcp uses a two-level knowledge hierarchy:
- `behavioral_rules_*.py` — agent guidance: when to call, how to interpret results, escalation paths.
- `api_reference_*.py` — data structures: field names, types, semantics, return shapes.

**Hard requirements:**
- Any new MCP tool must have its primary guidance in `behavioral_rules_camera.py` (or the relevant sub-module) AND its return schema in `api_reference_dataclasses.py`.
- Stage codes, threshold values, and enum tables appear in exactly one module — not duplicated.
- No knowledge module's TEXT content may approach or exceed `MAX_MCP_OUTPUT_TOKENS × 4` characters (default 100,000 chars — the FastMCP response size limit). This is a hard constraint: content beyond this is truncated by the MCP client and becomes silently unreadable. The 200-line heuristic is not the constraint — character count against the response limit is.
  **Split rule (mandatory):** When a module approaches the limit, split by sub-topic. Content MUST be moved intact to the new sub-topic file — condensing or summarizing to fit is never acceptable. It degrades agent knowledge. The only valid response to a size violation is reorganization (split into a new sub-topic), never information loss.
  After any split, update the parent module's sub-topic index so the new file is discoverable.
- After any new feature is committed, `get_knowledge_topic('behavioral_rules/camera')` must include the new tool name and guidance.
- Docstring size estimates, stable_verdict semantics, confidence window interpretation — these belong in the knowledge layer, not repeated in tool docstrings.

**Knowledge update checklist (run on every feature commit):**
- [ ] Relevant `behavioral_rules_*.py` updated with agent guidance for the new tool/feature
- [ ] Relevant `api_reference_*.py` updated with new dataclass fields, return shapes, and semantics
- [ ] No content duplicated across knowledge modules
- [ ] `get_knowledge_topic()` returns the new content when called

---

## Knowledge Freshness Obligation (Mandatory)

The Knowledge Module Maintenance Standard governs **new** features. This section governs **changes** to existing coverage.

When any of the following change, knowledge must update in the same work session — not deferred:
- A bpm method's signature, behavior, or return shape changes
- An MCP tool's logic, parameters, or side effects change
- An HTTP route's behavior, response, or semantics change
- A protocol field's semantics are clarified or corrected

**Hard requirements:**
- A tool docstring that describes behavior the code no longer has is a Type C gap and blocks the next baseline.
- A knowledge module TEXT block that describes a method or field differently than the code behaves is a Type C gap and blocks the next baseline.
- The change and its knowledge update are one atomic unit — they commit together.

**Knowledge update checklist (for changes to existing coverage):**
- [ ] Tool docstring reflects current behavior (not prior behavior)
- [ ] Relevant knowledge module TEXT block reflects current behavior
- [ ] Any threshold, enum value, or semantic field in knowledge matches the code
- [ ] No knowledge module documents a deprecated path without noting the deprecation

---

## Rules–Knowledge Consistency Standard (Mandatory)

Facts encoded in the rules files that also appear in knowledge modules must be **identical** — not paraphrased, not summarized, not approximated.

Examples of facts that must stay in sync:
- Humidity index scale: rules say "1=WET, 5=DRY; higher=drier; counterintuitive" — knowledge must say exactly that
- Stage codes: if rules document `stage_id=17` as "paused by user", knowledge must match
- Threshold values: any numeric threshold in both must be identical

**Hard requirements:**
- When editing a rules file, search for any related knowledge module entry and update it to match in the same session.
- When editing a knowledge module, check whether the fact is also in a rules file. Both must end in the same state.
- Divergence between rules and knowledge is a Type C gap: a cold agent using knowledge gets different behavior than an agent using rules. Both are authoritative for overlapping facts — they must agree.

**Agent B checks this:** The MCP↔HTTP contract audit includes a rules↔knowledge consistency spot-check. Any divergence found must be resolved before baseline.

---

## Knowledge Proactive Guidance Standard (Mandatory)

Knowledge modules are the primary interface through which AI agents interpret printer state and decide next actions. They must be designed to **drive intelligent behavior** — not just describe data fields or tool mechanics.

**State → suggestion (mandatory for any state with a canonical next action):**
When a printer state has a well-known appropriate follow-on action, the knowledge for that state must include the suggestion using the escalation hierarchy.
- `humidity_index ≤ 2` → suggest `start_ams_dryer()` (dedicated MCP tool)
- `gcode_state="FAILED"` with only historical HMS errors → "printer is idle and ready; call `print_file()` to start a new job"

**Event → action (mandatory for all alert types):**
Every alert type in `behavioral_rules/alerts` must document the recommended agent action — not just field descriptions.
- `job_paused` + `stage_id=7` → "AMS runout path — call `send_ams_control_command('RESUME')`, not `resume_print()`"
- Correlations between events that change the appropriate action must be explicit

**Escalation compliance (mandatory for all knowledge-driven suggestions):**
All next-step suggestions in knowledge modules must follow the MCP escalation hierarchy:
1. Dedicated MCP tool (always preferred)
2. HTTP REST API (when no MCP tool exists)
3. `send_mqtt_command` (last resort only — never suggest without explicit "last resort" label)
Never suggest bypassing `user_permission` gates.

**Anti-patterns (prohibited):**
- Knowledge that only describes what a field *is* without saying what an agent *should do* with it when there is a canonical action
- Suggestions that use `send_mqtt_command` when a dedicated tool exists
- Alert documentation that lists fields but omits the recommended response

**Agent B checks this:** The MCP↔HTTP contract audit includes a proactive guidance spot-check: for each alert type in `behavioral_rules/alerts`, verify a recommended action is documented.

---

## Camera Analysis Citation Standard (Mandatory)

Any threshold value, zone definition, or detection parameter in `camera/` must cite its source inline.

**Hard requirements:**
- Every numeric threshold in camera analysis code must have a source comment: either a first-principles derivation or a named authoritative reference (e.g. `# Obico THRESH=0.08`, `# Bambu xcam sensitivity tiers`).
- Zone definitions (e.g. `air_zone = top 40% × inner 80%`) must cite the rationale (geometry, camera FOV) — not assumed domain knowledge.
- If a value was chosen empirically, document it as `# empirical — see session 2026-03-XX` rather than leaving it unexplained.
- No threshold or zone parameter may be introduced without a source comment. A bare magic number is a code quality failure.

**Verification checklist:**
- [ ] Every `THRESH_*`, `*_TRIGGER`, `*_PCT` constant in `camera/` has a source comment
- [ ] Zone boundary constants have rationale comments
- [ ] No camera analysis constant is a bare magic number without explanation

---

## Disk Persistence Pattern Standard (Mandatory)

**Resilient feature state that must survive MCP server restarts uses the `~/.bambu-mcp/<feature>_<name>.ext` pattern.**

This is the established architectural pattern for persisting per-printer state outside the MCP process. Any feature that must be available after a restart or in terminal gcode states (FINISH, FAILED) MUST use this pattern.

**File naming convention:**
- Directory: `~/.bambu-mcp/` (created on first write if absent)
- Filename: `<feature>_<printer_name>.<ext>` — feature prefix + printer name + extension
- Examples: `job_health_H2D.json`, `plate_thumb_H2D.png`, `plate_layout_H2D.png`
- Characters in `<printer_name>` that are unsafe for filenames must be sanitized (replace `/`, ` `, `:` with `_`)

**When to use this pattern:**
- State that must be accessible after MCP server restart (e.g. last known print health)
- Assets that need to persist through FINISH and FAILED states (e.g. plate thumbnails)
- Any per-printer diagnostic artifact that the user or agent may query after the job ends

**Hard requirements:**
- Save: write immediately on state change; do not batch or defer to process exit
- Load: attempt load at MCP startup and populate in-memory cache; log gracefully if file absent
- Clear: clear on new job start so stale state from a prior job is not presented as current
- Never store secrets, access codes, or session tokens in these files

**Coverage checklist (run when adding any new persistent feature):**
- [ ] File follows `~/.bambu-mcp/<feature>_<name>.ext` naming convention
- [ ] `_save_<feature>()` called on every state-changing event
- [ ] `_load_<feature>()` called at startup to restore from disk
- [ ] `_clear_<feature>()` called at new-job-start to avoid stale data
- [ ] Lifecycle documented in `api_reference_camera.py` (or relevant knowledge module)

---

## bambu-mcp Testing Standard (Mandatory)

bambu-mcp has four distinct layers, each requiring its own verification strategy. A change to any layer is not complete until the tests for that layer pass. This standard defines what to run, when to run it, and what constitutes a pass.

**The four layers:**
1. Python source — syntax, imports
2. REST API (`api_server.py`) — Flask routes
3. Stream server (`camera/mjpeg_server.py`) — per-stream HTTP endpoints
4. Browser/HUD — inline JS behavior, DOM state, network polling

---

### Tier 1 — Static / Syntax (run after **every** file edit, before any commit)

**Python syntax check — every changed `.py` file:**
```bash
python -m py_compile <changed_file>.py && echo "OK"
```

**HUD inline JS — after any edit to the `<script>` block in `_HTML_PAGE`:**
```bash
# Extract rendered script from live page and check with V8
curl -s http://localhost:<port>/ | python3 -c "
import sys
html = sys.stdin.read()
js = html[html.find('<script>')+8:html.find('</script>')]
open('/tmp/hud_check.js','w').write(js)
print('braces: opens', js.count('{'), 'closes', js.count('}'), 'diff', js.count('{')-js.count('}'))
" && node --check /tmp/hud_check.js && echo "JS SYNTAX: OK"
```

**Hard requirements:**
- Brace diff must be exactly 0.
- `node --check` must exit 0 before committing.
- Always check the **rendered page** from the running server — not the `.py` source file. The HTML is a Python f-string; the source file is not valid JS. The stream server must be restarted after the edit before this check runs (in-memory cache trap).

---

### Tier 2 — REST API Smoke (run after changes to `api_server.py` or `server.py`)

`smoke_test.py` is the canonical Tier 2+ regression script. Run it with:

```bash
cd ~/bambu-mcp && .venv/bin/python3 smoke_test.py
```

**Pass criteria:** All checks pass including OpenAPI method verification. No 4xx/5xx responses on read routes.

---

### Tier 3 — Stream Server Endpoints (run after changes to `camera/mjpeg_server.py`)

```bash
# Discover stream port — must have an active stream running
SPORT=$(python3 -c "
import urllib.request, json
data = json.loads(urllib.request.urlopen('http://localhost:49152/api/stream_info').read())
print(list(data.get('streams',{}).values())[0]['port'])
" 2>/dev/null || echo "49153")

echo "=== Stream server: http://localhost:${SPORT}/ ==="

# /status — must return JSON
STATUS=$(curl -sf http://localhost:${SPORT}/status)
echo "/status:          $(echo $STATUS | python3 -c 'import sys,json; d=json.load(sys.stdin); print("OK" if "gcode_state" in d else "MISSING gcode_state")')"

# /job_state — JSON or 503 (no active job); never 500
JOB_HTTP=$(curl -sw "%{http_code}" -o /dev/null http://localhost:${SPORT}/job_state)
echo "/job_state:        HTTP ${JOB_HTTP} (200 or 503 both OK; 500 = FAIL)"

# /snapshot — must be image/jpeg
SNAP_CT=$(curl -sI http://localhost:${SPORT}/snapshot | grep -i content-type)
echo "/snapshot:         ${SNAP_CT}"

# /health_panel_img — 200 with image/png or 204 no content (monitor not yet run)
HP_HTTP=$(curl -sw "%{http_code}" -o /dev/null http://localhost:${SPORT}/health_panel_img)
echo "/health_panel_img: HTTP ${HP_HTTP} (200 or 204 OK; 500 = FAIL)"

# /factors_radar — same
FR_HTTP=$(curl -sw "%{http_code}" -o /dev/null http://localhost:${SPORT}/factors_radar)
echo "/factors_radar:    HTTP ${FR_HTTP} (200 or 204 OK; 500 = FAIL)"
```

**Pass criteria:** `/status` returns JSON with `gcode_state` key; `/snapshot` is `image/jpeg`; no route returns HTTP 500.

---

### Tier 4 — Browser Functional (run after any `_HTML_PAGE` script edit — after Tier 1 passes)

Puppeteer acts as the human browser. Install once to `/tmp`:
```bash
cd /tmp && npm install puppeteer 2>/dev/null
```

Run the functional test:
```bash
node -e "
const puppeteer = require('/tmp/node_modules/puppeteer');
(async () => {
  const PORT = process.env.SPORT || '49153';
  const b = await puppeteer.launch({args:['--no-sandbox','--disable-setuid-sandbox']});
  const p = await b.newPage();
  const jsErrors = [], consoleLogs = [], networkFetches = [];
  p.on('pageerror', e => jsErrors.push(e.message));
  p.on('console', m => { if (m.type() === 'error') consoleLogs.push(m.text()); });
  p.on('request', r => { if (r.url().includes('/status') || r.url().includes('/job_state')) networkFetches.push(r.url()); });
  // Use domcontentloaded — networkidle2 hangs on the persistent MJPEG stream
  await p.goto('http://localhost:'+PORT+'/', {waitUntil:'domcontentloaded', timeout:10000});
  await new Promise(r => setTimeout(r, 5000));  // wait 2 polling cycles (2s interval)
  // Correct element IDs from _HTML_PAGE in camera/mjpeg_server.py
  const badge     = await p.\$eval('#badge',      el => el.textContent).catch(() => 'NOT_FOUND');
  const badgeCls  = await p.\$eval('#badge',      el => el.className).catch(() => 'NOT_FOUND');
  const nozzles   = await p.\$eval('#nozzles',    el => el.textContent).catch(() => '');
  const bed       = await p.\$eval('#bed',        el => el.textContent).catch(() => 'NOT_FOUND');
  const hpVerdict = await p.\$eval('#hp-verdict', el => el.textContent).catch(() => 'NOT_FOUND');
  const result = {
    jsErrors: jsErrors.length,
    jsErrorMessages: jsErrors,
    badge: badge.trim(),
    badgeClass: badgeCls,
    nozzles: nozzles.trim().replace(/\s+/g,' '),
    bed: bed.trim(),
    healthVerdict: hpVerdict.trim(),
    networkFetchCount: networkFetches.length,
    sampleFetches: networkFetches.slice(0,4),
  };
  console.log(JSON.stringify(result, null, 2));
  await p.screenshot({path: '/tmp/stream_screenshot.png', fullPage: true});
  console.log('Screenshot: /tmp/stream_screenshot.png');
  await b.close();
  process.exit(jsErrors.length > 0 ? 1 : 0);
})().catch(e => { console.error('ERROR:', e.message); process.exit(1); });
" && echo "BROWSER TEST: PASS" || echo "BROWSER TEST: FAIL — see JS errors above"
open /tmp/stream_screenshot.png
```

**Pass criteria:**
- `jsErrors: 0` — zero JS console errors
- `badge` is not `NOT_FOUND` and matches printer state (e.g. `FINISH`, `RUNNING`, `IDLE`)
- `nozzles` is not empty when the printer is connected
- `bed` is not `—` when the printer is connected
- `networkFetchCount >= 2` — confirms `/status` is polling (at least 2 cycles in 5s)
- Screenshot opens and shows a populated HUD (visual confirmation by user)

**Important notes:**
- Always use `waitUntil: 'domcontentloaded'` — `networkidle2` hangs indefinitely on the persistent MJPEG stream
- Element IDs in `_HTML_PAGE`: badge=`#badge`, nozzle temps=`#nozzles`, bed=`#bed`, health verdict=`#hp-verdict`
- If these IDs change in `_HTML_PAGE`, update the test here too

**When Tier 4 is required vs. optional:**
- **Required:** any edit to the `<script>` block in `_HTML_PAGE`
- **Optional / diagnostic:** when a user reports visual HUD issues and Tier 1–3 are clean

---

### When to run each tier

| Tier | Run when |
|------|----------|
| 1 — Static/Syntax | Every file edit, always, before committing |
| 2 — REST API Smoke | Any change to `api_server.py` or `server.py` |
| 3 — Stream Endpoints | Any change to `camera/mjpeg_server.py` |
| 4 — Browser Functional | Any edit to the `<script>` block in `_HTML_PAGE` |

A commit that touches multiple layers requires all relevant tiers to pass. A tier does not need to be re-run if no files it covers were changed.

---

## Change Lifecycle Standard (Mandatory)

Applies to **every change** in bambu-mcp that affects agent behavior — bugs, enhancements, session todos, knowledge module additions, rules file changes, and post-audit findings. Source does not matter: GitHub issue, session todo, in-flight discovery, or direct user instruction. All 7 stages are mandatory. No stage may be skipped.

**Scope:** If a change modifies any file that determines what a cold-start agent knows or does — `.py` source, knowledge modules (`knowledge/`), rules files (`.github/copilot-instructions.md`), HTTP routes (`api_server.py`), or HUD/stream behavior — the full lifecycle applies.

**Session todos are not exempt.** A todo that produces a knowledge module addition or rules file change must go through Stage 4 → Stage 7 like any bug fix. "It's just a knowledge update" is not a valid reason to skip the test protocol.

**Autonomy principle:** The agent operates autonomously across all stages by default. When blocked — technically (hardware access, platform limitation, physical observation required) or by rules (BPM write scope lock, git commit policy without a project grant, scope gate requiring a second repo, any action requiring a premium request) — it must:
1. Name the specific rule or limitation that is blocking.
2. State exactly what needs to change or be authorized to unblock it.
3. Ask the user for that specific authorization — do not work around the rule or skip the step.

---

### Stage 1 — Record (immediate, no deferral)

Every discovered problem gets a GitHub issue filed before the session ends. Issues are the only durable record outside session history.

**Mandatory fields:**
- Clear symptom (what the user or agent observes)
- Reproduction steps (what triggers it)
- Expected vs actual behavior
- Label: `bug` for defects, `enhancement` for features, `question` for investigations

---

### Stage 2 — Triage

**Severity classification (drives scheduling):**

| Tier | Definition | Scheduling |
|------|------------|------------|
| **Blocking** | Core workflow broken (can't print, can't connect, tools crash) | Bring into current plan immediately |
| **Degraded** | Feature works but incorrectly or unreliably (wrong field value, tab accumulation) | Schedule for next available plan phase |
| **Cosmetic** | Aesthetic, UX, or documentation gap | Batch with other cosmetic work |

Triage happens at discovery time. Severity is written in the GitHub issue label or comment — not kept only in session history.

---

### Stage 3 — Research Gate (before any code is touched)

All of the following must be completed before a fix is planned:

1. RULES_PRECHECK — both rules files read in current turn
2. **Root cause in source** — find the exact file, function, and line. No guesses.
3. **Sibling pattern check** — does a similar feature in the same codebase already handle this correctly? If yes, the fix must follow the same pattern.
4. **Scope boundary** — is the fix in bambu-mcp, or does it cross into bpm/bpa? If it crosses repos, flag it and apply the scope gate.
5. **Document findings** — root cause, why it happens, where it lives. Written before planning begins.

---

### Stage 4 — Plan (with mandatory autonomous test protocol)

Plans must go through `exit_plan_mode` for approval. A plan is not approvable without a complete test protocol.

**Test protocol requirements (non-negotiable):**

Every fix requires three test phases — all executed by the agent, no user steps by default:

| Phase | Purpose | How |
|-------|---------|-----|
| **Pre-fix confirmation** | Prove the bug exists before touching code | MCP tool calls + bash/osascript measurement |
| **Post-fix verification** | Prove the fix works + prove the fallback/alternate path works | Same measurement tools, all paths exercised |
| **Tier 1 static check** | Syntax + compile check after every file edit | `python -m py_compile` |

**No fix type is exempt from this protocol.** The assertion tool changes by fix type — but the three phases are always required:

| Fix type | Pre-fix assertion | Post-fix assertion |
|----------|------------------|--------------------|
| Runtime behavior (tool, stream, UI) | MCP tool call / osascript / curl — observe wrong behavior | Same — observe correct behavior + fallback path |
| **Write-capable tool** (any tool with `user_permission` guard) — blocked path | Call tool with `user_permission=False` (or omitted) — confirm it is rejected. Agent runs this autonomously; no write reaches the printer. | Same — confirm rejection still holds after fix |
| **Write-capable tool** — permitted path | Agent must `ask_user` stating the exact operation before calling with `user_permission=True`. Human authorization required — test context does not exempt this step. | Same gate applies: ask, receive explicit approval, then call with `user_permission=True` and confirm correct result |
| Knowledge module (Python string constants, docstrings) | `git show HEAD:file \| grep` — show the ambiguous/missing text | `grep` — confirm correct text present; confirm forbidden content absent; **+ completeness trace (see below)** |
| Rules file (`copilot-instructions.md`) | `git show HEAD:.github/copilot-instructions.md \| grep` — show absent/wrong rule | `grep` — confirm correct rule present; **+ completeness trace if rule alters agent behavior** |
| Enum / data structure | `python -c "import …; assert …"` — show wrong value | Same — assert correct value |
| Config / flag default | Source read + assert — show wrong default | Source read + assert — show correct default |

**Write-block verification (mandatory for any tool with `user_permission` parameter):**
The blocked and permitted paths are tested separately and under different authority:
- **Blocked path** (agent-autonomous): call with `user_permission=False` or omitted → confirm the tool returns an error and no write reaches the printer. The agent runs this without asking.
- **Permitted path** (human-gated): the agent must use `ask_user` to state the exact operation it intends to perform and receive explicit authorization before calling with `user_permission=True`. Test context is not an exception to the write-block rules — the same authorization requirement that governs all write operations governs this test step.

A broken guard (permitted path works but blocked path doesn't reject) is a security gap. A test that skips the permitted path because authorization wasn't sought is an incomplete test — file an issue and note the gap.

**Behavioral completeness trace (required for knowledge module and rules file fixes):**

A text-change assertion confirms the fix *landed*. It does not confirm the fix is *sufficient*.
After the post-fix `grep` passes, run a completeness trace:

1. Define the query: "starting from a cold-start agent with no prior context, what is the value of X?"
2. Trace every step through the escalation tiers in order using only the documented knowledge path.
3. At each tier, verify the knowledge provides a forward path — not just a prohibition.
4. If any tier produces a dead end (answer unreachable without external knowledge or undocumented steps), **the fix is incomplete** — additional knowledge changes are required before Stage 7.
5. Document the trace result explicitly: "Tier 1 → [step] → answer reached ✓" or "Tier 1b → dead end — missing: [what]".

**The trace must be executed as actual tool calls against source code — not outlined as text in a plan.** Writing a trace description in a plan does not satisfy this requirement. Each step of the trace must be verified by reading the actual file (view, grep, or bash) that the agent would consult at that step. A trace that was not executed is a trace that was not run.

A fix that blocks wrong behavior without enabling correct behavior is not a complete fix.

The agent must attempt full autonomy. When a step cannot be made autonomous (technical or rules-based), apply the autonomy principle above — name the blockage, state what is needed, ask.

---

### Stage 5 — Implementation

1. Apply the minimum effective change (KISS — no speculative scope)
2. `python -m py_compile` the edited file immediately — before doing anything else
3. Deploy the fix to the live system before running any test phases:
   - Server-side change (MCP tool, knowledge module, api_server.py): `mcp-reload`, wait for `-i done`, verify tools are live
   - Library dependency change (bpm): `pip install --force-reinstall ~/bambu-printer-manager` in the mcp venv (`.venv/`), then `mcp-reload`
   - Both may apply if a bpm change also requires a knowledge update

---

### Stage 6 — Verification (all phases in order, no skipping)

**Skipping Stage 6 is not permitted.** If a phase cannot be run (e.g., printer unavailable), document why and defer the issue close until verification is possible. Do not proceed to Stage 7 with an unverified fix.

Run the test protocol from Stage 4 in full:
1. **Pre-fix confirmation** — call the affected interface *before* applying the fix and record the broken value. This is the mandatory baseline. If the fix is already applied, this step cannot be recovered — note the protocol violation and do not close the issue until the bug can be reproduced and re-verified on a fresh opportunity.
2. Apply fix (Stage 5)
3. **Post-fix verification** — call the same interface and confirm the value changed from broken → correct. Report: interface, before value, after value, all three required.
4. **Fallback path** — verify the alternate/fallback behavior also works correctly

All pass criteria must be checked by the agent. "Looks right" is not a pass criterion.

---

### Stage 7 — Delivery (mandatory, no skipping)

**Commit gate (absolute — no exceptions):** `git commit` is blocked until all of the following are true in the current turn:
- `python -m py_compile` passed on every changed `.py` file (Stage 5 step 2)
- `mcp-reload` completed and `-i done` received (Stage 5 step 3, for server-side changes)
- Stage 6 post-fix verification passed (including completeness trace for knowledge/rules changes)

If any of these have not been executed in the current turn, do not commit. Run the missing steps first.

All of the following, in order:

1. `git commit` with message referencing the issue number (`fixes #N` or `closes #N`)
2. `git push`
3. **Rules update** — if the fix reveals a behavioral gap that was previously undocumented, write it into the rules before closing the issue. A fix without a rules update is a fix that can be undone.
4. **Verification carried forward** — Stage 6 pre-fix confirmation + post-fix verification results satisfy Stage 7. Reproduce them here in the closing comment. If Stage 6 was skipped or incomplete, do not close the issue — rerun Stage 6 first.
5. GitHub issue: close only after step 4 is satisfied. The closing comment must include: interface queried, before value, after value.
6. `bambu-rules` sync — push updated project rules to remote mirror

---

## Bambu GCode Flavor — Camera Calibration Commands (Mandatory)

**Claim status: `[VERIFIED: BambuStudio]`** — BambuStudio machine profiles confirm `gcode_flavor: marlin` for all Bambu printers.
- Evidence: `resources/profiles/BBL/machine/fdm_machine_common.json` → `"gcode_flavor": "marlin"` (base profile; H2D inherits via `fdm_bbl_3dp_002_common`; A1 via `fdm_bbl_3dp_001_common`; all Bambu printers share this base)
- Confirmed: 2026-03-10
- Note: Prior session cited `resources/profiles/machine/Bambu Lab H2D.json` — this path does NOT exist in BambuStudio repo. All profiles live under `resources/profiles/BBL/`. Prior claim of `gcode_flavor = marlin2` is **REFUTED**; the correct value is `marlin`.

The following applies specifically to any agent-generated GCode for pre-print camera calibration sequences issued via `send_gcode()` or the HTTP `POST /api/send_gcode` route.

**Command set for calibration (all Bambu models — `[VERIFIED: BambuStudio]`):**

| Command | Purpose | Notes |
|---------|---------|-------|
| `G28` | Home all axes | Firmware-controlled homing order (H2D: X first, then Z separately per BambuStudio start gcode); safe to issue at any time |
| `G90` | Set absolute coordinate mode | Must be issued explicitly at calibration start; never assume prior state |
| `G0 X<n> Y<n> F<n>` | Rapid XY move | Always include `F`; Bambu accepts mm/min (standard Marlin units) |
| `G0 Z<n> F<n>` | Rapid Z move | Always include `F`; separate command from XY — never combined |
| `M400` | Wait for moves to complete | Mandatory before every Z transition and every frame capture |

**Safe calibration feedrates (conservative design choices — not sourced from printer specs):**
- `F3000` — XY/Y travel (50 mm/s): deliberate undershot of firmware-capable speeds (BambuStudio uses F30000 for XY travel); safe margin for agent-controlled motion
- `F600` — Z moves (10 mm/s): conservative descent/ascent; prevents nozzle crash from feedrate overshoot

**Coordinate system:** Always `G90` (absolute). Corner positions are expressed as absolute machine coordinates from the Bambu bed origin. Never use `G91` (relative) in a calibration sequence — position errors accumulate and are not recoverable without rehoming.

**Confirmed pattern from `print_control.py`:** `G91\nG0 X10\nG90` demonstrates that Bambu firmware correctly handles relative-then-back-to-absolute patterns if ever needed for auxiliary moves. This is **not used** in calibration sequences (which are always absolute), but confirms `G91`/`G90` mode switching works as expected on all Bambu models.

**Complete safe calibration sequence preamble (required at top of every generated sequence):**
```gcode
G28          ; home all axes — firmware handles safe homing order
M400         ; confirmed stopped
G90          ; absolute coordinate mode — explicit, never assumed
G0 Z10 F600  ; lift to clearance height before any XY travel
M400         ; confirmed at clearance
```

**Do not use:** `G1` (controlled-rate feed) is not necessary for calibration moves — `G0` (rapid move) is the correct command. Do not use firmware-specific extensions (e.g., Bambu's `M622`, AMS commands) in calibration sequences — use only the verified command set above.

This section is the Bambu-specific extension of the global "GCode Calibration Motion Safety" rule. The global rule governs structure and safety; this section governs the exact command syntax for Bambu printers.

## Proactive Bed Preheat Suggestion (Mandatory)

When the user asks about a print job's plate or a specific 3MF file (e.g. viewing plate thumbnails, checking filament, asking "what does plate N look like", reviewing a file on the SD card), assess whether preheating is appropriate and, if so, offer it proactively.

**Hard requirements:**
- **Always ask first** — never issue a preheat command without explicit user permission in the current turn.
- Determine "good time to preheat" by: printer is idle (`gcode_state` = IDLE/FINISH/FAILED), bed is currently at or near ambient, and a print job appears imminent from context.
- When offering, use `ask_user` — state the target bed and chamber temperatures and ask permission before acting.

**Temperature source (mandatory — do not hardcode or infer):**
All target temperatures must be derived from the MCP's own knowledge and tool docstrings:
1. Call `get_project_info()` for the plate — read `bed_type` and the filament list (type, `nozzle_temp_min`/`nozzle_temp_max`, `drying_temp`).
2. Consult the `print_file` tool docstring for `bed_type` semantics (`cool_plate`, `eng_plate`, `hot_plate`, `textured_plate`).
3. Consult `get_knowledge_topic('enums/filament')` for `PlateType` enum values and their standard temperature associations.
4. Consult `get_spool_info()` for the active spool's `nozzle_temp_min`/`nozzle_temp_max` and `drying_temp` if the filament is already loaded.
5. Never hardcode bed/chamber temperatures. If knowledge modules do not provide a clear target, state that and ask the user to confirm temps before offering to preheat.

**Execution (after permission):**
- Use `set_bed_temp(user_permission=True)` and `set_chamber_temp(user_permission=True)`.
- If the printer is already printing, do not offer (preheat is already active).
- If the user declines, do not offer again in the same conversation context for the same file.
