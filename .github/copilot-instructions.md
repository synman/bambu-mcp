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
- `bambu-printer-app` is a **knowledge reference only** — it must not be referenced or imported at runtime.
- No tool may open its own direct FTPS, MQTT, socket, or HTTP connection to a printer — **with one exception**: camera streaming.
- **Camera streaming exception**: the `camera/` module is explicitly permitted to open direct connections to the printer for video data only:
  - **TCP+TLS port 6000** — A1/P1 series camera protocol (`TCPFrameBuffer` in `camera/tcp_stream.py`)
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
3. **Relaunch using the bash tool with `mode="async", detach=true`**:
   ```
   cd ~/bambu-mcp && BAMBU_MCP_DEBUG=1 nohup .venv/bin/python3 server.py >> bambu-mcp.log 2>&1
   ```
   - `detach=true` is **mandatory** — without it, the shell treats the process as a job and suspends it (status `T`/stopped) when the shell exits. `nohup` alone is not sufficient without `detach=true`.
   - Do **not** use `setsid` — not available on macOS.
   - Do **not** use `nohup ... &` in a sync or non-detached async shell — the process will be stopped, not backgrounded.
4. **Verify**: `ps aux | grep "server.py" | grep -v grep` — confirm the process is running (`S` state, not `T`).
5. **Check logs**: `tail -10 ~/bambu-mcp/bambu-mcp.log` — confirm clean startup, no import errors.

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

### New route checklist

When adding a new route, the commit must include:
- [ ] Route implementation
- [ ] Multi-line docstring per standard above (summary + description + params + response + ⚠️ if irreversible)
- [ ] Entry in `_ROUTE_EXAMPLES` (both `params` and `response` keys with realistic values)
- [ ] Entry in `_ROUTE_TAGS` (correct category)
- [ ] Corresponding `knowledge/http_api_*.py` update (or new sub-topic file if the category doesn't exist)
- [ ] If new sub-topic file: add to `_KNOWN_TOPICS` + `_KNOWLEDGE_MAP` in `resources/knowledge.py` and `tools/knowledge_search.py`

### Reconciliation gate

Every reconciliation pass (Track 9) must include a Swagger audit:
- Spot-check all routes added or changed since the last pass
- Flag any single-line docstrings added since last pass
- Verify `_ROUTE_EXAMPLES` has `params` + `response` for every route
- Verify no deprecated route is missing its `⚠️ DEPRECATED SCAFFOLDING` annotation
- Re-run `curl http://localhost:{api_port}/api/openapi.json | python3 -m json.tool | grep -c '"example"'` to confirm example count

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

- Log file: `~/bambu-mcp/bambu-mcp.log` — written at DEBUG level when `BAMBU_MCP_DEBUG=1`.
- `BAMBU_MCP_DEBUG=1` must be set in the live MCP config (`~/.copilot/mcp-config.json`) for DEBUG output to reach the log file. Without it, only INFO+ is logged.
- Stderr always receives INFO+ regardless of `BAMBU_MCP_DEBUG`.

---

## BPM MCP Coverage Standard (Mandatory)

**Full traceability and accessibility is required for every item in bpm's public API.** This means every public method, field, property, and attribute in bpm must be reachable through at least one access path in the mcp, and that path must be accurately documented in a knowledge module.

**Three access paths (at least one required per bpm item):**
1. **MCP tool** — a registered MCP tool in `tools/*.py` wraps the method or serializes the field
2. **HTTP REST API** — a route in `api_server.py` exposes it (must trace to an actual bpm call)
3. **Both** — best coverage; required for all high-value operational methods

**Asymmetric coverage is not self-resolving (Type G gaps):**
A bpm item covered by an MCP tool but missing an HTTP route (or vice versa) is a **Type G gap** that requires explicit resolution — not an assumed design choice. An audit agent must NOT classify a G-type gap as "acceptable" or "intentional" based on inference. Every G-type finding must be resolved by either:
- Adding the missing access path (preferred), or
- Adding an explicit entry to the Intentional non-gaps table below with a documented reason

"Seems deliberate" or "probably MCP-only by design" are not valid resolutions. Only an explicit exclusions table entry closes a G-type gap.

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

The following BPM methods are **intentionally not** exposed as MCP tools or HTTP routes:

| BPM Method / Property | Reason |
|------------------------|--------|
| `get_current_bind_list` | Internal H2D helper called only by `print_3mf_file`; not a user-facing operation |
| `delete_all_contents` / `search_for_and_remove_*` | Internal FTPS helpers; not part of the public API |
| `speed_level` (read) | Surfaced in `get_printer_state`; dedicated getter is redundant |
| `set_spool_k_factor` | Firmware-broken, no-op stub; `select_extrusion_calibration` is the replacement |
| `rename_printer()` | Low-frequency management op; MCP-only is sufficient — no automation use case for HTTP |
| `set_first_layer_inspection()` (xcam raw) | No dedicated bpm method; MCP tool sends raw xcam command via `send_anything`; MCP-only |
| AMS dryer status fields (read) | Dryer state fields available in full JSON via `/api/printer`; no targeted HTTP route needed |
| Monitoring history/series (`get_monitoring_history`, `get_monitoring_data`, `get_monitoring_series`) | Large time-series data (60-min rolling, ~1440 pts/field); not suitable for synchronous HTTP polling; MCP-only |
| Firmware version (targeted) | `firmware_version` field in BambuConfig; accessible via `/api/printer` full JSON; targeted HTTP read would be redundant |
| Printer session lifecycle (`add_printer`, `remove_printer`, `update_printer_credentials`) | Session lifecycle operations; HTTP API operates within pre-configured sessions managed by the MCP server process itself |
| Printer connection status (`get_printer_connection_status`, `get_configured_printers`) | Session-level queries; available via `/api/printer` or MCP tools; HTTP REST API assumes sessions are pre-configured |
| MQTT session pause/resume (`pause_mqtt_session`, `resume_mqtt_session`) | HTTP has combined `/api/toggle_session`; deliberate consolidation into single toggle endpoint |
| Printer discovery (`discover_printers`) | SSDP discovery is a setup-time agent/CLI tool; HTTP API assumes printer already configured in session |
| `force_state_refresh()` | HTTP has `trigger_printer_refresh` which performs the same operation (with user_permission gate); deliberate naming divergence, not a gap |
| `/api/health_check` (HTTP-only) | Server-level diagnostic; no printer context; no MCP tool needed — agents can call the route directly |
| `/api/filament_catalog` (HTTP-only) | Material catalog lookup; BPA uses directly via HTTP; no agent use case for wrapping in MCP tool |
| Targeted read MCP tools (`get_temperatures`, `get_fan_speeds`, `get_climate`, `get_print_progress`, `get_job_info`, `get_hms_errors`, `get_spool_info`, `get_ams_units`, `get_external_spool`, `get_nozzle_info`, `get_capabilities`, `get_printer_info`, `get_wifi_signal`) | By design: all targeted read tools return focused subsets of `/api/printer` full JSON. HTTP consumers get everything at once; MCP tools offer targeted slices. Asymmetry is intentional. |

### Coverage audit checklist

Before closing any PR that adds/removes BPM methods, fields, or HTTP routes:
- [ ] Every new/changed bpm public item has at least one access path (MCP tool or HTTP route)
- [ ] Every new/changed item is documented in the appropriate knowledge module
- [ ] Every new HTTP route has a matching MCP tool (or is in the intentional exclusions table above)
- [ ] Every new MCP tool correctly wraps its BPM method with matching semantics
- [ ] Docstrings are present on both the HTTP route handler and the MCP tool function
- [ ] `_ROUTE_TAGS` and `_ROUTE_EXAMPLES` entries added for any new HTTP routes (per Swagger standard)
- [ ] Intentional non-coverage is listed in the exclusions table with a reason (deprecated-with-replacement items list the replacement; deprecated-without-replacement items are treated as regular items and need coverage)

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
- No knowledge module may exceed 200 lines. Split by sub-topic if it does.
- After any new feature is committed, `get_knowledge_topic('behavioral_rules/camera')` must include the new tool name and guidance.
- Docstring size estimates, stable_verdict semantics, confidence window interpretation — these belong in the knowledge layer, not repeated in tool docstrings.

**Knowledge update checklist (run on every feature commit):**
- [ ] `behavioral_rules_camera.py` updated with agent guidance for any new tool
- [ ] `api_reference_dataclasses.py` updated with new dataclass fields and semantics
- [ ] No content duplicated across knowledge modules
- [ ] `get_knowledge_topic()` returns the new content when called

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

Discover the API port first, then run smoke checks:
```bash
# Port discovery
PORT=$(python -c "
import json, subprocess
r = subprocess.run(['python', '-c',
  'from bambu_mcp.api_server import get_api_port; print(get_api_port())'],
  capture_output=True, text=True, cwd='$HOME/bambu-mcp')
print(r.stdout.strip())
" 2>/dev/null || echo "49152")

# Health gate — must pass before any other route check
curl -sf http://localhost:${PORT}/api/health_check | python3 -c "
import sys, json; d=json.load(sys.stdin); print('health_check:', 'OK' if d.get('status')=='ok' else 'FAIL', d)
"

# Printer state — must return JSON with expected top-level keys
curl -sf "http://localhost:${PORT}/api/printer" | python3 -c "
import sys, json; d=json.load(sys.stdin)
required = {'printers','connected'}
missing = required - set(d.keys())
print('printer route:', 'OK' if not missing else 'MISSING: '+str(missing))
"
```

**Pass criteria:** `health_check` returns `status: ok`; `/api/printer` returns JSON with expected keys; no 500 responses.

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

## Bug Fix Lifecycle Standard (Mandatory)

Applies to every discovered bug in bambu-mcp, regardless of source (audit, user report, tool output, or incidental discovery during other work). All 7 stages are mandatory. No stage may be skipped.

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
| **Write-capable tool** (any tool with `user_permission` guard) | Call tool with `user_permission=False` (or omitted) — confirm it is rejected; call with `user_permission=True` — confirm it executes | Post-fix: both paths again — blocked path still blocked, permitted path still works |
| Knowledge module (Python string constants, docstrings) | `git show HEAD:file \| grep` — show the ambiguous/missing text | `grep` — confirm correct text present; confirm forbidden content absent; **+ completeness trace (see below)** |
| Rules file (`copilot-instructions.md`) | `git show HEAD:.github/copilot-instructions.md \| grep` — show absent/wrong rule | `grep` — confirm correct rule present; **+ completeness trace if rule alters agent behavior** |
| Enum / data structure | `python -c "import …; assert …"` — show wrong value | Same — assert correct value |
| Config / flag default | Source read + assert — show wrong default | Source read + assert — show correct default |

**Write-block verification (mandatory for any tool with `user_permission` parameter):**
The blocked path must be tested independently of the permitted path. A tool that works correctly with `user_permission=True` but fails to block without it has a broken guard — this is a security gap, not a style issue. Both paths must pass before Stage 7:
- `user_permission=False` (or omitted) → tool returns error / refuses to execute
- `user_permission=True` → tool executes and produces the correct result

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

All of the following, in order:

1. `git commit` with message referencing the issue number (`fixes #N` or `closes #N`)
2. `git push`
3. **Rules update** — if the fix reveals a behavioral gap that was previously undocumented, write it into the rules before closing the issue. A fix without a rules update is a fix that can be undone.
4. **Verification carried forward** — Stage 6 pre-fix confirmation + post-fix verification results satisfy Stage 7. Reproduce them here in the closing comment. If Stage 6 was skipped or incomplete, do not close the issue — rerun Stage 6 first.
5. GitHub issue: close only after step 4 is satisfied. The closing comment must include: interface queried, before value, after value.
6. `bambu-rules` sync — push updated project rules to remote mirror
