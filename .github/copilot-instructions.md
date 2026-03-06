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

## Architecture

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

## BPM Usage

- Access printers via `session_manager.get_printer(name)` → `BambuPrinter` instance.
- Use `printer.*` methods for all printer interactions.
- Import BPM library modules (`from bpm.*`) only for types, helpers, and project parsing.
- BPM is stable — do not modify it to solve MCP-layer problems.
