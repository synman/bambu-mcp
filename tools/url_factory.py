"""
URL factory: MCP tools that return HTTP endpoint URLs instead of large payloads.

For tools where P(overflow) is high — native-resolution camera snapshots during active
prints (~4 MB), raw telemetry time-series (~280K chars) — this module registers the MCP
tool and returns a local HTTP URL rather than the payload itself.

The agent fetches the URL immediately via bash/curl (pre-authorized, no human permission
needed — same authorization level as HTTP escalation: local GET, read-only).

Why: these tools reliably exhaust the MCP token budget under normal use. Returning a URL
eliminates the failed MCP round trip and collapses two round trips to one.

Original tool implementations remain in their source modules and serve the HTTP API layer.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _api_base() -> str:
    """Return the local HTTP API base URL using the currently bound port."""
    try:
        import api_server
        port = api_server.get_port()
        return f"http://localhost:{port}/api"
    except Exception:
        return "http://localhost:49152/api"


def get_snapshot(name: str, resolution: str = "native", quality: int = 85, include_status: bool = False) -> dict:
    """
    Return a single still frame from the printer camera.

    Returns a URL to fetch the frame via HTTP — do not call MCP recursively.
    Fetch immediately via bash: `curl -s "$url"` — pre-authorized, no permission needed.

    This tool returns a URL instead of embedding the frame because native-resolution
    snapshots during active prints reach ~4 MB (well above the MCP token budget).

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

    include_status=True adds a "status" key with live print telemetry in the HTTP response.

    The HTTP response shape is identical to the prior direct return:
      data_uri, width, height, resolution, quality, protocol, timestamp, [status]

    Returns {"url": "http://localhost:{port}/api/snapshot?printer=...&..."}
    Returns {"error": "not_connected"} if the printer MQTT session is not active.
    Returns {"error": "no_camera"} if this printer model has no camera (HTTP response).
    """
    log.debug("get_snapshot (url_factory): name=%s resolution=%s quality=%d", name, resolution, quality)
    from session_manager import session_manager
    if session_manager.get_printer(name) is None:
        return {"error": "not_connected"}
    base = _api_base()
    include_str = "true" if include_status else "false"
    url = f"{base}/snapshot?printer={name}&resolution={resolution}&quality={quality}&include_status={include_str}"
    log.debug("get_snapshot (url_factory): url=%s", url)
    return {"url": url}


def get_monitoring_data(name: str) -> dict:
    """
    Return telemetry history for charting: temperature and fan speed time-series.

    Returns a URL to fetch the data via HTTP — the raw payload (~280K chars) exhausts
    the MCP token budget. Fetch immediately via bash: `curl -s "$url"` — pre-authorized.

    Data is provided as rolling 60-minute collections sampled every ~2.5 seconds.
    Also includes gcode_state_durations (time spent in each print state per job).

    Note on gcode_state_durations: a FAILED entry does not mean the current job failed.
    The rolling window captures the prior job's terminal state before the current job
    started. A print that has been RUNNING continuously will show a small FAILED duration
    from the previous job alongside its dominant RUNNING duration.

    Response may be gzip+base64 compressed if the payload is large. Decompress:
      import gzip, json, base64
      data = json.loads(gzip.decompress(base64.b64decode(r["data"])))
    No HTTP fallback route exists for this tool. If the response exceeds the MCP
    limit, use get_monitoring_series(name, field) to fetch individual fields instead.

    Returns {"url": "http://localhost:{port}/api/monitoring_data?printer=..."}
    Returns {"error": "not_connected"} if the printer MQTT session is not active.
    """
    log.debug("get_monitoring_data (url_factory): name=%s", name)
    from session_manager import session_manager
    if session_manager.get_printer(name) is None:
        return {"error": "not_connected"}
    base = _api_base()
    url = f"{base}/monitoring_data?printer={name}"
    log.debug("get_monitoring_data (url_factory): url=%s", url)
    return {"url": url}


def get_monitoring_history(name: str, raw: bool = False) -> dict:
    """
    Return telemetry history for charting: temperature and fan speed time-series.

    Returns a URL to fetch the data via HTTP — raw=True payload (~280K chars) can exhaust
    the MCP token budget. Fetch immediately via bash: `curl -s "$url"` — pre-authorized.

    When raw=False (default), returns a lightweight summary with {min, max, avg,
    last, count} statistics for each field, plus gcode_state_durations. Use this
    for a quick overview of thermal and fan activity without transferring the full
    time-series.

    When raw=True, returns the complete rolling 60-minute time-series for all 8
    fields (~1440 data points each). Use raw=True only when you need precise
    charting data. For a single field, prefer get_monitoring_series() instead.

    Data is sampled every ~2.5 seconds. Fields: tool, tool_1 (H2D second nozzle),
    bed, chamber, part_fan, aux_fan, exhaust_fan, heatbreak_fan.

    Also includes gcode_state_durations (time spent in each print state per job).

    Note on gcode_state_durations: a FAILED entry does not mean the current job failed.
    The rolling window captures the prior job's terminal state before the current job
    started. A print that has been RUNNING continuously will show a small FAILED duration
    from the previous job alongside its dominant RUNNING duration.

    Returns {"url": "http://localhost:{port}/api/monitoring_history?printer=...&raw=..."}
    Returns {"error": "not_connected"} if the printer MQTT session is not active.
    """
    log.debug("get_monitoring_history (url_factory): name=%s raw=%s", name, raw)
    from session_manager import session_manager
    if session_manager.get_printer(name) is None:
        return {"error": "not_connected"}
    base = _api_base()
    raw_str = "true" if raw else "false"
    url = f"{base}/monitoring_history?printer={name}&raw={raw_str}"
    log.debug("get_monitoring_history (url_factory): url=%s", url)
    return {"url": url}


def get_monitoring_series(name: str, field: str) -> dict:
    """
    Return the full time-series for a single telemetry field.

    Returns a URL to fetch the data via HTTP — the time-series payload (~120K chars)
    consumes 40%+ of the MCP token budget. Fetch immediately via bash:
    `curl -s "$url"` — pre-authorized, no permission needed.

    field must be one of: tool, tool_1, bed, chamber, part_fan, aux_fan,
    exhaust_fan, heatbreak_fan.

    Returns the complete rolling 60-minute data for that field only (~1440 points,
    ~22 KB compressed). Use this instead of get_monitoring_history(raw=True) when
    you only need one metric — it avoids transferring all 8 series at once.

    Call get_monitoring_history() first (default raw=False) to see the summary
    for all fields, then call this for the specific field(s) you want to chart.

    Returns {"url": "http://localhost:{port}/api/monitoring_series?printer=...&field=..."}
    Returns {"error": "not_connected"} if the printer MQTT session is not active.
    """
    log.debug("get_monitoring_series (url_factory): name=%s field=%s", name, field)
    from session_manager import session_manager
    if session_manager.get_printer(name) is None:
        return {"error": "not_connected"}
    base = _api_base()
    url = f"{base}/monitoring_series?printer={name}&field={field}"
    log.debug("get_monitoring_series (url_factory): url=%s", url)
    return {"url": url}
