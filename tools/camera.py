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
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
from datetime import datetime, timezone

log = logging.getLogger(__name__)

from session_manager import session_manager
from camera.mjpeg_server import mjpeg_server
from camera.protocol import get_protocol, get_rtsps_url


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
        active_tray_id = getattr(state, "active_tray_id", -1)
        active_spool = next(
            (s for s in (state.spools or []) if s.id == active_tray_id),
            None
        ) if active_tray_id not in (-1, 255) else None
        active_filament = None
        if active_spool:
            color = active_spool.color or ""
            # Normalise bare 6-char hex to #RRGGBB
            if color and not color.startswith("#") and len(color) == 6 and all(
                c in "0123456789abcdefABCDEF" for c in color
            ):
                color = "#" + color
            active_filament = {
                "type": active_spool.type or "",
                "color": color,
                "remaining_pct": active_spool.remaining_percent,
            }

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
        log.debug("_make_stream_session: → session type=%s", type(session).__name__)
        return session, None
    if protocol == "tcp_tls":
        from camera.tcp_stream import TCPFrameBuffer
        buf = TCPFrameBuffer(ip, access_code)
        log.info("_make_stream_session: waiting for first TCP frame from %s", ip)
        buf.wait_first_frame(timeout=30.0)
        log.debug("_make_stream_session: → session type=%s", type(buf).__name__)
        return buf, buf.close
    raise ValueError("No camera protocol for this printer model")


def get_snapshot(name: str, quality: str = "standard", include_status: bool = False) -> dict:
    """
    Return a single still frame from the printer camera.

    Captures one JPEG frame and returns it as a base64-encoded data URI suitable for
    direct display. Does not start or stop a background streaming server.

    quality controls image size and JPEG compression:
      "preview"  — ~5 KB  (320×180, JPEG q=65)  — quick overview
      "standard" — ~16 KB (640×360, JPEG q=75)  — default, renders cleanly inline
      "full"     — ~71 KB (original resolution)  — maximum detail

    include_status=True adds a "status" key with live print telemetry (gcode_state,
    progress, temperatures, fan speeds, etc.) — the same status dict available in
    the camera overlay when streaming via start_stream().

    Returns:
      data_uri  — complete data:image/jpeg;base64,... string (embed as Markdown image directly)
      width     — frame width in pixels
      height    — frame height in pixels
      quality   — the quality tier used
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
    """
    log.info("get_snapshot: called for %s quality=%s include_status=%s", name, quality, include_status)
    printer, err = _get_printer_checked(name)
    if err:
        return err
    protocol = get_protocol(printer)
    log.debug("get_snapshot: protocol=%s for %s", protocol, name)
    if protocol == "none":
        log.warning("get_snapshot: no camera for %s (protocol=none)", name)
        return {"error": "no_camera", "detail": "This printer model does not have a camera"}
    try:
        from tools._response import resize_image_to_tier
        jpeg = _capture_jpeg(printer)
        jpeg_out, width, height = resize_image_to_tier(jpeg, quality)
        data_uri = "data:image/jpeg;base64," + base64.b64encode(jpeg_out).decode("ascii")
        log.debug("get_snapshot: → width=%d height=%d quality=%s protocol=%s bytes=%d", width, height, quality, protocol, len(jpeg_out))
        tmp_path = os.path.join(tempfile.gettempdir(), f"bambu_snap_{name}_{quality}.jpg")
        with open(tmp_path, "wb") as f:
            f.write(jpeg_out)
        log.info("get_snapshot: saved %dx%d %s snapshot to %s", width, height, quality, tmp_path)
        result = {
            "data_uri": data_uri,
            "saved_path": tmp_path,
            "width": width,
            "height": height,
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
    """
    Return camera stream URL information without starting a server or connecting to the camera.

    Returns:
      protocol        — "rtsps", "tcp_tls", or "none"
      rtsps_url       — RTSPS URL for X1/H2D printers (password redacted; open with VLC/ffplay), or null
      local_mjpeg_url — URL of running local MJPEG server, or null if not started
      streaming       — bool: whether a local MJPEG server is currently active
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
    """
    Start a local MJPEG HTTP server for this printer's camera feed.

    Connects to the printer camera and begins serving Motion JPEG frames at a local
    HTTP URL. Any web browser can open this URL to watch the live feed.

    If a stream is already running for this printer, returns the existing server URL
    without starting a duplicate.

    The served page includes a live HUD overlay with the following components:

    Top-left HUD panel (dark semi-transparent, polls /status every 2 s):
      - Badge row: state badge (IDLE/RUNNING/PAUSE/FINISH/FAILED — color-coded) +
        speed badge (Quiet/Standard/Sport/Ludicrous — shown only while active)
      - Subtask line: job/file name, truncated with ellipsis
      - Progress bar: thin 3-px bar, color tracks state
      - Rows (label + value pairs): Stage, Layers (current/total), Elapsed, Remaining
      - Temps section: nozzle temp(s) (°C / target), bed temp, chamber temp
      - Fans section: part cooling %, aux %, exhaust %
      - Filament swatch: colored dot + type label for the active spool
      - AMS humidity index
      - Wi-Fi signal bars (unicode block chars, color-tiered by strength)
      - HMS error links (clickable, open Bambu error page in popup)

    Top-right FPS counter:
      - Numeric FPS readout + 5-column animated bar graph (green/amber/red by rate)

    Bottom image panels (appear when a print job is active):
      - Thumbnail panel (bottom-left): isometric 3D render of the current job
      - Layout panel (bottom-right): annotated top-down plate layout image

    Args:
      name — printer name
      port — optional preferred port; defaults to next available port in the shared
             ephemeral pool (IANA RFC 6335 range 49152–49251 by default)

    Returns:
      url      — http://localhost:{port}/ — open in any browser to watch live
      port     — the allocated port number
      protocol — "rtsps" or "tcp_tls"
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

        def _get_images(n=name):
            """Return cached (thumbnail_bytes, layout_bytes), regenerating on job change."""
            import re, base64, json as _json, dataclasses
            from enum import Enum
            from bpm.bambuproject import get_project_info as _get_project_info
            from tools.files import _build_layout_uri

            job = session_manager.get_job(n)
            if job is None or not job.subtask_name:
                return None, None

            # Derive the SD-card 3MF path from subtask_name (e.g. "H2D H2S main riser 2025-9-19"
            # → "/_jobs/H2D H2S main riser 2025-9-19.gcode.3mf") and plate_num from the
            # gcode_file path (e.g. "/data/Metadata/plate_3.gcode" → 3).
            tmf_path = f"/_jobs/{job.subtask_name}.gcode.3mf"
            m = re.search(r"plate_(\d+)", job.gcode_file or "")
            plate_num = int(m.group(1)) if m else 1

            cache_key = (tmf_path, plate_num)
            if _img_cache["key"] == cache_key and _img_cache["thumbnail"] is not None:
                return _img_cache["thumbnail"], _img_cache["layout"]

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

                _img_cache["key"] = cache_key
                _img_cache["thumbnail"] = thumb_bytes
                _img_cache["layout"] = layout_bytes
                return thumb_bytes, layout_bytes
            except Exception as _e:
                log.warning("_get_images: error fetching thumbnail/layout: %s", _e, exc_info=True)
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
                                 fps_cap=fps_cap)
        allocated_port = int(url.split(":")[-1].rstrip("/"))
        log.info("start_stream: server started for '%s' at %s protocol=%s", name, url, protocol)
        return {"url": url, "port": allocated_port, "protocol": protocol}
    except Exception as e:
        log.error("start_stream: exception: %s", e, exc_info=True)
        return {"error": "stream_failed", "detail": str(e)}


def stop_stream(name: str) -> dict:
    """
    Stop the local MJPEG HTTP server for this printer and disconnect from the camera.

    Returns:
      stopped — bool: True if a server was running and has been stopped
      name    — the printer name
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
    """
    Capture the live camera frame and produce a full active job state report.

    Returns a cohesive suite of digital assets representing every meaningful
    dimension of the active print job:

    Categories:
      P — Project Identity  : project_thumbnail_png, project_layout_png
      C — Live Camera       : raw_png, diff_png (when reference stored)
      D — Anomaly Detection : air_zone_png, mask_png, annotated_png, heat_png, edge_png
      H — Print Health      : health_panel_png
      X — Composite         : job_state_composite_png (default primary output)

    Spaghetti / strand detection is the anomaly sub-module (Category D). It is one
    lens within the larger report, not the deliverable itself.

    Spaghetti score thresholds (Obico-derived + xcam tier mapping):
      clean   < 0.08
      warning   0.08 – 0.20
      critical ≥ 0.20

    store_as_reference=True stores the current frame as the diff baseline for this
    printer. On subsequent calls the diff assets (diff_png, Category C2) become active.

    quality controls output resolution:
      "auto"     — scales with verdict severity (clean=preview, warning=standard, critical=full)
      "preview"  — 320×180, ~5 KB per asset
      "standard" — 640×360, ~16 KB per asset
      "full"     — original camera resolution

    categories controls which image assets are returned (default: ["X"] — composite only).
    Pass multiple letters to include more assets. Estimated sizes at standard quality:
      X only  (default) : ~25 KB total   — composite JPEG dashboard
      H                 : ~8 KB          — health strip
      C                 : ~35 KB         — raw + diff frames
      D                 : ~80 KB         — all anomaly detection images
      P                 : ~20 KB         — project thumbnail + layout
      all               : ~160 KB total

    The composite (X) is encoded as JPEG for efficiency. All other assets are PNG.
    The composite dashboard (job_state_composite_jpg) is the primary output for
    quick inspection; individual category assets are included for detailed analysis.

    Returns {"error": "no_camera"} if this printer has no camera.
    Returns {"error": "not_connected"} if the MQTT session is not active.
    Returns {"error": "no_active_job"} if the printer is idle (gcode_state IDLE/FINISH).
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

    printer_context = {
        "job_name":          (job.subtask_name or job.gcode_file or "") if job else "",
        "gcode_state":       state.gcode_state if state else "IDLE",
        "layer":             job.current_layer if job else 0,
        "total_layers":      job.total_layers  if job else 0,
        "progress_pct":      job.print_percentage if job else 0,
        "remaining_minutes": job.remaining_minutes if job else 0,
        "nozzle_temp":       nozzle,
        "nozzle_target":     nozzle_target,
        "bed_temp":          climate.bed_temp        if climate else 0,
        "bed_target":        climate.bed_temp_target if climate else 0,
        "chamber_temp":      climate.chamber_temp    if climate else 0,
        "part_fan_pct":      climate.part_cooling_fan_speed_percent if climate else 0,
        "aux_fan_pct":       climate.aux_fan_speed_percent          if climate else 0,
        "exhaust_fan_pct":   climate.exhaust_fan_speed_percent      if climate else 0,
        "ams_humidity":      status.get("ams_humidity_index", 0),
        "hms_errors":        hms_errors,
        "detectors":         detectors,
    }

    # Fetch project info (thumbnail + layout) for the active job
    project_thumbnail_uri: str | None = None
    project_layout_uri: str | None = None
    if job and job.gcode_file:
        try:
            from tools.files import get_current_job_project_info, get_plate_thumbnail, get_plate_topview
            pinfo = get_current_job_project_info(name)
            if "error" not in pinfo:
                plate_num = getattr(job, "plate_number", 1) or 1
                thumb = get_plate_thumbnail(name, job.gcode_file, plate_num=plate_num, quality="standard")
                if "data_uri" in thumb:
                    project_thumbnail_uri = thumb["data_uri"]
                topview = get_plate_topview(name, job.gcode_file, plate_num=plate_num, quality="standard")
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
        "score":                 round(report.score, 4),
        "hot_pct":               round(report.hot_pct, 4),
        "strand_score":          round(report.strand_score, 4),
        "edge_density":          round(report.edge_density, 4),
        "diff_score":            round(report.diff_score, 4) if report.diff_score is not None else None,
        "reference_age_s":       round(report.reference_age_s, 1) if report.reference_age_s is not None else None,
        "quality":               report.quality,
        "yolo_available":        report.yolo_available,
        "yolo_boost":            round(report.yolo_boost, 4),
        "yolo_detections":       report.yolo_detections,
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
        result["air_zone_png"]  = _png_uri(report.air_zone_png)
        result["mask_png"]      = _png_uri(report.mask_png)
        result["annotated_png"] = _png_uri(report.annotated_png)
        result["heat_png"]      = _png_uri(report.heat_png)
        result["edge_png"]      = _png_uri(report.edge_png)
    if "H" in cats:
        result["health_panel_png"] = _png_uri(report.health_panel_png)

    return result


def view_stream(name: str) -> dict:
    """
    Start the local MJPEG camera stream server (if not already running) and open it
    in the system default browser.

    Uses Python's webbrowser.open() which delegates to the OS default browser — works
    on macOS, Linux, and Windows without any extra dependencies.

    The browser page shows the live camera feed with a full HUD overlay. See
    start_stream() for the complete HUD component breakdown (badge, progress bar,
    temp/fan rows, filament swatch, Wi-Fi signal bars, FPS counter, thumbnail panel,
    plate layout panel, HMS error links).

    Returns:
      url            — the local MJPEG server URL that was opened
      port           — the server port
      protocol       — "rtsps" or "tcp_tls"
      opened         — bool: True if the browser was launched successfully
      overlay_active — always True; confirms HUD + image panels are active
    """
    import webbrowser

    log.debug("view_stream: called for %s", name)
    result = start_stream(name)
    if "error" in result:
        return result
    url = result["url"]
    opened = webbrowser.open(url)
    log.debug("view_stream: browser open result=%s for url=%s", opened, url)
    return {
        "url": url,
        "port": result["port"],
        "protocol": result["protocol"],
        "opened": opened,
        "overlay_active": True,
    }
