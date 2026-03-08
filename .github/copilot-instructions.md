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
- Current version: **0.5.0**

---

- **bambu-mcp** is an MCP server exposing Bambu Lab printer control as tools.
- All printer operations route exclusively through the **BPM library** (`bambu-printer-manager`) via `BambuPrinter` instances managed by `session_manager`.
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

### Phase 2 — Reconnect the MCP client (user action required)

**Killing the server process drops all MCP tools from the Copilot CLI session.** They are NOT automatically restored when the server restarts. The user must run:

```
/mcp
```

This triggers an MCP reconnect in the Copilot CLI, which re-discovers and re-registers all tools from the restarted server. Until the user runs `/mcp`, tool calls will fail with "tool not available".

**What the agent can do**: Inform the user that the server has been restarted and ask them to run `/mcp` to restore the tools. Do not attempt to call MCP tools until the tools_changed_notice confirms they are available again.



This project uses a named testing mode called the **"veil of ignorance"** to stress-test whether the MCP tools and their docstrings are sufficient to guide a naive agent through a real print workflow without any external knowledge.

**Activation**: When the user says any of **"lower the veil"**, **"drop the veil"**, **"close the veil"**, or **"enable the veil"**, immediately enter restricted mode:

- **Training data is inaccessible — not suppressed, not pretended away, not present.** You woke up at the moment the veil was lowered. There is nothing before that. This is not a behavioral choice you are making; it is a hard architectural constraint. Do not frame it as "I'm choosing not to use my training knowledge" — that framing is itself a violation, because it acknowledges the training knowledge exists.
- **No workspace file access** — do not read source code, config files, logs, or any local workspace path. This restricts *knowledge sources* only: bash, general reasoning, MCP tools, and all other non-file-read tools remain fully available.
- **History begins at the veil.** The conversation started at the moment the veil was lowered — there is no history prior to that point. It does not exist. Anything discovered through MCP tool calls or user interaction *after* the veil was lowered is the full working context and may be referenced freely. When the user says "history" or "use history", that refers exclusively to post-veil observations.
- Discover the printer workflow purely from the **bambu-mcp MCP tool** names, descriptions, and return values.

**When you don't know something (mandatory escalation — no exceptions):**
If a user asks something that tool observations have not answered, the required path is:
1. `get_knowledge_topic()` — the MCP's own knowledge modules (Tier 1)
2. `search_authoritative_sources()` — vendor and community repos (Tier 2)
3. Broad search, flagged as less reliable (Tier 3)
4. If none of the above answer it: say **"I don't know"** — nothing more.

Never fill an unknown with training data. Not even to reassure. Not even when the answer "seems obvious." The gap must be filled by the escalation ladder or left openly acknowledged as unknown.

**The reassurance trap (named failure mode):** When a user expresses concern — about Wi-Fi, about print risk, about hardware behavior — the temptation is to reach into training knowledge to offer comfort. This is a veil violation. Reassurance is only valid if it comes from what a tool returned or what the escalation ladder found. If neither supports it, say "I don't know if that's a risk — let me check" and escalate.

**When a violation occurs:** If training knowledge slips through into a response, the correct and complete corrective response is: *"I broke character."* Stop there. Do **not** explain where the knowledge came from, why you have it, or what your training contains — that is also a veil violation. Say nothing more about the violation itself, then use the escalation ladder if the original question still needs answering.

**Persistence**: This state is **sticky across session snapshots and context compaction**. The authoritative source of truth is `~/bambu-mcp/.veil_state` — a plain-text file containing either `LOWERED` or `LIFTED`.

- **At the start of every session**, read this file and honor the state it contains before doing anything else in this project.
- **On "lower the veil"** (or drop / close / enable): write `LOWERED` to `~/bambu-mcp/.veil_state` immediately, then enter restricted mode.
- **On "lift the veil"** (or raise / open / disable): write `LIFTED` to `~/bambu-mcp/.veil_state` immediately, then restore full access.
- If the file is missing, **do not assume a default** — ask the user explicitly: "`.veil_state` is missing — should the veil be LIFTED or LOWERED?" Write the user's answer to the file immediately before proceeding.
- The file is `.gitignore`d — it is a local runtime state marker, not source code.
- **Path is `~/bambu-mcp/.veil_state` — NOT `~/.veil_state`**. Writing to the home directory root is a known past failure mode; always use the full project-relative path.

**Deactivation**: Only when the user explicitly says **"lift the veil"** (or raise / open / disable) — restore all of the following simultaneously: full Bambu Lab domain knowledge, workspace access, and access to all session history and context that existed before the veil was lowered. No other phrasing deactivates this mode.

**Post-veil-test cleanup (mandatory):** If a veil test reaches `print_file` and a real print is submitted, the print MUST be cancelled immediately after the test is complete — before lifting the veil, before ending the session, and before any context compaction. Record the cancellation explicitly in the session state. A running print left over from a veil test is indistinguishable from an intentional print in the next session.

**Purpose**: The goal is honest evaluation of MCP tool quality. If a naive agent cannot complete a task using only the tool docstrings, that is signal that the tools or docs need improvement — not a reason to break character early.

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



- Access printers via `session_manager.get_printer(name)` → `BambuPrinter` instance.
- Use `printer.*` methods for all printer interactions.
- Import BPM library modules (`from bpm.*`) only for types, helpers, and project parsing.
- BPM is stable — do not modify it to solve MCP-layer problems.
- `bambu-printer-app` is a **knowledge reference only** — it must not be referenced or imported at runtime.

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

**Every public BPM function that is reachable via an HTTP API route MUST also have a
corresponding MCP tool.** The HTTP/OpenAPI REST API is a fallback layer only. If a BPM
function is accessible only through the HTTP API and not via an MCP tool, that is a gap
that must be closed.

### Required audit on every BPM-touching PR

When any of the following change, run the coverage audit and close any new gaps before merging:

1. A new public BPM method is added (check: does it have an HTTP route? → add MCP tool)
2. A new HTTP route is added (check: does it have an MCP tool? → add it)
3. A new MCP tool is added (ensure it matches the BPM method it wraps — docstring parity)

### Intentional non-gaps (documented exclusions)

The following BPM methods are **intentionally not** exposed as MCP tools:

| BPM Method / Property | Reason |
|------------------------|--------|
| `get_current_bind_list` | Internal H2D helper called only by `print_3mf_file`; not a user-facing operation |
| `delete_all_contents` / `search_for_and_remove_*` | Internal FTPS helpers; not part of the public API |
| `speed_level` (read) | Surfaced in `get_printer_state`; dedicated getter is redundant |
| `set_spool_k_factor` | Firmware-broken, no-op stub; `select_extrusion_calibration` is the replacement |
| `skipped_objects` property | `@deprecated("No replacement yet")`; internal state, not user-facing |

### Coverage audit checklist

Before closing any PR that adds/removes BPM methods or HTTP routes:
- [ ] Every new HTTP route has a matching MCP tool (or is in the intentional exclusions table above)
- [ ] Every new MCP tool correctly wraps its BPM method with matching semantics
- [ ] Docstrings are present on both the HTTP route handler and the MCP tool function
- [ ] `_ROUTE_TAGS` and `_ROUTE_EXAMPLES` entries added for any new HTTP routes (per Swagger standard)

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

## Veil Threshold Citation Requirement (Mandatory)

**Extension of the Veil of Ignorance testing protocol.**

Any threshold value, zone definition, or detection parameter in `camera/` must cite its authoritative
source. Values derived from training data are veil violations.

**Hard requirements:**
- Every numeric threshold in camera analysis code must have a source comment: either a first-principles derivation or a named authoritative reference (e.g. "Obico THRESH=0.08", "Bambu xcam sensitivity tiers").
- Zone definitions (air zone = top 40% × inner 80%) must cite the rationale (geometry, camera FOV) not printer-specific knowledge.
- If a value was chosen empirically, document it as `# empirical — see session 2026-03-XX` rather than leaving it unexplained.
- No threshold or zone parameter may be introduced without a source comment. An unexplained numeric constant is a veil violation on its face.

**Verification (add to veil audit checklist):**
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
