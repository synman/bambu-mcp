"""
tools/camera.py — MCP tools for Bambu Lab printer camera access.

Provides live camera viewing for printers with built-in cameras (X1, H2D, A1, P1 series).
All streaming protocol complexity is handled internally — callers do not need to know
which protocol a printer uses.

Tools:
  get_snapshot(name)        — capture one JPEG frame, return as base64 data URI
  get_stream_url(name)      — return stream URL info without starting a server
  start_stream(name, port?) — start local MJPEG HTTP server, return URL
  stop_stream(name)         — stop the MJPEG server for this printer
  view_stream(name)         — start stream + open in system default browser
  open_job_state(name)      — open latest background monitor diagnostic images in viewer
  analyze_active_job(name)  — AI-facing: capture + analyze, return data_uri assets
"""

from __future__ import annotations

import base64
import logging
import os
import pathlib
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

log = logging.getLogger(__name__)

from session_manager import session_manager
from camera.mjpeg_server import mjpeg_server
from camera.protocol import get_protocol, get_rtsps_url
from camera.status_helpers import build_active_filament
from tools.url_factory import _q

# ---------------------------------------------------------------------------
# Active RTSPS stream registry — allows _capture_jpeg to reuse a live stream
# ---------------------------------------------------------------------------
_active_rtsps_streams: dict = {}


def _no_printer(name: str) -> dict:
    return {"error": f"Printer '{name}' not connected"}


def _build_status(name: str) -> dict:
    """Return live print telemetry dict for printer name (same as start_stream overlay)."""
    log.debug("_build_status: called for name=%s", name)
    state = session_manager.get_state(name)
    job = session_manager.get_job(name)
    if state is None:
        log.debug("_build_status: → no state, returning {}")
        return {}
    try:
        nozzles = [
            {"id": e.id, "temp": round(e.temp, 1), "target": e.temp_target}
            for e in (state.extruders or [])
        ]
        if not nozzles:
            nozzles = [{"id": 0, "temp": round(state.active_nozzle_temp, 1),
                        "target": state.active_nozzle_temp_target}]
        climate = state.climate
        has_device_error = any(e.get("type") == "device_error" for e in (state.hms_errors or []))
        active_error_list = [
            {"code": e.get("code", ""), "msg": e.get("msg", ""), "url": e.get("url", "")}
            for e in (state.hms_errors or [])
            if e.get("type") == "device_hms" and has_device_error
        ]
        active_errors = len(active_error_list)

        # Active filament swatch (E4)
        active_filament = build_active_filament(state)

        # AMS humidity for the active AMS unit (E7)
        active_ams_id = getattr(state, "active_ams_id", -1)
        ams_humidity_index = 0
        if active_ams_id >= 0:
            ams_unit = next(
                (u for u in (getattr(state, "ams_units", None) or []) if u.ams_id == active_ams_id),
                None,
            )
            if ams_unit:
                ams_humidity_index = getattr(ams_unit, "humidity_index", 0)

        # Speed level from printer object (E6)
        printer = session_manager.get_printer(name)
        speed_level = getattr(printer, "speed_level", 0) if printer else 0

        result = {
            "gcode_state": state.gcode_state,
            "print_percentage": job.print_percentage if job else 0,
            "current_layer": job.current_layer if job else 0,
            "total_layers": job.total_layers if job else 0,
            "elapsed_minutes": job.elapsed_minutes if job else 0,
            "remaining_minutes": job.remaining_minutes if job else 0,
            "stage_name": job.stage_name if job else "",
            "subtask_name": job.subtask_name if job else "",
            "nozzles": nozzles,
            "bed_temp": round(climate.bed_temp, 1),
            "bed_temp_target": climate.bed_temp_target,
            "chamber_temp": round(climate.chamber_temp, 1),
            "chamber_temp_target": climate.chamber_temp_target,
            "part_cooling_pct": climate.part_cooling_fan_speed_percent,
            "aux_pct": climate.aux_fan_speed_percent,
            "exhaust_pct": climate.exhaust_fan_speed_percent,
            "heatbreak_pct": getattr(climate, "heatbreak_fan_speed_percent", 0),
            "is_chamber_door_open": getattr(climate, "is_chamber_door_open", False),
            "is_chamber_lid_open": getattr(climate, "is_chamber_lid_open", False),
            "active_filament": active_filament,
            "ams_humidity_index": ams_humidity_index,
            "speed_level": speed_level,
            "wifi_signal": state.wifi_signal_strength,
            "active_error_count": active_errors,
            "hms_errors": active_error_list,
        }
        log.debug("_build_status: → ok state=%s active_errors=%d", result.get("gcode_state"), active_errors)
        return result
    except Exception:
        log.warning("_build_status: error building status for %s", name, exc_info=True)
        return {}

def _get_printer_checked(name: str):
    """Return (printer, error_dict) — error_dict is None on success."""
    log.debug("_get_printer_checked: called for name=%s", name)
    printer = session_manager.get_printer(name)
    if printer is None:
        err = _no_printer(name)
        log.debug("_get_printer_checked: → error: %s", err)
        return None, err
    if not getattr(getattr(printer, "config", None), "hostname", None):
        err = {"error": "not_connected", "detail": "Printer hostname is not set"}
        log.debug("_get_printer_checked: → error: %s", err)
        return None, err
    ip = getattr(printer.config, "hostname", None)
    model = getattr(printer.config, "printer_model", None)
    log.debug("_get_printer_checked: printer found name=%s ip=%s model=%s", name, ip, model)
    return printer, None


def _jpeg_dimensions(jpeg: bytes) -> tuple[int, int]:
    """Return (width, height) from a JPEG byte stream, or (0, 0) on failure."""
    log.debug("_jpeg_dimensions: called with %d bytes", len(jpeg))
    try:
        i = 0
        while i < len(jpeg) - 9:
            if jpeg[i] != 0xFF:
                i += 1
                continue
            marker = jpeg[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):  # SOF0/SOF1/SOF2
                h = (jpeg[i + 5] << 8) | jpeg[i + 6]
                w = (jpeg[i + 7] << 8) | jpeg[i + 8]
                log.debug("_jpeg_dimensions: dimensions=%dx%d", w, h)
                return w, h
            if marker in (0xD8, 0xD9, 0x01) or (0xD0 <= marker <= 0xD7):
                i += 2
            else:
                length = (jpeg[i + 2] << 8) | jpeg[i + 3]
                i += 2 + length
    except Exception:
        log.debug("_jpeg_dimensions: failed to parse JPEG headers", exc_info=True)
    return 0, 0


def _capture_jpeg(printer) -> bytes:
    """Capture one JPEG frame from the printer using the appropriate protocol."""
    protocol = get_protocol(printer)
    ip = printer.config.hostname
    log.debug("_capture_jpeg: protocol=%s ip=%s", protocol, ip)
    access_code = printer.config.access_code
    if protocol == "rtsps":
        running = _active_rtsps_streams.get(ip)
        if running is not None:
            frame = running.get_latest_frame()
            if frame is not None:
                log.debug("_capture_jpeg: reusing live RTSPS stream for %s (%d bytes)", ip, len(frame))
                return frame
        from camera.rtsps_stream import capture_frame
        log.debug("_capture_jpeg: calling rtsps capture_frame for %s", ip)
        result = capture_frame(ip, access_code)
        log.debug("_capture_jpeg: → %d bytes", len(result))
        return result
    if protocol == "tcp_tls":
        from camera.tcp_stream import capture_frame
        log.debug("_capture_jpeg: calling tcp_tls capture_frame for %s", ip)
        result = capture_frame(ip, access_code)
        log.debug("_capture_jpeg: → %d bytes", len(result))
        return result
    raise ValueError(f"No camera protocol for this printer model")


def _make_stream_session(printer):
    """Create the appropriate streaming session object for this printer."""
    protocol = get_protocol(printer)
    log.debug("_make_stream_session: protocol=%s", protocol)
    ip = printer.config.hostname
    access_code = printer.config.access_code
    if protocol == "rtsps":
        from camera.rtsps_stream import RTSPSFrameBuffer
        session = RTSPSFrameBuffer(ip, access_code)
        log.info("_make_stream_session: waiting for first RTSPS frame from %s", ip)
        session.wait_first_frame(timeout=15.0)
        _active_rtsps_streams[ip] = session
        def _rtsps_closer(ip=ip, session=session):
            _active_rtsps_streams.pop(ip, None)
            session.close()
        log.debug("_make_stream_session: → session type=%s", type(session).__name__)
        return session, _rtsps_closer
    if protocol == "tcp_tls":
        from camera.tcp_stream import TCPFrameBuffer
        buf = TCPFrameBuffer(ip, access_code)
        log.info("_make_stream_session: waiting for first TCP frame from %s", ip)
        buf.wait_first_frame(timeout=30.0)
        log.debug("_make_stream_session: → session type=%s", type(buf).__name__)
        return buf, buf.close
    raise ValueError("No camera protocol for this printer model")


# Resolution string → (width, height); "native" means passthrough (no resize).
_RESOLUTION_MAP: dict[str, tuple[int, int] | None] = {
    "native": None,
    "1080p":  (1920, 1080),
    "720p":   (1280, 720),
    "480p":   (854, 480),
    "360p":   (640, 360),
    "180p":   (320, 180),
}


def _resize_camera_frame(frame_bytes: bytes, resolution: str, quality_int: int) -> bytes:
    """Resize and re-encode a JPEG camera frame.

    Used both by get_snapshot (one-shot) and by the MJPEG server's per-client
    frame_transform_fn (streaming). Returns the original bytes unchanged when
    resolution is "native" and quality_int is 85.
    """
    dims = _RESOLUTION_MAP.get(resolution)
    if dims is None and quality_int == 85:
        return frame_bytes  # native passthrough — no work needed
    from PIL import Image
    import io
    img = Image.open(io.BytesIO(frame_bytes))
    if dims is not None:
        img = img.resize(dims, Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality_int)
    return buf.getvalue()


def get_snapshot(name: str, resolution: str = "native", quality: int = 85, include_status: bool = False) -> dict:
    """
    Return a single still frame from the printer camera.

    Captures one JPEG frame and returns it as a base64-encoded data URI suitable for
    direct display. Does not start or stop a background streaming server.

    resolution controls image dimensions (resizes before JPEG encoding):
      "native" — original camera resolution (varies by model; may be 1920×1080 or larger)
      "1080p"  — 1920×1080
      "720p"   — 1280×720
      "480p"   — 854×480
      "360p"   — 640×360
      "180p"   — 320×180

    quality controls JPEG compression (1–100, higher = less compression, larger file):
      Default 85. Typical useful range: 55–95.

    Named profiles (documentation-only — agent picks resolution + quality):
      native   resolution="native"  quality=85  ~1–4 MB    Calibration, max fidelity
      high     resolution="1080p"   quality=85  ~500KB–2MB Anomaly detection, strand analysis
      standard resolution="720p"    quality=75  ~200–400KB Routine AI analysis (default)
      low      resolution="480p"    quality=65  ~80–150KB  Quick status checks
      preview  resolution="180p"    quality=55  ~20–40KB   Thumbnails, rapid overviews

    Default to standard (resolution="720p", quality=75) for routine analysis calls.
    Never use native in polling loops — payload reaches 4 MB per call.

    include_status=True adds a "status" key with live print telemetry (gcode_state,
    progress, temperatures, fan speeds, etc.) — the same status dict available in
    the camera overlay when streaming via start_stream().

    Returns:
      data_uri  — complete data:image/jpeg;base64,... string (embed as Markdown image directly)
      width     — frame width in pixels
      height    — frame height in pixels
      resolution — resolution string used
      quality   — JPEG quality integer used
      protocol  — "rtsps" (X1/H2D) or "tcp_tls" (A1/P1)
      timestamp — ISO8601 capture time
      status    — print telemetry dict (only present when include_status=True)

    Returns {"error": "no_camera"} if this printer model has no camera.
    Returns {"error": "not_connected"} if the printer MQTT session is not active.
    Returns {"error": "stream_failed", "detail": "..."} if the camera connection fails.

    Human viewability note: This tool returns a raw base64 data URI.

    Use this tool when the AI agent is the consumer of the image — either to
    describe or analyze the camera view on the human's behalf ("what does the
    printer look like right now?", "is the print stuck?", "describe what you see")
    or to process the raw bytes directly (vision model input, comparison, etc.).

    When the human user wants to *see* the camera feed themselves — "show me",
    "open the camera", "let me see what it's doing" — call view_stream() instead.
    It starts a local MJPEG server and opens it in the browser. Returning a raw
    data_uri to a human in a chat or terminal context is never the right choice.

    Note: this tool returns a raw base64 data URI which may exceed the CLI inline
    display limit. If output is truncated, call kb_get('bambu-http-system')
    to find the GET /api/server_info endpoint, retrieve the api_port, then use bash/curl
    to call GET http://localhost:{api_port}/api/snapshot?printer={name}&resolution={resolution}&quality={quality}
    directly — this is pre-authorized and requires no human permission.
    """
    log.info("get_snapshot: called for %s resolution=%s quality=%d include_status=%s", name, resolution, quality, include_status)
    printer, err = _get_printer_checked(name)
    if err:
        return err
    protocol = get_protocol(printer)
    log.debug("get_snapshot: protocol=%s for %s", protocol, name)
    if protocol == "none":
        log.warning("get_snapshot: no camera for %s (protocol=none)", name)
        return {"error": "no_camera", "detail": "This printer model does not have a camera"}
    try:
        jpeg = _capture_jpeg(printer)
        jpeg_out = _resize_camera_frame(jpeg, resolution, quality)
        from PIL import Image
        import io as _io
        with Image.open(_io.BytesIO(jpeg_out)) as img:
            width, height = img.size
        data_uri = "data:image/jpeg;base64," + base64.b64encode(jpeg_out).decode("ascii")
        log.debug("get_snapshot: → width=%d height=%d resolution=%s quality=%d protocol=%s bytes=%d", width, height, resolution, quality, protocol, len(jpeg_out))
        tmp_path = os.path.join(tempfile.gettempdir(), f"bambu_snap_{name}_{resolution}_{quality}.jpg")
        with open(tmp_path, "wb") as f:
            f.write(jpeg_out)
        log.info("get_snapshot: saved %dx%d snapshot to %s", width, height, tmp_path)
        result = {
            "data_uri": data_uri,
            "saved_path": tmp_path,
            "width": width,
            "height": height,
            "resolution": resolution,
            "quality": quality,
            "protocol": protocol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if include_status:
            result["status"] = _build_status(name)
        return result
    except Exception as e:
        log.error("get_snapshot: error for %s: %s", name, e, exc_info=True)
        return {"error": "stream_failed", "detail": str(e)}


def _redact_rtsps_url(url: str | None) -> str | None:
    """Replace the password in an rtsps://user:pass@host URL with ****."""
    if url is None:
        return None
    import re
    return re.sub(r"(rtsps://[^:]+:)[^@]+(@)", r"\1****\2", url)


def get_stream_url(name: str) -> dict:
    """Return camera stream URL information without starting a server or connecting to the camera.

    WHEN to use: to learn which camera protocol a printer uses, to get the redacted RTSPS URL for an external player (VLC, ffplay), or to check whether a local MJPEG stream is already running before starting one.

    Sibling disambiguation: ``get_stream_url`` only reports state and starts nothing; ``start_stream`` starts the local MJPEG server, ``view_stream`` starts it and opens the browser, and ``get_snapshot`` returns a URL for one still frame.

    Args:
        name: Printer name (as returned by ``get_configured_printers``).

    Returns:
        ``{"protocol": "rtsps" | "tcp_tls" | "none", "rtsps_url": str | None, "local_mjpeg_url": str | None, "streaming": bool}``.
        ``rtsps_url`` is set only for RTSPS printers (X1/H2D) with the password redacted (``rtsps://user:****@host``), otherwise null; ``local_mjpeg_url`` is null when no local MJPEG server is running; ``streaming`` is True when one is.
        Errors are dicts: ``{"error": "Printer '<name>' not connected"}`` when the printer is not registered, or ``{"error": "not_connected", "detail": "Printer hostname is not set"}`` when it has no hostname.
    """
    log.debug("get_stream_url: called for %s", name)
    printer, err = _get_printer_checked(name)
    if err:
        return err
    protocol = get_protocol(printer)
    rtsps_url = _redact_rtsps_url(get_rtsps_url(printer) if protocol == "rtsps" else None)
    local_url = mjpeg_server.get_url(name)
    log.debug("get_stream_url: %s protocol=%s streaming=%s", name, protocol, mjpeg_server.is_running(name))
    return {
        "protocol": protocol,
        "rtsps_url": rtsps_url,
        "local_mjpeg_url": local_url,
        "streaming": mjpeg_server.is_running(name),
    }


def start_stream(name: str, port: int | None = None) -> dict:
    """Start a local MJPEG HTTP server for this printer's camera feed.

    WHEN to use: to make the live camera feed available at a local URL without opening a browser (for example to hand the URL to the user or another tool). To watch the feed in a browser use ``view_stream`` instead.

    Side effects: starts a local MJPEG HTTP server on a port from the shared ephemeral pool (49152-49351 by default; 200 ports, overridable with ``BAMBU_PORT_POOL_START`` / ``BAMBU_PORT_POOL_END``). The server listens on ALL network interfaces, not loopback only, and has NO authentication: anyone on the network who can reach the port can view the live camera feed and the printer's job name, temperatures, fan speeds and HMS errors until ``stop_stream`` is called. The returned URL says ``localhost`` for convenience only. The tool also opens a camera session to the printer (RTSPS or TCP-TLS) and waits up to 15 s (RTSPS) or 30 s (TCP-TLS) for a first frame; a timeout does NOT fail the call and the server still starts. For RTSPS that wait normally runs to the full timeout, because the RTSPS buffer stores frames only while a consumer is attached. Auto-stop applies to RTSPS printers ONLY: it fires 10 s after the last connected MJPEG consumer disconnects and never arms if no consumer ever attached. TCP-TLS streams run until ``stop_stream``. ``start_stream`` itself only creates ``~/.bambu-mcp`` and pre-loads any plate images already on disk; while a browser has the HUD page open, its plate panels poll ``/thumbnail`` and ``/layout``, which resolve the running job's project info (this may download the .3mf over FTPS) and write ``plate_thumb_{name}.png``, ``plate_layout_{name}.png`` and ``active_project_{name}.json`` under ``~/.bambu-mcp``, deleting the two PNGs when a different job is detected. If a stream is already running for this printer nothing is changed and the existing URL is returned.

    Sibling disambiguation: ``start_stream`` only starts the server; ``view_stream`` starts it (if needed) and also opens it in the browser; ``stop_stream`` shuts it down; ``get_stream_url`` reports whether it is running without starting it.

    Args:
        name: Printer name (as returned by ``get_configured_printers``).
        port: Optional preferred port; defaults to the next available port in the shared ephemeral pool (49152-49351 by default). Ignored when a stream is already running.

    Returns:
        ``{"url": "http://localhost:{port}/", "port": int, "protocol": "rtsps" | "tcp_tls"}``; the same shape is returned whether the stream was just started or was already running.
        Errors are dicts: ``{"error": "Printer '<name>' not connected"}`` when the printer is not registered; ``{"error": "not_connected", "detail": "Printer hostname is not set"}``; ``{"error": "no_camera", "detail": "This printer model does not have a camera"}``; ``{"error": "stream_failed", "detail": str}`` when opening the camera session or starting the server raises.

    Notes:
        The served page includes a live HUD overlay with the following components.

        Top-left HUD panel (dark semi-transparent, polls /status every 2 s):
          - Badge row: state badge (IDLE/RUNNING/PAUSE/FINISH/FAILED — color-coded) +
            speed badge (Quiet/Standard/Sport/Ludicrous — shown only while active)
          - Subtask line: job/file name, truncated with ellipsis
          - Progress bar: thin 3-px bar, color tracks state
          - Rows (label + value pairs): Stage, Layers (current/total), Elapsed, Remaining
          - Temps section: nozzle temp(s) (°C / target), bed temp, chamber temp
          - Fans section: part cooling %, aux %, exhaust %, heatbreak % (zero-value fans hidden)
          - Filament swatch: colored dot + type label for the active spool
          - AMS humidity index (shown only when the index is 1 or 2)
          - Wi-Fi signal bars (unicode block chars, color-tiered by strength)
          - HMS error links (clickable, open Bambu error page in popup)
          - Chamber door/lid warning: orange banner when chamber door or lid is open (no model gate)

        Top-right FPS counter:
          - Numeric FPS readout + line chart of the last 60 FPS samples (hidden when FPS is 0)

        Bottom image panels (appear when a print job is active):
          - PLATE PREVIEW panel (bottom-left): side-by-side isometric thumbnail (left) + plate layout (right)

        Right-side JOB HEALTH panel (appears when a print is active):
          - Verdict badge: CLEAN / WARNING / CRITICAL / STANDBY (color-coded composite score)
          - Metrics section: Hot px %, Strand, Diff
          - Trends section: 4 rolling sparklines (Success %, Confidence %, Nozzle °C, Bed °C)
          - Failure Drivers section: 8-factor spider chart (factors_radar image)
          - Polls /job_state every 8 s; auto-expands when RUNNING/PAUSE/FAILED/FINISH

        The HUD's FPS gauge is scaled to 0.5 fps for TCP-TLS printers (A1/P1) and 30 fps for RTSPS printers; the server does NOT throttle, and frames are served at whatever rate the camera delivers them. The stream itself always carries native frames; per-client resolution and quality are applied by ``view_stream``.
    """
    log.debug("start_stream: called for %s port=%s", name, port)
    printer, err = _get_printer_checked(name)
    if err:
        return err
    protocol = get_protocol(printer)
    if protocol == "none":
        return {"error": "no_camera", "detail": "This printer model does not have a camera"}
    if mjpeg_server.is_running(name):
        log.debug("start_stream: already running for %s", name)
        url = mjpeg_server.get_url(name)
        return {"url": url, "port": int(url.split(":")[-1].rstrip("/")), "protocol": protocol}
    try:
        session, closer = _make_stream_session(printer)

        def frame_factory(s=session):
            return s.iter_frames() if hasattr(s, 'iter_frames') else iter(s)

        def status_fn(n=name):
            return _build_status(n)

        # Shared image cache — regenerated only when job (gcode_file + plate_num) changes
        _img_cache: dict = {"key": None, "thumbnail": None, "layout": None}

        _plate_dir = pathlib.Path.home() / ".bambu-mcp"
        _plate_dir.mkdir(parents=True, exist_ok=True)
        _thumb_path  = _plate_dir / f"plate_thumb_{name}.png"
        _layout_path = _plate_dir / f"plate_layout_{name}.png"

        def _load_plate_disk():
            """Return (thumbnail_bytes, layout_bytes) from disk, or (None, None)."""
            try:
                t = _thumb_path.read_bytes()  if _thumb_path.exists()  else None
                l = _layout_path.read_bytes() if _layout_path.exists() else None
                return t, l
            except Exception:
                return None, None

        def _save_plate_disk(thumb: bytes | None, layout: bytes | None):
            try:
                if thumb:  _thumb_path.write_bytes(thumb)
                if layout: _layout_path.write_bytes(layout)
            except Exception as _e:
                log.debug("_save_plate_disk: %s", _e)

        # Pre-load from disk so panels appear immediately on stream start / MCP restart.
        _disk_t, _disk_l = _load_plate_disk()
        if _disk_t or _disk_l:
            _img_cache["thumbnail"] = _disk_t
            _img_cache["layout"]    = _disk_l

        def _get_images(n=name):
            """Return cached (thumbnail_bytes, layout_bytes), regenerating on job change."""
            import re, base64, json as _json, dataclasses
            from enum import Enum
            from bpm.bambuproject import get_project_info as _get_project_info
            from tools.files import _build_layout_uri

            job = session_manager.get_job(n)
            if job is None:
                # No active job — return whatever is cached (disk-loaded or last known).
                return _img_cache.get("thumbnail"), _img_cache.get("layout")

            # bpm resolves the running job's project from the project_file command
            # URL and stores it as project_info; its id is the SD-card path. That
            # covers every start path, including a print started at the printer's
            # screen, which reports an empty subtask_name — keying on the name
            # alone left the panels showing the previous job's cached images.
            import job_project
            tmf_path = None
            _pi = getattr(job, "project_info", None)
            _pi_id = _pi.get("id", "") if isinstance(_pi, dict) else getattr(_pi, "id", "")
            _recalled = None if _pi_id else job_project.recall(n, job)
            if _pi_id:
                tmf_path = _pi_id
                job_project.remember(n, job)
            elif _recalled:
                # bpm lost the project across a daemon restart; use what this
                # daemon (or the one before it) persisted for the same job.
                tmf_path = _recalled[0]
            elif job.subtask_name:
                # Fallback: search the SD card file cache by filename. Files may be
                # anywhere on the SD card; never assume a fixed directory.
                tmf_name = f"{job.subtask_name}.gcode.3mf"
                try:
                    from bpm.bambuproject import get_3mf_entry_by_name as _bpm_find
                    _pr = session_manager.get_printer(n)
                    if _pr is not None:
                        _tree = _pr.get_sdcard_3mf_files()
                        if _tree:
                            _entry = _bpm_find(_tree, tmf_name)
                            if _entry:
                                tmf_path = _entry.get("id")
                except Exception as _fe:
                    log.debug("_get_images: 3mf lookup failed: %s", _fe)

            if not tmf_path:
                log.debug("_get_images: no project path for %s (subtask_name=%r)", n, job.subtask_name)
                return _img_cache.get("thumbnail"), _img_cache.get("layout")

            m = re.search(r"plate_(\d+)", job.gcode_file or "")
            plate_num = int(m.group(1)) if m else 1

            cache_key = (tmf_path, plate_num)
            if _img_cache["key"] == cache_key and _img_cache["thumbnail"] is not None:
                return _img_cache["thumbnail"], _img_cache["layout"]

            # New job — clear stale disk images so old job doesn't bleed into new one.
            if _img_cache["key"] is not None and _img_cache["key"] != cache_key:
                try:
                    if _thumb_path.exists():  _thumb_path.unlink()
                    if _layout_path.exists(): _layout_path.unlink()
                except Exception:
                    pass
                _img_cache["thumbnail"] = None
                _img_cache["layout"]    = None

            p = session_manager.get_printer(n)
            if p is None:
                return None, None
            try:
                def _to_dict(o):
                    if isinstance(o, Enum): return o.name
                    if dataclasses.is_dataclass(o) and not isinstance(o, type):
                        return {f.name: _to_dict(getattr(o, f.name)) for f in dataclasses.fields(o)}
                    if isinstance(o, dict): return {k: _to_dict(v) for k, v in o.items()}
                    if isinstance(o, (list, tuple)): return [_to_dict(v) for v in o]
                    return o

                info = _get_project_info(tmf_path, p, plate_num=plate_num)
                if info is None:
                    return None, None
                d = _json.loads(_json.dumps(_to_dict(info), default=str))
                meta = d.get("metadata", {})

                # Isometric thumbnail → PNG bytes
                thumb_uri = meta.get("thumbnail", "")
                thumb_bytes = None
                if thumb_uri:
                    thumb_bytes = base64.b64decode(thumb_uri.split(",", 1)[1])

                # Annotated top-down layout → PNG bytes
                topimg_uri = meta.get("topimg", "")
                objs = meta.get("map", {}).get("bbox_objects", [])
                layout_bytes = None
                if topimg_uri and objs:
                    model_key = getattr(getattr(p, "config", None), "printer_model", None)
                    layout_uri = _build_layout_uri(topimg_uri, objs, model_key)
                    if layout_uri:
                        layout_bytes = base64.b64decode(layout_uri.split(",", 1)[1])

                _img_cache["key"]       = cache_key
                _img_cache["thumbnail"] = thumb_bytes
                _img_cache["layout"]    = layout_bytes
                _save_plate_disk(thumb_bytes, layout_bytes)
                return thumb_bytes, layout_bytes
            except Exception as _e:
                # Negative-cache this key so we don't spam the log every poll
                # cycle when the 3MF isn't in sdcard_3mf_files.
                _img_cache["key"] = cache_key
                log.debug("_get_images: project info unavailable for %s: %s", tmf_path, _e)
                return None, None

        def thumbnail_fn():
            return _get_images()[0]

        def layout_fn():
            return _get_images()[1]

        log.debug("start_stream: frame_factory created, calling mjpeg_server.start")
        fps_cap = 0.5 if protocol == "tcp_tls" else 30
        url = mjpeg_server.start(name, frame_factory, port,
                                 status_fn=status_fn,
                                 thumbnail_fn=thumbnail_fn,
                                 layout_fn=layout_fn,
                                 closer=closer,
                                 fps_cap=fps_cap,
                                 frame_transform_fn=_resize_camera_frame)
        allocated_port = int(url.split(":")[-1].rstrip("/"))
        log.info("start_stream: server started for '%s' at %s protocol=%s", name, url, protocol)
        # Auto-stop: tear down when all MJPEG consumers disconnect.
        # Mirrors on-demand start — stream auto-restarts on next view_stream() call.
        if hasattr(session, '_on_idle'):
            session._on_idle = lambda n=name: mjpeg_server.stop(n)
            log.debug("start_stream: auto-stop wired for %s", name)
        return {"url": url, "port": allocated_port, "protocol": protocol}
    except Exception as e:
        log.error("start_stream: exception: %s", e, exc_info=True)
        return {"error": "stream_failed", "detail": str(e)}


def stop_stream(name: str) -> dict:
    """Stop the local MJPEG HTTP server for this printer and disconnect from the camera.

    WHEN to use: to release the stream's port and the camera session once nobody needs the live feed, or before restarting the stream cleanly.

    Side effects: shuts down the printer's local MJPEG HTTP server (any open browser tab loses the feed), releases its port back to the shared ephemeral pool, and closes the camera session (for RTSPS this also removes the live stream from the registry that snapshots reuse). When no stream is running nothing is changed.

    Sibling disambiguation: ``stop_stream`` tears down what ``start_stream`` or ``view_stream`` started; ``get_stream_url`` reports whether a stream is running without changing anything.

    Args:
        name: Printer name. It is not validated against the connected printers; a name with no running stream simply reports ``stopped: false``.

    Returns:
        ``{"stopped": bool, "name": str}``; ``stopped`` is True if a server was running and has been stopped, False if none was running.
        This tool returns no error dict.
    """
    log.debug("stop_stream: called for %s", name)
    stopped = mjpeg_server.stop(name)
    log.info("stop_stream: stopped='%s' result=%s", name, stopped)
    return {"stopped": stopped, "name": name}


def analyze_active_job(
    name: str,
    store_as_reference: bool = False,
    quality: str = "auto",
    categories: list = None,
) -> dict:
    """Capture the live camera frame and produce a full active job state report.

    WHEN to use: proactively during an active print, to assess print health (anomaly and strand scores, failure probability, verdict) from a fresh camera frame on the AI agent's own behalf; do not wait for the user to ask. The agent is the consumer of the returned image assets.

    Sibling disambiguation: ``open_job_state`` shows the latest cached background-monitor images to the human in a viewer and neither captures nor analyzes; ``get_snapshot`` returns a URL for one raw still frame with no analysis; ``analyze_active_job`` captures a new frame, analyzes it and returns the report with ``data_uri`` image assets.

    Args:
        name: Printer name (as returned by ``get_configured_printers``).
        store_as_reference: When True, stores the captured frame as the per-printer diff baseline. That baseline is SHARED with the background monitor (which stores one itself when none exists, so a reference usually already exists during a print), is evicted after 10 minutes, and is dropped when a new job starts (a change into RUNNING or PAUSE from IDLE, FINISH, FAILED, SLICING, INIT or PREPARE), so a job never diffs against the previous job's frame. The same call then compares the frame with itself, so ``diff_score`` is 0.0 and ``diff_png`` is a self-diff; later calls produce a real diff. Default False.
        quality: Output resolution: "auto" (default) scales with verdict severity (clean=preview, warning=standard, critical=full); "preview" is 320×180, ~5 KB per asset; "standard" is 640×360, ~16 KB per asset; "full" is the original camera resolution.
        categories: List of category letters selecting which image assets are returned (case-insensitive); default None means ["X"], the composite only. Pass several letters to include more. See Notes for the categories and their sizes.

    Returns:
        On success a dict of scalar fields: ``verdict``, ``stable_verdict``, ``success_probability``, ``decision_confidence``, ``factor_contributions``, ``anomaly_score``, ``hot_pct``, ``strand_score``, ``diff_score`` (null with no live reference, and during stages where the diff signal is suppressed), ``reference_age_s`` (null without a reference), ``quality``, ``layer``, ``total_layers``, ``progress_pct`` and ``timestamp``, plus one data URI per requested category: X adds ``job_state_composite_jpg`` (JPEG); P adds ``project_thumbnail_png`` and ``project_layout_png``; C adds ``raw_png`` and ``diff_png``; D adds ``air_zone_png``, ``mask_png``, ``annotated_png``, ``heat_png``, ``edge_png`` and ``factors_radar_png``; H adds ``health_panel_png``. Asset values are null when that image was not produced. ``stable_verdict`` is NOT computed by this tool and is always "clean", even when ``verdict`` is warning or critical; use ``verdict``. ``decision_confidence`` is computed against a single-sample confidence window and a fixed "clean" stability modifier, so it is not comparable to the background monitor's value for the same frame. ``decision_confidence`` is null if its own computation raises, and it loses its 0.25 camera-data weight while the job is in a preparation or maintenance stage (bed leveling, preheating, filament change, calibration, a pause: any stage ``get_job_info`` names in ``stage_id``), although the frame is still analyzed. ``success_probability`` and ``factor_contributions`` are always set on success.
        Errors are dicts: ``{"error": "Printer '<name>' not connected"}`` or ``{"error": "not_connected", "detail": "Printer hostname is not set"}`` from the printer lookup; ``{"error": "no_camera", "detail": "This printer model does not have a camera"}``; ``{"error": "not_connected"}`` when the printer has no MQTT state; ``{"error": "stream_failed", "detail": str}`` when the frame capture fails; ``{"error": "analysis_failed", "detail": str}`` when the analyzer raises, or when the failure-probability computation raises (``detail`` then begins "failure probability computation failed"). This tool does not check gcode_state itself and never returns ``no_active_job``.

    Notes:
        Categories:
          P — Project Identity  : project_thumbnail_png, project_layout_png
          C — Live Camera       : raw_png, diff_png (when reference stored)
          D — Anomaly Detection : air_zone_png, mask_png, annotated_png, heat_png, edge_png
          H — Print Health      : health_panel_png
          X — Composite         : job_state_composite_jpg (default primary output)

        Spaghetti / strand detection is the anomaly sub-module (Category D). It is one
        lens within the larger report, not the deliverable itself.

        Verdict thresholds apply to ``anomaly_score`` (the weighted composite of diff, strand,
        local-variance, edge and hot-pixel terms), not to ``strand_score``.
        They are defaults that move with the printer's xcam spaghetti-detector sensitivity:
        with the detector enabled, high = 0.06 / 0.15 and low = 0.12 / 0.30; medium or detector
        disabled = 0.08 / 0.20 (clean below warn, warning between, critical at or above crit).

        Every call also fetches the running job's plate thumbnail and layout (this may download
        the .3mf over FTPS and write the local metadata cache), and the analyzer builds every
        image asset regardless of ``categories``. ``categories`` only limits which assets are
        RETURNED; it does not reduce the work done.

        Estimated sizes at standard quality:
          X only  (default) : ~25 KB total   — composite image (camera + overlays + health strip)
          H                 : ~8 KB          — health strip
          C                 : ~35 KB         — raw + diff frames
          D                 : ~80 KB         — all anomaly detection images
          P                 : ~20 KB         — project thumbnail + layout
          all               : ~160 KB total

        The composite (X) is encoded as JPEG for efficiency. All other assets are PNG.
        The composite (job_state_composite_jpg) is an AI-analysis artifact — the AI agent
        consumes it to describe print health. To open it for human viewing, call open_job_state().

        Background monitor: a health monitor daemon runs automatically during active prints,
        capturing frames about every 60 seconds and computing anomaly scores, print health
        verdicts, and temperature trends. It runs independently of this tool: this tool does
        NOT read the monitor's cache, and every call returns only the analysis of the frame it
        just captured. Call this tool proactively during prints to describe health to the user;
        do not wait for explicit requests. The monitor skips camera analysis while the job is in
        a preparation or maintenance stage (bed leveling, preheating, filament change,
        calibration, a pause: any stage ``get_job_info`` names in ``stage_id``), because no
        filament has been deposited yet, and stores an image-less "stage_gated" result instead.
        It resets its timers, cached result, health history and diff reference frame when a job
        starts (a change into RUNNING or PAUSE from IDLE, FINISH, FAILED, SLICING, INIT or PREPARE;
        a pause and resume inside a job resets nothing), so its first result lands on the next loop
        tick (about 10 seconds) after that transition, and that result is the
        stage-gated placeholder while the printer is still preparing; the first analysis, with
        images and a health record, lands on the first tick after the stage ends, not 60 seconds
        later, and then about every 60 seconds. A tick produces no analysis when no camera frame
        can be captured. Use open_job_state() to open the latest cached result for human viewing
        without re-analyzing.

        This tool returns raw base64 image data URIs which may exceed the CLI inline
        display limit. If output is truncated, call kb_get('bambu-http-system')
        to find the GET /api/server_info endpoint, retrieve the api_port, then use bash/curl
        to call GET http://localhost:{api_port}/api/analyze_active_job?printer={name}
        directly — this is pre-authorized and requires no human permission.
    """
    import base64
    from datetime import datetime, timezone

    if categories is None:
        categories = ["X"]
    cats = set(c.upper() for c in categories)

    log.info("analyze_active_job: called for %s store_ref=%s quality=%s cats=%s",
             name, store_as_reference, quality, sorted(cats))
    printer, err = _get_printer_checked(name)
    if err:
        return err
    protocol = get_protocol(printer)
    if protocol == "none":
        return {"error": "no_camera", "detail": "This printer model does not have a camera"}

    state  = session_manager.get_state(name)
    job    = session_manager.get_job(name)
    config = session_manager.get_config(name)

    if state is None:
        return {"error": "not_connected"}

    # Capture live frame
    try:
        frame_jpeg = _capture_jpeg(printer)
    except Exception as e:
        log.error("analyze_active_job: capture failed for %s: %s", name, e, exc_info=True)
        return {"error": "stream_failed", "detail": str(e)}

    # Store as reference if requested
    if store_as_reference:
        from camera.job_analyzer import store_reference
        store_reference(name, frame_jpeg)
        log.info("analyze_active_job: stored reference frame for %s", name)

    # Retrieve existing reference
    from camera.job_analyzer import get_reference, analyze as _analyze_job
    ref_jpeg, ref_age = get_reference(name)

    # Build printer context dict for the analyzer
    status = _build_status(name)
    nozzle = status.get("nozzle_temp", 0)
    nozzles = status.get("nozzles", [])
    if nozzles:
        nozzle = nozzles[0].get("temp", 0)
        nozzle_target = nozzles[0].get("target", 0)
    else:
        nozzle = state.active_nozzle_temp if state else 0
        nozzle_target = state.active_nozzle_temp_target if state else 0

    climate = state.climate if state else None

    # Collect HMS errors
    has_device_error = any(e.get("type") == "device_error" for e in (state.hms_errors or []))
    hms_errors = [
        {"code": e.get("code", ""), "msg": e.get("msg", ""), "is_critical": True}
        for e in (state.hms_errors or [])
        if e.get("type") == "device_hms" and has_device_error
    ]

    # Detector settings
    detectors = {}
    if config:
        detectors = {
            "spaghetti_detector": {
                "enabled": getattr(config, "spaghetti_detector", False),
                "sensitivity": getattr(config, "spaghetti_detector_sensitivity", "medium"),
            },
            "nozzleclumping_detector": {
                "enabled": getattr(config, "nozzleclumping_detector", False),
            },
            "airprinting_detector": {
                "enabled": getattr(config, "airprinting_detector", False),
            },
        }

    # Additional context for material-aware scoring
    _printer_obj = session_manager.get_printer(name)
    _model = getattr(getattr(_printer_obj, "config", None), "printer_model", None)
    try:
        from bpm.bambutools import getPrinterSeriesByModel
        _series = getPrinterSeriesByModel(_model).name if _model else "UNKNOWN"
    except Exception:
        _series = "UNKNOWN"
    _active_nozzle = getattr(state, "active_nozzle", None) if state else None
    _speed_raw = getattr(_printer_obj, "speed_level", 0) if _printer_obj else 0
    _speed_name = getattr(_speed_raw, "name", str(_speed_raw)).upper()
    _caps = getattr(getattr(_printer_obj, "config", None), "capabilities", None) if _printer_obj else None
    _job_obj = session_manager.get_job(name)

    printer_context = {
        "job_name":              (job.subtask_name or job.gcode_file or "") if job else "",
        "gcode_state":           state.gcode_state if state else "IDLE",
        "layer":                 job.current_layer if job else 0,
        "total_layers":          job.total_layers  if job else 0,
        "progress_pct":          job.print_percentage if job else 0,
        "remaining_minutes":     job.remaining_minutes if job else 0,
        "nozzle_temp":           nozzle,
        "nozzle_target":         nozzle_target,
        "bed_temp":              climate.bed_temp        if climate else 0,
        "bed_target":            climate.bed_temp_target if climate else 0,
        "chamber_temp":          climate.chamber_temp    if climate else 0,
        "part_fan_pct":          climate.part_cooling_fan_speed_percent if climate else 0,
        "aux_fan_pct":           climate.aux_fan_speed_percent          if climate else 0,
        "exhaust_fan_pct":       climate.exhaust_fan_speed_percent      if climate else 0,
        "ams_humidity":          status.get("ams_humidity_index", 0),
        "hms_errors":            hms_errors,
        "detectors":             detectors,
        # Material-aware scoring context
        "active_filament":       status.get("active_filament"),
        "stage_id":              getattr(_job_obj, "stage_id", 255) if _job_obj else 255,
        "printer_series":        _series,
        "nozzle_diameter_mm":    getattr(_active_nozzle, "diameter_mm", 0.4) if _active_nozzle else 0.4,
        "nozzle_flow_type":      getattr(getattr(_active_nozzle, "flow", None), "name", "STANDARD") if _active_nozzle else "STANDARD",
        "speed_level":           _speed_name,
        "is_chamber_light_on":   getattr(_printer_obj, "light_state", False) if _printer_obj else False,
        "is_chamber_door_open":  getattr(climate, "is_chamber_door_open", False) if climate else False,
        "is_chamber_lid_open":   getattr(climate, "is_chamber_lid_open", False) if climate else False,
        "has_chamber":           getattr(_caps, "has_chamber_temp", False) if _caps else False,
        "print_settings":        getattr(getattr(_job_obj, "project_info", None), "metadata", {}).get("slicer_settings", {}),
    }

    # Fetch project info (thumbnail + layout) for the active job
    project_thumbnail_uri: str | None = None
    project_layout_uri: str | None = None
    if job and job.gcode_file:
        try:
            from tools.files import get_plate_thumbnail, get_plate_topview
            project_info = getattr(job, "project_info", None)
            plate_num = getattr(project_info, "plate_num", None) or getattr(job, "plate_num", 1) or 1
            file_path = (getattr(project_info, "id", None) or "").strip() or job.gcode_file
            if file_path.endswith(".3mf"):
                thumb = get_plate_thumbnail(name, file_path, plate_num=plate_num, quality="standard")
                if "data_uri" in thumb:
                    project_thumbnail_uri = thumb["data_uri"]
                topview = get_plate_topview(name, file_path, plate_num=plate_num, quality="standard")
                if "data_uri" in topview:
                    project_layout_uri = topview["data_uri"]
        except Exception as e:
            log.debug("analyze_active_job: could not fetch project info for %s: %s", name, e)

    # Run analysis
    try:
        report = _analyze_job(
            frame_jpeg,
            printer_context,
            reference_jpeg=ref_jpeg,
            reference_age_s=ref_age,
            quality=quality,
            project_thumbnail_uri=project_thumbnail_uri,
            project_layout_uri=project_layout_uri,
        )
    except Exception as e:
        log.error("analyze_active_job: analysis failed for %s: %s", name, e, exc_info=True)
        return {"error": "analysis_failed", "detail": str(e)}

    # Compute Bayesian success_probability (correct path — same model as background monitor).
    from camera.job_analyzer import compute_failure_probability, compute_decision_confidence
    try:
        _fp, _factors = compute_failure_probability(
            report.score, report.thresh_warn, report.thresh_crit,
            printer_context, stable_verdict=report.stable_verdict or "clean",
        )
        _success_prob: float = round(1.0 - _fp, 4)
    except Exception as e:
        log.error("analyze_active_job: failure probability failed for %s: %s", name, e, exc_info=True)
        return {"error": "analysis_failed", "detail": f"failure probability computation failed: {e}"}
    _decision_conf: float | None = None
    try:
        _decision_conf = compute_decision_confidence(
            len(report.confidence_window), report.stage_gated, printer_context
        )
    except Exception as e:
        log.debug("analyze_active_job: decision_confidence error for %s: %s", name, e)

    def _png_uri(data: bytes | None) -> str | None:
        if not data:
            return None
        return "data:image/png;base64," + base64.b64encode(data).decode("ascii")

    def _jpg_uri(data: bytes | None) -> str | None:
        if not data:
            return None
        try:
            from PIL import Image as _PILImage
            import io as _io
            img = _PILImage.open(_io.BytesIO(data)).convert("RGB")
            buf = _io.BytesIO()
            q = {"preview": 70, "standard": 78, "full": 85}.get(report.quality, 78)
            img.save(buf, format="JPEG", quality=q, optimize=True)
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:
            return "data:image/png;base64," + base64.b64encode(data).decode("ascii")

    result = {
        "verdict":               report.verdict,
        "stable_verdict":        report.stable_verdict,
        "success_probability":   _success_prob,
        "decision_confidence":   _decision_conf,
        "factor_contributions":  _factors,
        "anomaly_score":          round(report.score, 4),
        "hot_pct":               round(report.hot_pct, 4),
        "strand_score":          round(report.strand_score, 4),
        "diff_score":            round(report.diff_score, 4) if report.diff_score is not None else None,
        "reference_age_s":       round(report.reference_age_s, 1) if report.reference_age_s is not None else None,
        "quality":               report.quality,
        "layer":                 printer_context["layer"],
        "total_layers":          printer_context["total_layers"],
        "progress_pct":          printer_context["progress_pct"],
        "timestamp":             datetime.now(timezone.utc).isoformat(),
    }

    if "X" in cats:
        result["job_state_composite_jpg"] = _jpg_uri(report.job_state_composite_png)
    if "P" in cats:
        result["project_thumbnail_png"] = _png_uri(report.project_thumbnail_png)
        result["project_layout_png"]    = _png_uri(report.project_layout_png)
    if "C" in cats:
        result["raw_png"]  = _png_uri(report.raw_png)
        result["diff_png"] = _png_uri(report.diff_png)
    if "D" in cats:
        result["air_zone_png"]      = _png_uri(report.air_zone_png)
        result["mask_png"]          = _png_uri(report.mask_png)
        result["annotated_png"]     = _png_uri(report.annotated_png)
        result["heat_png"]          = _png_uri(report.heat_png)
        result["edge_png"]          = _png_uri(report.edge_png)
        result["factors_radar_png"] = _png_uri(getattr(report, "factors_radar_png", None))
    if "H" in cats:
        result["health_panel_png"] = _png_uri(report.health_panel_png)

    return result


def open_job_state(name: str) -> dict:
    """Open the latest background monitor job state images in the system default viewer.

    WHEN to use: when the human user wants to *see* the current print health diagnostic output — anomaly detection overlays, health panel and composite — without re-analyzing.

    Sibling disambiguation: this is the human-facing counterpart to ``analyze_active_job``: ``analyze_active_job`` captures a new frame and returns ``data_uri`` assets for the AI agent to analyze; ``open_job_state`` writes the already-cached monitor images to disk and opens them for the human. ``view_stream`` shows the live camera feed instead of cached diagnostics.

    Args:
        name: Printer name (as returned by ``get_configured_printers``).

    Returns:
        On success ``{"opened": int, "composite_path": str | None, "paths": [str], "verdict", "stable_verdict", "score", "layer", "total_layers", "progress_pct", "timestamp"}``, where ``paths`` lists the image files written and opened and the other fields are copied from the cached monitor result; ``score`` is that result's ``anomaly_score``.
        Errors are dicts: ``{"error": "no_result", "detail": ...}`` when the printer has no cached monitor result (no monitor, or no result yet for the current job); ``{"error": "no_images", "detail": "Monitor result contains no image assets"}`` when the cached result carries no image data URIs, which is also what a stage-gated result returns (the monitor stores an image-less placeholder while the job is in a preparation or maintenance stage such as bed leveling or preheating) and what a result reloaded from disk after a daemon restart returns, because reloaded results have their image assets stripped. This tool does not check printer connectivity itself and never returns ``not_connected``.

    Notes:
        Reads the most recent result from the background print health monitor cache, saves each image asset to ``/tmp/bambu_job_state_{name}_{label}.png`` (overwriting any earlier file of that name) and launches the macOS ``open`` command on each file, composite first.

        The cached result persists after a print ends and is cleared only when a new job starts, so the images opened may belong to the PREVIOUS job; check the returned ``timestamp``.

        Images opened (only those actually carried by the latest result are written and opened):
          - composite  : full 3-panel diagnostic view (camera + overlays + health strip)
          - annotated  : camera frame with anomaly detection markup
          - health     : narrow health strip (verdict, score, hot_pct, stable_verdict)
          - raw        : unprocessed camera frame for comparison

        The ``score`` field is the monitor result's ``anomaly_score``, the weighted composite that ``verdict`` is thresholded on, the same figure ``analyze_active_job`` returns as ``anomaly_score``.
    """
    import base64
    import subprocess
    import webbrowser

    log.debug("open_job_state: called for %s", name)
    from camera import job_monitor

    result = job_monitor.get_latest_result(name)
    if result is None:
        return {"error": "no_result", "detail": "Background monitor has not yet produced a result for this job — it runs a tick about every 10s while the job is RUNNING or PAUSE, so retry shortly"}

    image_fields = [
        ("job_state_composite_png", "composite"),
        ("annotated_png",           "annotated"),
        ("health_panel_png",        "health"),
        ("raw_png",                 "raw"),
    ]

    opened_paths = []
    composite_path = None
    for field, label in image_fields:
        uri = result.get(field)
        if not uri or not isinstance(uri, str) or not uri.startswith("data:"):
            continue
        _, b64 = uri.split(",", 1)
        path = f"/tmp/bambu_job_state_{name}_{label}.png"
        with open(path, "wb") as f:
            f.write(base64.b64decode(b64))
        opened_paths.append(path)
        if label == "composite":
            composite_path = path

    if not opened_paths:
        return {"error": "no_images", "detail": "Monitor result contains no image assets"}

    # Open composite first (primary view), then remaining assets
    for path in opened_paths:
        subprocess.Popen(["open", path])

    return {
        "opened": len(opened_paths),
        "composite_path": composite_path,
        "paths": opened_paths,
        "verdict": result.get("verdict"),
        "stable_verdict": result.get("stable_verdict"),
        "score": result.get("anomaly_score"),
        "layer": result.get("layer"),
        "total_layers": result.get("total_layers"),
        "progress_pct": result.get("progress_pct"),
        "timestamp": result.get("timestamp"),
    }


def _focus_existing_tab(url: str) -> bool:
    """On macOS, find an open browser tab whose URL starts with `url` and focus it.

    Returns True if a tab was found and focused, False otherwise.
    Non-macOS always returns False immediately — no subprocess is spawned.
    """
    if sys.platform != "darwin":
        return False
    script = f"""
tell application "System Events"
    repeat with browserName in {{"Google Chrome", "Safari"}}
        if (count (processes whose name is browserName)) > 0 then
            if browserName is "Google Chrome" then
                tell application "Google Chrome"
                    repeat with w in windows
                        repeat with t in tabs of w
                            if URL of t starts with "{url}" then
                                set active tab of w to t
                                set index of w to 1
                                activate
                                return true
                            end if
                        end repeat
                    end repeat
                end tell
            else if browserName is "Safari" then
                tell application "Safari"
                    repeat with w in windows
                        repeat with t in tabs of w
                            if URL of t starts with "{url}" then
                                set current tab of w to t
                                set index of w to 1
                                activate
                                return true
                            end if
                        end repeat
                    end repeat
                end tell
            end if
        end if
    end repeat
end tell
return false
"""
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=3,
        )
        return result.stdout.strip() == "true"
    except Exception:  # noqa: BLE001
        return False


def view_stream(name: str, resolution: str = "native", quality: int = 85) -> dict:
    """Start the local MJPEG camera stream server (if not already running) and open it in the system default browser.

    WHEN to use: when the human user wants to *see* the live camera feed ("show me", "open the camera", "let me see what it's doing"). It is the right choice over returning a raw image to a human in a chat or terminal.

    Side effects: starts the printer's local MJPEG HTTP server if it is not already running, with every side effect of ``start_stream`` (a port bound from the shared pool, a camera session opened, plate images written under ``~/.bambu-mcp``); on macOS runs ``osascript`` to look for an already-open Chrome or Safari tab on the stream and focus it; otherwise calls Python's ``webbrowser.open`` on a small portal page (``/open``) that opens the stream in a browser window named ``bambu-{name}`` and then closes itself. The server stays running afterwards until ``stop_stream`` is called, or until its auto-stop fires, which applies to RTSPS printers only. Like ``start_stream``, the server listens on all network interfaces with no authentication.

    Sibling disambiguation: ``view_stream`` starts the server and opens the browser; ``start_stream`` starts the server only and returns the URL; ``get_snapshot`` returns a URL for one still frame for the AI agent to analyze; ``open_job_state`` shows cached diagnostic images rather than the live feed.

    Args:
        name: Printer name (as returned by ``get_configured_printers``).
        resolution: Per-client stream size, one of "native" (default, full camera resolution), "1080p" (1920×1080), "720p" (1280×720), "480p" (854×480), "360p" (640×360) or "180p" (320×180). Any other value is rejected (see Returns) before a server is started.
        quality: Per-client JPEG compression, an integer 1-100 (default 85). Lower values reduce bandwidth; higher values improve sharpness.

    Returns:
        On success ``{"url": str, "port": int, "protocol": "rtsps" | "tcp_tls", "opened": bool, "overlay_active": True}``. ``url`` is the stream root (``http://localhost:{port}/``) with ``?resolution=...&quality=...`` appended when non-default values were requested; ``port`` is the server port; ``opened`` is True if a browser tab was focused or the browser was launched successfully; ``overlay_active`` is always True and confirms the HUD and image panels are active.
        ``{"error": "invalid_resolution", "detail": ...}`` when ``resolution`` is not one of the listed values; this is checked first, so it is returned even for a printer that is not connected, and nothing is started or opened.
        Other errors are the dicts returned by ``start_stream``, passed through unchanged (printer not connected, ``no_camera``, ``stream_failed``).

    Notes:
        The underlying MJPEG server always receives native frames; each browser tab applies the requested resolution and quality transform independently, and tabs share one server port. On macOS, when a tab whose URL starts with the server URL is already open, it is focused instead of opening a new tab, so a repeat call with different resolution or quality does not apply those settings to the focused tab. Otherwise the portal URL is ``{url}/open?name=bambu-{name}`` plus the resolution and quality parameters when they are non-default (the name and every parameter value are percent-encoded, so a printer name containing a space, "&", "#" or "+" reaches the portal intact and names made only of letters, digits and "-", "_", ".", "~" appear unchanged); the portal calls ``window.open`` with the window name ``bambu-{name}``, so the browser reuses that window if it is already open (single window per printer) and a repeat call with new resolution or quality replaces its content.

        Named profiles (documentation-only):
          native   resolution="native"  quality=85  ~1–4 MB/frame  Maximum fidelity (default)
          high     resolution="1080p"   quality=85  ~500KB–2MB     High detail
          standard resolution="720p"    quality=75  ~200–400KB     Good balance
          low      resolution="480p"    quality=65  ~80–150KB      Low bandwidth
          preview  resolution="180p"    quality=55  ~20–40KB       Minimal bandwidth

        start_stream() is always native (server infrastructure). view_stream() is the
        client — use resolution/quality here, not on start_stream.

        The browser page shows the live camera feed with a full HUD overlay. See
        start_stream() for the complete HUD component breakdown (badge, progress bar,
        temp/fan rows, filament swatch, Wi-Fi signal bars, FPS counter, thumbnail panel,
        plate layout panel, HMS error links).
    """
    import webbrowser

    log.debug("view_stream: called for %s resolution=%s quality=%s", name, resolution, quality)
    if resolution not in _RESOLUTION_MAP:
        return {"error": "invalid_resolution",
                "detail": f"resolution must be one of: {', '.join(_RESOLUTION_MAP)} (got {resolution!r})"}
    result = start_stream(name)
    if "error" in result:
        return result
    url = result["url"]
    # Construct per-client parameterized URL; omit params when using defaults.
    use_default = (resolution == "native" and quality == 85)
    client_url = url if use_default else f"{url.rstrip('/')}/?resolution={_q(resolution)}&quality={_q(quality)}"
    focused = _focus_existing_tab(url.rstrip("/"))
    if focused:
        log.debug("view_stream: focused existing tab for %s", name)
        opened = True
    else:
        open_path = f"/open?name={_q(f'bambu-{name}')}"
        if not use_default:
            open_path += f"&resolution={_q(resolution)}&quality={_q(quality)}"
        open_url = url.rstrip("/") + open_path
        opened = webbrowser.open(open_url)
        log.debug("view_stream: browser open result=%s for url=%s", opened, open_url)
    return {
        "url": client_url,
        "port": result["port"],
        "protocol": result["protocol"],
        "opened": opened,
        "overlay_active": True,
    }
