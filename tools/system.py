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


def _permission_denied(consequence: str) -> str:
    return f"Error: user_permission must be True to perform this action. {consequence}"


def get_server_info() -> dict:
    """
    Return runtime port pool state for the bambu-mcp server.

    WHEN to use: discover the actual REST API port at runtime before constructing an HTTP request
    URL (the REST API base URL is http://localhost:{api_port}/api), or to see which ports the
    shared pool has claimed.

    Sibling disambiguation: ``get_server_info`` reports server-wide port usage, including every
    active MJPEG camera stream. ``get_stream_url`` returns the camera stream details for one
    named printer.

    Args: none.

    Returns:
        A dict on success:

        - ``api_port``: TCP port the REST API is currently bound to (0 if not running).
        - ``api_url``: convenience base URL, ``http://localhost:{api_port}/api``.
        - ``pool_start``: first port in the shared ephemeral pool (default 49152).
        - ``pool_end``: last port in the shared ephemeral pool, inclusive (default 49351).
        - ``pool_size``: total number of ports in the pool (``pool_end - pool_start + 1``; 200 with
          the default range).
        - ``pool_available``: ``pool_size`` minus the number of claimed ports. It is understated
          when a claimed port lies outside the pool range (for example an out-of-range
          ``BAMBU_API_PORT``).
        - ``pool_claimed``: sorted list of all currently claimed port numbers (the REST API port
          plus all active MJPEG stream ports).
        - ``stream_count``: number of active MJPEG camera streams.
        - ``streams``: dict of ``{printer_name: {port, url}}`` for each active stream.

        On failure: ``{"error": "Error retrieving server info: <exception>"}``.

    Notes:
        The bambu-mcp HTTP REST API and all MJPEG camera stream servers draw ports from a shared
        ephemeral pool anchored at port 49152 (IANA RFC 6335 Dynamic/Private range 49152-65535).
        Ports are allocated on demand and released when listeners stop.

        The server also registers a Zeroconf/mDNS service (`_bambu-mcp._tcp.local.`) at startup
        so non-MCP clients can discover the port without calling this tool. See
        kb_get('bambu-http-system') for the TXT record schema.

        Environment variables that control the pool:

        - ``BAMBU_PORT_POOL_START``: override pool start (default 49152).
        - ``BAMBU_PORT_POOL_END``: override pool end (default 49351).
        - ``BAMBU_API_PORT``: preferred port for the REST API. It is tried first and is honoured
          even when it lies outside the pool; otherwise the pool is rescanned from ``pool_start``
          (the scan does not continue from the preferred port).

        Example, construct the REST API base URL::

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

    WHEN to use: check whether a printer's MQTT session is connected or paused, for example
    after ``pause_mqtt_session`` or ``resume_mqtt_session``, or when live state looks stale.

    Sibling disambiguation: ``get_session_status`` reports on a printer that has a live session
    object and returns an error dict for one that does not. ``get_printer_connection_status``
    inspects one printer by name and reports configured=False for an unknown name instead of an
    error.

    Args:
        name: Configured printer name (see ``get_configured_printers``).

    Returns:
        ``{"name": str, "connected": bool, "service_state": str, "session_active": True}`` on
        success. ``connected`` is True only when the service state is CONNECTED;
        ``service_state`` is the BPM service state enum name (NO_STATE, CONNECTED, DISCONNECTED,
        PAUSED or QUIT);
        ``session_active`` is always True on success, because a printer without a live session
        returns the error shape instead. Errors: ``{"error": "Printer '<name>' not connected"}``
        when the printer has no live session, or ``{"error": "Error getting session status:
        <exception>"}`` on failure.
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

    WHEN to use: stop live telemetry from a printer without removing it, for example to quiet a
    session you are debugging. Undo it with ``resume_mqtt_session``.

    WRITE GUARD: unsubscribes from the printer's MQTT report topic and sets the session state to
    PAUSED, so live state stops updating. The printer configuration is retained. With
    ``user_permission`` unset the tool changes nothing and returns the refusal string naming that
    consequence.

    Sibling disambiguation: ``pause_mqtt_session`` only stops the incoming telemetry and keeps
    the configuration; ``resume_mqtt_session`` reverses it. ``disconnect_printer`` tears down the
    camera stream and the MQTT session while keeping the printer configured.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        user_permission: Set to True only after the operator approves pausing this printer's
            telemetry.

    Returns:
        A string. Success: ``"MQTT session paused for '<name>'."``. Errors are strings starting
        with ``"Error"``: the refusal ``"Error: user_permission must be True to perform this
        action. <consequence>"``, ``"Error: Printer '<name>' not connected."`` (the printer has no
        live session), or ``"Error pausing session for '<name>': <exception>"``.

    Notes:
        Telemetry is the continuous stream of printer state updates (temperatures, fan speeds,
        print progress) received over MQTT. While paused, tools that read live state
        (get_printer_state, get_temperatures, etc.) will return stale data. Pausing an
        already-paused session is a no-op that still returns the success string.
    """
    log.debug("pause_mqtt_session: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("pause_mqtt_session: permission denied for %s", name)
        return _permission_denied(
            "This would unsubscribe from the printer's MQTT report topic and pause the session, "
            "so live state stops updating."
        )
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

    WHEN to use: restart telemetry after ``pause_mqtt_session``, or when a session has dropped
    and live state has stopped updating.

    WRITE GUARD: re-subscribes to the printer's MQTT report topic to restart telemetry, and if the
    MQTT session thread has exited it opens a new MQTT session to the printer. With
    ``user_permission`` unset the tool changes nothing and returns the refusal string naming that
    consequence.

    Sibling disambiguation: ``resume_mqtt_session`` restarts telemetry for a printer that already
    has a session object; ``pause_mqtt_session`` is its opposite. ``get_session_status`` reads the
    resulting state without changing it.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        user_permission: Set to True only after the operator approves resuming or reconnecting
            this printer's MQTT session.

    Returns:
        A string. Success: ``"MQTT session resumed for '<name>'."``. Errors are strings starting
        with ``"Error"``: the refusal ``"Error: user_permission must be True to perform this
        action. <consequence>"``, ``"Error: Printer '<name>' not connected."`` (the printer has no
        live session), or ``"Error resuming session for '<name>': <exception>"``.

    Notes:
        The success string is returned once the resume call completes without raising; it does not
        confirm that the session reached CONNECTED. Call ``get_session_status`` to check. If the
        session is not paused and its session thread is still alive, the call does nothing and
        still returns the success string; if that thread has exited, it starts a new session.
        If the session is paused but its MQTT connection has dropped while the session thread is
        still alive, the library leaves the state at QUIT and relies on paho's own reconnect: no
        new session is opened, and ``get_session_status`` reads QUIT until that reconnect
        completes.
    """
    log.debug("resume_mqtt_session: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("resume_mqtt_session: permission denied for %s", name)
        return _permission_denied(
            "This would resume the paused MQTT session, re-subscribing to the printer's report "
            "topic or reconnecting the MQTT session if it has dropped."
        )
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

    Also includes a 'health' key with rolling print health records from the background
    monitor. When raw=False, 'health' contains {min,max,avg,last,count} stats for each
    of 6 fields: success_pct, confidence, hot_pct, strand_score, diff_score,
    remaining_min. When raw=True, 'health' is the full list of structured records.
    Health records are only present when a print job has been active and the background
    monitor has accumulated data.

    Note on gcode_state_durations: a FAILED entry does not mean the current job failed.
    The rolling window captures the prior job's terminal state before the current job
    started. A print that has been RUNNING continuously will show a small FAILED duration
    from the previous job alongside its dominant RUNNING duration.

    Response may be gzip+base64 compressed if the payload is large. Decompress:
      import gzip, json, base64
      data = json.loads(gzip.decompress(base64.b64decode(r["data"])))
    No HTTP fallback route exists for this tool. If raw=True response exceeds the
    MCP limit even after compression, use get_monitoring_series(field) to fetch
    one field at a time instead.
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
    # Append rolling health-history (structured analysis records).
    from camera import job_monitor as _jm
    health_history = _jm.get_health_history(name)
    if health_history:
        if raw:
            data["health"] = health_history
        else:
            health_stats: dict = {}
            for field in ("success_pct", "confidence", "hot_pct", "strand_score", "diff_score", "remaining_min"):
                values = [r[field] for r in health_history if r.get(field) is not None]
                if values:
                    health_stats[field] = {
                        "min":   round(min(values), 4),
                        "max":   round(max(values), 4),
                        "avg":   round(sum(values) / len(values), 4),
                        "last":  round(values[-1], 4),
                        "count": len(values),
                    }
            if health_stats:
                data["health"] = health_stats
    log.debug("get_monitoring_history: returning data for %s raw=%s", name, raw)
    return compress_if_large(data)


def get_monitoring_series(name: str, field: str) -> dict:
    """
    Return the full time-series for a single telemetry field.

    field must be one of: tool, tool_1, bed, chamber, part_fan, aux_fan,
    exhaust_fan, heatbreak_fan (sensor fields), or one of the health fields:
    success_pct, confidence, hot_pct, strand_score, diff_score, remaining_min.
    Health fields are sourced from the background print monitor and are only
    populated during or after an active print job.

    Returns the complete rolling 60-minute data for that field only (~1440 points,
    ~22 KB compressed). Use this instead of get_monitoring_history(raw=True) when
    you only need one metric — it avoids transferring all 8 series at once.

    Call get_monitoring_history() first (default raw=False) to see the summary
    for all fields, then call this for the specific field(s) you want to chart.

    Response may be gzip+base64 compressed if the payload is large. Decompress:
      import gzip, json, base64
      data = json.loads(gzip.decompress(base64.b64decode(r["data"])))
    No HTTP fallback route exists for this tool. This is already the smallest
    scope of monitoring data (one field). If the envelope still exceeds the MCP
    limit, the series is unusually large — check data_collector for retention issues.
    """
    _HEALTH_FIELDS = frozenset({
        "success_pct", "confidence", "hot_pct", "strand_score", "diff_score", "remaining_min",
    })
    log.debug("get_monitoring_series: called for name=%s field=%s", name, field)
    from tools._response import compress_if_large
    if field in _HEALTH_FIELDS:
        from camera import job_monitor as _jm
        if data_collector.get_summary(name) is None:
            log.warning("get_monitoring_series: printer %s not connected (health field)", name)
            return _no_printer(name)
        history = _jm.get_health_history(name)
        series_data = [{"t": r["ts"], "v": r[field]} for r in history if r.get(field) is not None]
        log.debug("get_monitoring_series: health field %s points=%d for %s", field, len(series_data), name)
        return compress_if_large({"field": field, "series": {"name": field, "data": series_data}})
    series = data_collector.get_collection(name, field)
    if series is None:
        if data_collector.get_summary(name) is None:
            log.warning("get_monitoring_series: printer %s not connected", name)
            return _no_printer(name)
        valid = list(data_collector._collectors[name].collections.keys()) if name in data_collector._collectors else []
        health_fields = sorted(_HEALTH_FIELDS)
        return {"error": f"Unknown field '{field}'. Sensor fields: {valid}. Health fields: {health_fields}"}
    log.debug("get_monitoring_series: returning series for %s field=%s points=%d", name, field, len(series.get("data", [])))
    return compress_if_large({"field": field, "series": series})


def trigger_printer_refresh(name: str, user_permission: bool = False) -> str:
    """
    Trigger a full data refresh by sending ANNOUNCE_VERSION and ANNOUNCE_PUSH via MQTT.

    WHEN to use: printer state looks stale or fields are missing and the session reads as
    connected. Use sparingly, since frequent calls indicate a session issue.

    WRITE GUARD: publishes ANNOUNCE_VERSION and ANNOUNCE_PUSH requests to the printer's MQTT
    request topic so it re-sends its full state. It changes no printer setting. With
    ``user_permission`` unset the tool changes nothing and returns the refusal string naming that
    consequence.

    Sibling disambiguation: ``trigger_printer_refresh`` and ``force_state_refresh`` make the
    same ``printer.refresh()`` call and differ only in return shape: this one returns a string,
    ``force_state_refresh`` returns a dict. ``refresh_nozzles`` refreshes nozzle information
    only.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        user_permission: Set to True only after the operator approves sending the refresh
            requests to the printer.

    Returns:
        A string. Success: ``"Refresh triggered for '<name>'."``. Errors are strings starting
        with ``"Error"``: the refusal ``"Error: user_permission must be True to perform this
        action. <consequence>"``, ``"Error: Printer '<name>' not connected."`` (the printer has no
        live session), or ``"Error triggering refresh for '<name>': <exception>"``.

    Notes:
        The tool calls printer.refresh(), which re-requests all state from the printer, but
        printer.refresh() publishes only while the BPM service state is CONNECTED. In any other
        state (for example PAUSED) it sends nothing, and the tool still returns the success
        string. Check ``get_session_status`` if state does not update.
    """
    log.debug("trigger_printer_refresh: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("trigger_printer_refresh: permission denied for %s", name)
        return _permission_denied(
            "This would publish ANNOUNCE_VERSION and ANNOUNCE_PUSH requests to the printer so "
            "it re-sends its full state."
        )
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

    WHEN to use: check which firmware a printer (and its AMS, when reported) is running, for
    example before deciding whether a firmware-dependent feature is available.

    Sibling disambiguation: ``get_firmware_version`` returns the firmware versions alone.
    ``get_printer_info`` returns the model and serial number together with the firmware version.

    Args:
        name: Configured printer name (see ``get_configured_printers``).

    Returns:
        ``{"firmware_version": str, "ams_firmware_version": str}`` on success. Both are strings
        and read ``""`` (never None) until the printer reports them: ``firmware_version`` until
        the version handshake reply arrives, and ``ams_firmware_version`` also on a printer with
        no AMS. To detect a missing value test for the empty string, not None; call
        ``trigger_printer_refresh`` if ``firmware_version`` stays ``""``. Error: ``{"error":
        "Printer '<name>' not connected"}`` when the printer has no live session.
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

    WHEN to use: turn auto recovery and printer sound notifications on or off in one call.

    WRITE GUARD: sends an MQTT print-option command to the printer for each flag you pass
    (auto_recovery, sound), which changes whether the printer resumes a print after a power loss
    and whether it beeps, and updates the local BPM config to match. With ``user_permission`` unset
    the tool changes nothing and returns the refusal naming that consequence.

    Sibling disambiguation: ``set_print_options`` sets only the auto_recovery and sound flags
    together and returns a dict. ``set_print_option`` (singular) sets one option by name from a
    wider list (including the filament tangle, nozzle blob and air print flags) and returns a
    string.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        auto_recovery: If True, resume the print after a power loss (bpm also describes it as
            step-loss recovery); False disables that. None leaves the option unchanged.
        sound: If True, the printer beeps for notifications; False silences them. None leaves the
            option unchanged.
        user_permission: Set to True only after the operator approves changing these printer
            options.

    Returns:
        A dict. Success: ``{"success": True, "options_set": {"auto_recovery": bool, "sound":
        bool}}`` listing only the options that were passed. Errors are ``{"error": str}``: the
        refusal ``"Error: user_permission must be True to perform this action. <consequence>"``,
        ``"Printer '<name>' not connected"``, ``"No options specified. Provide at least one of:
        auto_recovery, sound"`` (both passed as None), or ``"Error setting print options:
        <exception>"``.

    Notes:
        Calls printer.set_print_option(PrintOption, bool) once per option provided, auto_recovery
        first and then sound. If the second call raises, only the error dict is returned: the
        first option has already been published and its local config updated, and the result does
        not say which succeeded.

        Each command carries the option keys accumulated by earlier print-option calls in this
        server process (bpm builds it from one shared module-level dict), so options you did not
        pass may be re-sent with their last-set values, possibly re-applying a value changed at
        the printer since. The same applies to ``set_print_option``. ``{"success": True}`` means
        the publish call returned without raising, not that the printer received anything: bpm
        discards the publish result, and with the MQTT connection down paho drops the message
        without raising while the local config is still updated. Check ``get_session_status``
        first.
    """
    log.debug("set_print_options: called for name=%s auto_recovery=%s sound=%s user_permission=%s", name, auto_recovery, sound, user_permission)
    if not user_permission:
        log.debug("set_print_options: permission denied for %s", name)
        return {"error": _permission_denied(
            "This would change the printer's auto-recovery and/or sound notification options "
            "over MQTT."
        )}
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


def force_state_refresh(name: str, user_permission: bool = False) -> dict:
    """
    Send a push_all / ANNOUNCE_PUSH request to force the printer to re-broadcast its full state.

    WHEN to use: printer state appears stale or fields are missing and you want the result as a
    dict.

    WRITE GUARD: publishes ANNOUNCE_VERSION and ANNOUNCE_PUSH requests to the printer's MQTT
    request topic so it re-sends its full state. It changes no printer setting. With
    ``user_permission`` unset the tool changes nothing, does not call printer.refresh(), and
    returns the refusal naming that consequence.

    Sibling disambiguation: ``force_state_refresh`` and ``trigger_printer_refresh`` make the same
    ``printer.refresh()`` call and differ only in return shape: this one returns a dict,
    ``trigger_printer_refresh`` returns a string.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        user_permission: Set to True only after the operator approves sending the refresh
            requests to the printer.

    Returns:
        A dict. Success: ``{"success": True, "message": "State refresh request sent to
        '<name>'."}``. Errors are ``{"error": str}``: the refusal ``"Error: user_permission must
        be True to perform this action. <consequence>"``, ``"Printer '<name>' not connected"``, or
        ``"Error sending state refresh for '<name>': <exception>"``.

    Notes:
        The tool calls printer.refresh(), which publishes ANNOUNCE_VERSION and ANNOUNCE_PUSH via
        MQTT, but only while the BPM service state is CONNECTED. In any other state (for example
        PAUSED) it sends nothing, and the tool still returns the success dict. Check
        ``get_session_status`` if state does not update.
    """
    log.debug("force_state_refresh: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("force_state_refresh: permission denied for %s", name)
        return {"error": _permission_denied(
            "This would publish ANNOUNCE_VERSION and ANNOUNCE_PUSH requests to the printer so "
            "it re-sends its full state."
        )}
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
    Rename the printer device by sending it a RENAME_PRINTER command.

    WHEN to use: change the device name the printer itself holds, as opposed to the local
    identifier this MCP uses.

    WRITE GUARD: publishes a RENAME_PRINTER command (``update.name``) to the printer. It does not
    change the local identifier this MCP uses for the printer. Where the new name becomes visible
    (the printer's touchscreen, Bambu Studio) is expected behaviour that this code does not
    establish. With ``user_permission`` unset the tool changes nothing and returns the refusal
    string naming that consequence.

    Sibling disambiguation: ``rename_printer`` renames the printer device itself. The local
    identifier used by this MCP is determined by the name passed to ``add_printer`` and is not
    changed here. ``rename_sdcard_file`` renames a file on the printer's SD card, not the
    printer.

    Args:
        name: Configured printer name (the local identifier, see ``get_configured_printers``).
        new_name: New device name to send to the printer.
        user_permission: Set to True only after the operator approves renaming the printer
            device.

    Returns:
        A string on success: ``"Rename command sent to '<name>': printer display name set to
        '<new_name>'."`` (the publish call returned; delivery is not checked, since with the MQTT
        connection down the message is dropped silently, and the printer's acceptance is not
        confirmed). String errors: the refusal ``"Error: user_permission must be True to perform this
        action. <consequence>"`` and ``"Error renaming printer '<name>': <exception>"``. A printer
        with no live session returns a dict instead of a string:
        ``{"error": "Printer '<name>' not connected"}``.
    """
    log.debug("rename_printer: called for name=%s new_name=%s user_permission=%s", name, new_name, user_permission)
    if not user_permission:
        return _permission_denied(
            "This would change the device name stored in the printer's own firmware, shown on its "
            "touchscreen and in Bambu Studio."
        )
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

    WHEN to use: diagnose connection issues, tool errors, or unexpected printer behavior by
    reading the last lines of the server log. It is a server-level operation and takes no
    printer parameter.

    Sibling disambiguation: ``dump_log`` reads the server log file and changes nothing.
    ``truncate_log`` empties that same file, so read what you need with ``dump_log`` first.

    Args:
        tail_lines: Number of lines to return from the end of the log (default 200). Reduce it to
            shrink the response. The value is not validated: 0 returns the whole file.

    Returns:
        ``{"lines": [str], "total_lines": int, "log_path": str}`` on success: ``lines`` holds the
        last ``tail_lines`` log lines (newest last), ``total_lines`` is the number of lines
        returned (not the size of the whole file), and ``log_path`` is the absolute path to the
        log file. When the log file does not exist yet, the same three keys are returned with an
        empty ``lines``, ``total_lines`` 0, and an added ``note`` string. Error:
        ``{"error": "Error reading log file: <exception>"}``.

    Notes:
        The log file is ``bambu-mcp.log`` in the bambu-mcp install directory (the repository
        root); ``log_path`` in the result gives its absolute path.

        The log file captures entries at the current runtime log level (both root and bpm boot
        fixed at ERROR; no env var sets this). Use POST /api/set_log_level?level=DEBUG&bpm_level=DEBUG
        for full debug output, then POST /api/set_bpm_verbose?printer=<name>&verbose=true to also
        capture raw MQTT message payloads from bpm — see docs/operators-guide.md Debug-logging SOP.

        This tool returns its dict as-is and does not itself gzip+base64 compress the response.
        If a very large ``tail_lines`` produces a response the MCP limit rejects, lower
        ``tail_lines``. The REST fallback ``GET /api/dump_log`` returns the whole log file as
        text/plain and ignores ``tail_lines``.
    """
    log.debug("dump_log: called with tail_lines=%s", tail_lines)
    from pathlib import Path
    log_path = Path(__file__).parent.parent / "bambu-mcp.log"
    log.debug("dump_log: log_path=%s", log_path)
    try:
        if not log_path.exists():
            log.debug("dump_log: log file does not exist at %s", log_path)
            return {"lines": [], "total_lines": 0, "log_path": str(log_path), "note": "Log file does not exist yet — entries will appear once the server logs at its current level (boots at ERROR; raise via /api/set_log_level)."}
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

    WHEN to use: start with a clean log after a debugging session. It is a server-level
    operation and takes no printer parameter.

    WRITE GUARD: empties the server log file (``bambu-mcp.log`` in the bambu-mcp install
    directory) to 0 bytes, permanently discarding every line it holds. With ``user_permission``
    unset the tool changes nothing and returns the refusal naming that consequence.

    Sibling disambiguation: ``truncate_log`` destroys the log contents; ``dump_log`` only reads
    the same file, so use it first to keep what you need.

    Args:
        user_permission: Set to True only after the operator approves permanently erasing the
            server log.

    Returns:
        ``{"success": True, "log_path": str}`` on success, where ``log_path`` is the absolute path
        of the truncated file. Errors are ``{"error": str}``: the refusal ``"Error:
        user_permission must be True to perform this action. <consequence>"``, or ``"Error
        truncating log file: <exception>"``.
    """
    log.debug("truncate_log: called with user_permission=%s", user_permission)
    if not user_permission:
        log.debug("truncate_log: permission denied")
        return {"error": _permission_denied(
            "This would permanently erase the entire bambu-mcp server log file."
        )}
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
