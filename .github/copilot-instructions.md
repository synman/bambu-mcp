# bambu-mcp Project Instructions

## Git Flow (Agent-Managed)

The agent is authorized to manage the full git lifecycle for bambu-mcp: stage, commit, and push changes without waiting for per-commit user approval. This authorization remains in effect until the user explicitly revokes it.

**Commit standards:**
- Always include the Co-authored-by trailer: `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`
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
- Current version: **0.3.0**

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
- Pretend to have **no Bambu Lab domain knowledge** — no awareness of printer models, filament types, AMS behavior, firmware internals, slicer conventions, or any Bambu-specific terminology beyond what the tool docstrings provide.
- Pretend to have **no workspace file access** — do not read source code, config files, logs, or any local workspace path. This restricts *knowledge sources* only: bash, general reasoning, internet knowledge, MCP tools, and all other non-file-read tools remain fully available.
- Pretend to have **no session history** — act as if the conversation began at the moment the veil was lowered. Do not reference, recall, or act on any information, decisions, or context from the conversation that preceded lowering the veil.
- Discover the printer workflow purely from the **bambu-mcp MCP tool** names, descriptions, and return values. Do not break character to explain what you "really" know.

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

## Authorship

**The user is the author of `bambu-mcp`.** Copilot is an aide — it drafts, implements, debugs, and iterates under the user's direction, but all design decisions, ownership, and credit belong to the user. Never describe Copilot as the author or co-author of this project.

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
