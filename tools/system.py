"""
tools/system.py — System and session management tools for Bambu Lab printers.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

from session_manager import session_manager
from data_collector import data_collector


def _no_printer(name: str) -> dict:
    return {"error": f"Printer '{name}' not connected"}


def _permission_denied() -> str:
    return "Error: user_permission must be True to perform this action."


def get_server_info() -> dict:
    """
    Return runtime port pool state for the bambu-mcp server.

    The bambu-mcp HTTP REST API and all MJPEG camera stream servers draw ports from
    a shared ephemeral pool anchored at port 49152 (IANA RFC 6335 Dynamic/Private range
    49152–65535).  Ports are allocated on demand and released when listeners stop.

    Use this tool to discover the actual REST API port at runtime before constructing
    an HTTP request URL.  The REST API base URL is: http://localhost:{api_port}/api

    Returns:
        api_port      — TCP port the REST API is currently bound to (0 if not running)
        api_url       — convenience base URL: http://localhost:{api_port}/api
        pool_start    — first port in the shared ephemeral pool (default 49152)
        pool_end      — last port in the shared ephemeral pool inclusive (default 49251)
        pool_size     — total number of ports in the pool (pool_end - pool_start + 1)
        pool_available — number of unclaimed ports remaining in the pool
        pool_claimed  — sorted list of all currently claimed port numbers
                        (includes the REST API port + all active MJPEG stream ports)
        stream_count  — number of active MJPEG camera streams
        streams       — dict of {printer_name: {port, url}} for each active stream

    Environment variables that control the pool:
        BAMBU_PORT_POOL_START  — override pool start (default 49152)
        BAMBU_PORT_POOL_END    — override pool end (default 49251)
        BAMBU_API_PORT         — preferred port for the REST API (tried first; rotates
                                 to next available pool port if taken)

    Example — construct the REST API base URL:
        info = get_server_info()
        base_url = f"http://localhost:{info['api_port']}/api"
    """
    log.debug("get_server_info: called")
    try:
        import api_server
        from port_pool import port_pool as _pp
        from camera.mjpeg_server import mjpeg_server as _mjs
        api_port = api_server.get_port()
        state = _pp.get_state()
        streams = _mjs.get_active_streams()
        pool_size = state["pool_end"] - state["pool_start"] + 1
        result = {
            "api_port":       api_port,
            "api_url":        f"http://localhost:{api_port}/api",
            "pool_start":     state["pool_start"],
            "pool_end":       state["pool_end"],
            "pool_size":      pool_size,
            "pool_available": pool_size - len(state["pool_claimed"]),
            "pool_claimed":   state["pool_claimed"],
            "stream_count":   len(streams),
            "streams":        streams,
        }
        log.debug("get_server_info: → %s", result)
        return result
    except Exception as e:
        log.error("get_server_info: error: %s", e, exc_info=True)
        return {"error": f"Error retrieving server info: {e}"}


def get_session_status(name: str) -> dict:
    """
    Return the current MQTT session state and connectivity info for the named printer.

    Includes whether the session is connected, the service state enum name, and
    whether a live BambuPrinter instance exists for this printer name.
    """
    log.debug("get_session_status: called for name=%s", name)
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("get_session_status: printer %s not connected", name)
        return _no_printer(name)
    try:
        is_connected = session_manager.is_connected(name)
        service_state = printer.service_state
        result = {
            "name": name,
            "connected": is_connected,
            "service_state": service_state.name if hasattr(service_state, "name") else str(service_state),
            "session_active": True,
        }
        log.debug("get_session_status: returning result for %s", name)
        return result
    except Exception as e:
        log.error("get_session_status: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error getting session status: {e}"}


def pause_mqtt_session(name: str, user_permission: bool = False) -> str:
    """
    Pause the MQTT session for the named printer, stopping telemetry updates.

    Telemetry is the continuous stream of printer state updates (temperatures, fan speeds,
    print progress) received over MQTT. While paused, tools that read live state
    (get_printer_state, get_temperatures, etc.) will return stale data.
    Requires user_permission=True. The connection is suspended but the printer
    configuration is retained. Resume with resume_mqtt_session().
    """
    log.debug("pause_mqtt_session: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("pause_mqtt_session: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("pause_mqtt_session: printer not connected: %s", name)
        return f"Error: Printer '{name}' not connected."
    try:
        log.debug("pause_mqtt_session: calling session_manager.pause_session for %s", name)
        session_manager.pause_session(name)
        log.debug("pause_mqtt_session: session paused for %s", name)
        return f"MQTT session paused for '{name}'."
    except Exception as e:
        log.error("pause_mqtt_session: error for %s: %s", name, e, exc_info=True)
        return f"Error pausing session for '{name}': {e}"


def resume_mqtt_session(name: str, user_permission: bool = False) -> str:
    """
    Resume a paused MQTT session for the named printer.

    Requires user_permission=True. Reconnects to the printer and restarts
    telemetry collection.
    """
    log.debug("resume_mqtt_session: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("resume_mqtt_session: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("resume_mqtt_session: printer not connected: %s", name)
        return f"Error: Printer '{name}' not connected."
    try:
        log.debug("resume_mqtt_session: calling session_manager.resume_session for %s", name)
        session_manager.resume_session(name)
        log.debug("resume_mqtt_session: session resumed for %s", name)
        return f"MQTT session resumed for '{name}'."
    except Exception as e:
        log.error("resume_mqtt_session: error for %s: %s", name, e, exc_info=True)
        return f"Error resuming session for '{name}': {e}"


def get_monitoring_history(name: str, raw: bool = False) -> dict:
    """
    Return telemetry history for charting: temperature and fan speed time-series.

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

    Response may be gzip+base64 compressed if the payload is large. Decompress:
      import gzip, json, base64
      data = json.loads(gzip.decompress(base64.b64decode(r["data"])))
    """
    log.debug("get_monitoring_history: called for name=%s raw=%s", name, raw)
    from tools._response import compress_if_large
    if raw:
        data = data_collector.get_all_data(name)
    else:
        data = data_collector.get_summary(name)
    if data is None:
        log.warning("get_monitoring_history: printer %s not connected", name)
        return _no_printer(name)
    log.debug("get_monitoring_history: returning data for %s raw=%s", name, raw)
    return compress_if_large(data)


def get_monitoring_series(name: str, field: str) -> dict:
    """
    Return the full time-series for a single telemetry field.

    field must be one of: tool, tool_1, bed, chamber, part_fan, aux_fan,
    exhaust_fan, heatbreak_fan.

    Returns the complete rolling 60-minute data for that field only (~1440 points,
    ~22 KB compressed). Use this instead of get_monitoring_history(raw=True) when
    you only need one metric — it avoids transferring all 8 series at once.

    Call get_monitoring_history() first (default raw=False) to see the summary
    for all fields, then call this for the specific field(s) you want to chart.

    Response may be gzip+base64 compressed if the payload is large. Decompress:
      import gzip, json, base64
      data = json.loads(gzip.decompress(base64.b64decode(r["data"])))
    """
    log.debug("get_monitoring_series: called for name=%s field=%s", name, field)
    from tools._response import compress_if_large
    series = data_collector.get_collection(name, field)
    if series is None:
        if data_collector.get_summary(name) is None:
            log.warning("get_monitoring_series: printer %s not connected", name)
            return _no_printer(name)
        valid = list(data_collector._collectors[name].collections.keys()) if name in data_collector._collectors else []
        return {"error": f"Unknown field '{field}'. Valid fields: {valid}"}
    log.debug("get_monitoring_series: returning series for %s field=%s points=%d", name, field, len(series.get("data", [])))
    return compress_if_large({"field": field, "series": series})


def trigger_printer_refresh(name: str, user_permission: bool = False) -> str:
    """
    Trigger a full data refresh by sending ANNOUNCE_VERSION and ANNOUNCE_PUSH via MQTT.

    Requires user_permission=True. Calls printer.refresh() which re-requests all
    state from the printer. Use sparingly — frequent calls indicate a session issue.
    """
    log.debug("trigger_printer_refresh: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("trigger_printer_refresh: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("trigger_printer_refresh: printer not connected: %s", name)
        return f"Error: Printer '{name}' not connected."
    try:
        log.debug("trigger_printer_refresh: calling printer.refresh() for %s", name)
        printer.refresh()
        log.debug("trigger_printer_refresh: refresh sent for %s", name)
        return f"Refresh triggered for '{name}'."
    except Exception as e:
        log.error("trigger_printer_refresh: error for %s: %s", name, e, exc_info=True)
        return f"Error triggering refresh for '{name}': {e}"


def get_firmware_version(name: str) -> dict:
    """
    Return the current firmware version for the named printer.

    Reads from printer.config.firmware_version which is populated during the
    initial MQTT version handshake. Also returns AMS firmware version when available.
    """
    log.debug("get_firmware_version: called for name=%s", name)
    config = session_manager.get_config(name)
    if config is None:
        log.warning("get_firmware_version: printer %s not connected", name)
        return _no_printer(name)
    log.debug("get_firmware_version: returning result for %s", name)
    return {
        "firmware_version": config.firmware_version,
        "ams_firmware_version": getattr(config, "ams_firmware_version", None),
    }


def set_print_options(
    name: str,
    auto_recovery: bool | None = None,
    sound: bool | None = None,
    user_permission: bool = False,
) -> dict:
    """
    Set one or more print option flags on the printer via MQTT.

    Requires user_permission=True. Pass None to leave an option unchanged.
    auto_recovery enables automatic print recovery after power loss or failure.
    sound enables or disables printer sound notifications.
    auto_recovery: if True, the printer will attempt to resume a print automatically
    after a power loss or hardware fault. sound: if True, the printer plays audible beep
    tones for events (print start, complete, error). Pass None to leave an option unchanged.
    Calls printer.set_print_option(PrintOption, bool) for each provided flag.
    """
    log.debug("set_print_options: called for name=%s auto_recovery=%s sound=%s user_permission=%s", name, auto_recovery, sound, user_permission)
    if not user_permission:
        log.debug("set_print_options: permission denied for %s", name)
        return {"error": _permission_denied()}
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("set_print_options: printer not connected: %s", name)
        return _no_printer(name)
    try:
        from bpm.bambutools import PrintOption
        results = {}
        if auto_recovery is not None:
            log.debug("set_print_options: calling printer.set_print_option for %s", name)
            printer.set_print_option(PrintOption.AUTO_RECOVERY, auto_recovery)
            results["auto_recovery"] = auto_recovery
        if sound is not None:
            log.debug("set_print_options: calling printer.set_print_option for %s", name)
            printer.set_print_option(PrintOption.SOUND_ENABLE, sound)
            results["sound"] = sound
        if not results:
            return {"error": "No options specified. Provide at least one of: auto_recovery, sound"}
        return {"success": True, "options_set": results}
    except Exception as e:
        log.error("set_print_options: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error setting print options: {e}"}


def force_state_refresh(name: str) -> dict:
    """
    Send a push_all / ANNOUNCE_PUSH request to force the printer to re-broadcast its full state.

    Calls printer.refresh() which publishes ANNOUNCE_VERSION and ANNOUNCE_PUSH via MQTT.
    Does not require user_permission because it only requests state from the printer —
    it does not modify any printer setting or send any command.
    Useful when printer state appears stale or fields are missing.
    """
    log.debug("force_state_refresh: called for name=%s", name)
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("force_state_refresh: printer %s not connected", name)
        return _no_printer(name)
    try:
        log.debug("force_state_refresh: calling printer.refresh() for %s", name)
        printer.refresh()
        log.debug("force_state_refresh: refresh sent for %s", name)
        return {"success": True, "message": f"State refresh request sent to '{name}'."}
    except Exception as e:
        log.error("force_state_refresh: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error sending state refresh for '{name}': {e}"}


def rename_printer(
    name: str,
    new_name: str,
    user_permission: bool = False,
) -> str:
    """
    Rename the printer device on the printer's own firmware.

    Sends a RENAME_PRINTER command to the printer to change its display name
    in the firmware (visible on the touchscreen and in Bambu Studio). This
    changes the name stored on the printer itself, not the local identifier
    used by this MCP (which is determined by the name passed to add_printer).
    Requires user_permission=True.
    """
    log.debug("rename_printer: called for name=%s new_name=%s user_permission=%s", name, new_name, user_permission)
    if not user_permission:
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        return _no_printer(name)
    try:
        printer.rename_printer(new_name)
        log.debug("rename_printer: sent rename command to %s (new_name=%s)", name, new_name)
        return f"Rename command sent to '{name}': printer display name set to '{new_name}'."
    except Exception as e:
        log.error("rename_printer: error for %s: %s", name, e, exc_info=True)
        return f"Error renaming printer '{name}': {e}"


def dump_log(tail_lines: int = 200) -> dict:
    """
    Return the bambu-mcp server log.

    Reads the last tail_lines lines from ~/bambu-mcp/bambu-mcp.log.
    No printer parameter required — this is a server-level operation.

    The log file captures entries at the configured log level (set via BAMBU_MCP_LOG_LEVEL
    in the MCP config; default WARNING). Set BAMBU_MCP_LOG_LEVEL=DEBUG for full debug output.
    Use this tool to diagnose connection issues, tool errors, or unexpected printer behavior.

    Returns a dict with:
    - lines: list of log lines (newest last)
    - total_lines: number of lines returned
    - log_path: absolute path to the log file
    """
    log.debug("dump_log: called with tail_lines=%s", tail_lines)
    from pathlib import Path
    log_path = Path(__file__).parent.parent / "bambu-mcp.log"
    log.debug("dump_log: log_path=%s", log_path)
    try:
        if not log_path.exists():
            log.debug("dump_log: log file does not exist at %s", log_path)
            return {"lines": [], "total_lines": 0, "log_path": str(log_path), "note": "Log file does not exist yet — entries will appear once the server logs at the configured BAMBU_MCP_LOG_LEVEL (default WARNING)."}
        with open(log_path, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        lines = [l.rstrip("\n") for l in all_lines[-tail_lines:]]
        log.debug("dump_log: returning %d lines (of %d total)", len(lines), len(all_lines))
        return {"lines": lines, "total_lines": len(lines), "log_path": str(log_path)}
    except Exception as e:
        log.error("dump_log: error reading log: %s", e, exc_info=True)
        return {"error": f"Error reading log file: {e}"}


def truncate_log(user_permission: bool = False) -> dict:
    """
    Truncate the bambu-mcp server log.

    Clears ~/bambu-mcp/bambu-mcp.log to 0 bytes. No printer parameter required.
    Useful after a debugging session to start with a clean log.
    Requires user_permission=True.
    """
    log.debug("truncate_log: called with user_permission=%s", user_permission)
    if not user_permission:
        log.debug("truncate_log: permission denied")
        return {"error": "user_permission=True required to truncate the log."}
    from pathlib import Path
    log_path = Path(__file__).parent.parent / "bambu-mcp.log"
    log.debug("truncate_log: log_path=%s", log_path)
    try:
        with open(log_path, "w") as f:
            pass
        log.info("truncate_log: log truncated at %s", log_path)
        return {"success": True, "log_path": str(log_path)}
    except Exception as e:
        log.error("truncate_log: error truncating log: %s", e, exc_info=True)
        return {"error": f"Error truncating log file: {e}"}
