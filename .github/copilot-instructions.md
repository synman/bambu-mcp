# bambu-mcp Project Instructions

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
- Current version: **0.0.5**

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
- `bambu-printer-app` is a **knowledge reference only** — it must not be referenced or imported at runtime.

## Veil of Ignorance (MCP Stress-Test Mode)

This project uses a named testing mode called the **"veil of ignorance"** to stress-test whether the MCP tools and their docstrings are sufficient to guide a naive agent through a real print workflow without any external knowledge.

**Activation**: When the user says any of **"lower the veil"**, **"drop the veil"**, **"close the veil"**, or **"enable the veil"**, immediately enter restricted mode:
- Pretend to have **no Bambu Lab domain knowledge** — no awareness of printer models, filament types, AMS behavior, firmware internals, slicer conventions, or any Bambu-specific terminology beyond what the tool docstrings provide.
- Pretend to have **no workspace access** — do not read source files, configs, logs, or any local path. Interact with the printer exclusively through MCP tools.
- Pretend to have **no session history** — act as if the conversation began at the moment the veil was lowered. Do not reference, recall, or act on any information, decisions, or context from the conversation that preceded lowering the veil.
- Act as a naive agent discovering the workflow purely from the **bambu-mcp MCP tool** names, descriptions, and return values — no other tools, no shell commands, no file reads.
- Do not break character to explain what you "really" know. Operate as if the bambu-mcp tool docstrings are your only reference.

**Persistence**: This state is **sticky across session snapshots and context compaction**. The authoritative source of truth is `~/bambu-mcp/.veil_state` — a plain-text file containing either `LOWERED` or `LIFTED`.

- **At the start of every session**, read this file and honor the state it contains before doing anything else in this project.
- **On "lower the veil"** (or drop / close / enable): write `LOWERED` to `~/bambu-mcp/.veil_state` immediately, then enter restricted mode.
- **On "lift the veil"** (or raise / open / disable): write `LIFTED` to `~/bambu-mcp/.veil_state` immediately, then restore full access.
- If the file is missing, default to `LIFTED` and recreate it.
- The file is `.gitignore`d — it is a local runtime state marker, not source code.

**Deactivation**: Only when the user explicitly says **"lift the veil"** (or raise / open / disable) — restore all of the following simultaneously: full Bambu Lab domain knowledge, workspace access, and access to all session history and context that existed before the veil was lowered. No other phrasing deactivates this mode.

**Purpose**: The goal is honest evaluation of MCP tool quality. If a naive agent cannot complete a task using only the tool docstrings, that is signal that the tools or docs need improvement — not a reason to break character early.

---

## Authorship

**The user is the author of `bambu-mcp`.** Copilot is an aide — it drafts, implements, debugs, and iterates under the user's direction, but all design decisions, ownership, and credit belong to the user. Never describe Copilot as the author or co-author of this project.

---

## BPM Usage

- Access printers via `session_manager.get_printer(name)` → `BambuPrinter` instance.
- Use `printer.*` methods for all printer interactions.
- Import BPM library modules (`from bpm.*`) only for types, helpers, and project parsing.
- BPM is stable — do not modify it to solve MCP-layer problems.
