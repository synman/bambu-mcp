"""
tools/management.py — Printer lifecycle and credential management tools.

These tools manage the set of configured printers: adding, removing, updating
credentials, and inspecting connection status.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

from session_manager import session_manager
import auth


def _permission_denied(consequence: str) -> str:
    return f"Error: user_permission must be True to perform this action. {consequence}"


def get_configured_printers() -> dict:
    """
    List every configured printer name with a session flag for each.

    WHEN to use: the first stop to see which printer names exist on this server before calling
    any per-printer tool, and to spot which of them currently have a session object in memory.

    Sibling disambiguation: ``get_configured_printers`` lists all configured printers in one call.
    ``get_printer_connection_status`` inspects ONE printer in depth (adds the BPM service state
    and the watchdog's ``recent_update`` flag, and reports whether a name is configured at all).

    Args:
        none.

    Returns:
        ``{"printers": [{"name": str, "connected": bool, "session_active": bool}], "total": int}``.
        There is no error shape: the tool never returns an ``error`` key.

    Notes:
        ``connected`` and ``session_active`` are computed from the same test in this tool: both
        are True exactly when a session object for the printer exists in memory, so they are always
        equal here. They do NOT prove that the MQTT service state is CONNECTED and telemetry is
        flowing. Use ``get_printer_connection_status`` for the real ``connected`` value and
        ``service_state``. A printer that is merely unreachable still has a session object (left in
        QUIT state, because the library swallows the connect failure) and so still reads True here.
        Only a printer torn down by ``disconnect_printer`` / ``remove_printer``, or one whose
        credentials, session construction or session start (``start_session`` raising) failed
        outright, has no session: a failed start leaves nothing registered.
    """
    log.debug("get_configured_printers: called")
    names = auth.get_configured_printer_names()
    connected = set(session_manager.list_connected())
    printers = []
    for n in names:
        printers.append({
            "name": n,
            "connected": n in connected,
            "session_active": n in connected,
        })
    log.debug("get_configured_printers: returning %d printers", len(printers))
    return {"printers": printers, "total": len(printers)}


def add_printer(
    name: str,
    ip: str,
    serial: str,
    access_code: str,
    user_permission: bool = False,
) -> str:
    """
    Add a new Bambu Lab printer: save its credentials and start an MQTT session.

    WHEN to use: set up a printer that has no configuration on this server yet (use
    ``discover_printers`` first to find its ip and serial). To change the credentials of an
    existing name, use ``update_printer_credentials`` (or ``disconnect_printer`` then
    ``start_printer``); do not reuse this tool for that.

    WRITE GUARD: saves ip, serial and access_code under ``name`` and starts an MQTT session for
    it. If ``name`` is already configured this OVERWRITES its stored ip, serial and access_code
    with no existence check (the previous access_code is not recoverable), and the new session
    replaces the existing one: the old MQTT session is stopped first, so it is a restart with no
    orphaned client, but telemetry and control are briefly interrupted (an active print is not
    cancelled). With ``user_permission`` unset the tool changes nothing (no credential is saved,
    no session is started) and returns the refusal string naming that consequence.

    Sibling disambiguation: ``add_printer`` writes all three credentials and starts a session.
    ``update_printer_credentials`` changes only the fields you pass for an already-configured
    printer and restarts its session cleanly. ``start_printer`` reconnects a configured printer
    without touching credentials. ``discover_printers`` only finds printers on the LAN and never
    adds one.

    Args:
        name: User-chosen identifier for the printer. Reusing an existing name overwrites it.
        ip: The printer's local IP address.
        serial: The printer's hardware serial number.
        access_code: The 8-character LAN access code, shown on the printer touchscreen at
            Settings -> Network -> LAN -> Access Code. A wrong code typically shows up as a
            session that does not STAY connected rather than an immediate connected=False: the
            library sets CONNECTED without inspecting the broker's reason code, so re-check
            ``get_printer_connection_status`` a few times rather than trusting one read.
        user_permission: Set to True only after the user has explicitly approved adding this
            printer and overwriting any credentials already stored under ``name``.

    Returns:
        A ``str`` in every case, never a dict. Success: ``"Printer '<name>' added and session
        started."``. Errors: ``"Error saving credentials for '<name>': <detail>"`` when the
        credential store write fails; ``"Credentials saved for '<name>' but session failed to
        start: <detail>"`` when the credentials were saved but the session could not start (no
        session is left registered for the name, so a printer that had one has none now).
        Refused (``user_permission`` False): ``"Error: user_permission must be True to perform this
        action. <consequence>"``.

    Notes:
        The success string means a session was launched, not that the printer is reachable: an
        unreachable ip still returns it, with the session left in QUIT and no automatic retry.
        Confirm with ``get_printer_connection_status``. A printer added at runtime gets an MQTT
        session and the live-state tools, but telemetry collection and the job health monitor are
        registered only at server startup. Until the MCP server restarts, the monitoring and
        chart tools (``get_monitoring_data``, ``get_monitoring_history``, ``get_monitoring_series``,
        ``open_charts``) report it as not connected, and ``open_job_state`` and the cached side
        of ``analyze_active_job`` have no monitor results.
    """
    log.debug("add_printer: called for name=%s ip=%s serial=%s access_code=<redacted> user_permission=%s", name, ip, serial, user_permission)
    if not user_permission:
        log.debug("add_printer: permission denied for %s", name)
        return _permission_denied(
            "This would save the given ip, serial and access code under this printer name, "
            "overwriting any credentials already stored for that name, and start an MQTT session for it "
            "(restarting the existing session if the name already has one)."
        )
    try:
        auth.save_printer_credentials(name=name, ip=ip, access_code=access_code, serial=serial)
        log.debug("add_printer: credentials saved for '%s'", name)
    except Exception as e:
        log.error("add_printer: error saving credentials for %s: %s", name, e, exc_info=True)
        return f"Error saving credentials for '{name}': {e}"
    try:
        session_manager.start_printer(name)
        log.info("add_printer: session started for '%s'", name)
        return f"Printer '{name}' added and session started."
    except Exception as e:
        log.error("add_printer: session failed to start for %s: %s", name, e, exc_info=True)
        return f"Credentials saved for '{name}' but session failed to start: {e}"


def remove_printer(name: str, user_permission: bool = False) -> str:
    """
    Remove a printer: stop its MQTT session and delete its stored credentials.

    WHEN to use: permanently retire a printer from this server's configured list, for example
    a printer that was sold, replaced, or added under the wrong name.

    WRITE GUARD: stops the printer's MQTT session and permanently deletes its stored ip, serial
    and access_code and its entry in the configured printer list; re-adding it later needs all
    three values again. The physical printer is not affected. With ``user_permission`` unset the
    tool changes nothing and returns the refusal string naming that consequence (checked before
    anything else, so it is returned even for a name that is not configured). A name that is not
    configured is also refused with nothing stopped or deleted.

    Sibling disambiguation: ``remove_printer`` deletes the configuration for good and stops only
    the MQTT session. ``disconnect_printer`` stops the camera stream and MQTT session but KEEPS
    the credentials, and ``start_printer`` reconnects it.

    Args:
        name: Name of the configured printer to remove.
        user_permission: Set to True only after the user has explicitly approved permanently
            deleting this printer's stored credentials.

    Returns:
        A ``str`` in every case, never a dict. Success: ``"Printer '<name>' removed and credentials
        deleted."``. Errors: ``"Error: Printer '<name>' is not configured."`` when ``name`` has no
        configuration (nothing is stopped or deleted); ``"Session stopped for '<name>' but error
        deleting credentials: <detail>"``. Refused (``user_permission`` False): ``"Error:
        user_permission must be True to perform this action. <consequence>"``.

    Notes:
        Only the MQTT session is stopped. A running MJPEG camera stream is left running (it
        captured the ip and access code when it started, so it keeps serving after the credentials
        are deleted; call ``stop_stream`` first), and the per-printer job monitor and its collected
        telemetry are neither stopped nor cleared.
    """
    log.debug("remove_printer: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("remove_printer: permission denied for %s", name)
        return _permission_denied(
            "This would stop the printer's MQTT session and permanently delete its stored ip, "
            "serial and access code from this server."
        )
    if name not in auth.get_configured_printer_names():
        log.warning("remove_printer: printer not configured: %s", name)
        return f"Error: Printer '{name}' is not configured."
    session_manager.stop_printer(name)
    try:
        auth.delete_printer_credentials(name)
        log.info("remove_printer: printer removed: %s", name)
        return f"Printer '{name}' removed and credentials deleted."
    except Exception as e:
        log.error("remove_printer: error deleting credentials for %s: %s", name, e, exc_info=True)
        return f"Session stopped for '{name}' but error deleting credentials: {e}"


def update_printer_credentials(
    name: str,
    ip: str | None = None,
    serial: str | None = None,
    access_code: str | None = None,
    user_permission: bool = False,
) -> str:
    """
    Update one or more stored credentials of an already-configured printer and restart its session.

    WHEN to use: a configured printer's ip changed (new DHCP lease), or its access code or serial
    must be corrected, and only some of the three values need to change.

    WRITE GUARD: overwrites the stored ip, serial and/or access_code with the values you pass
    (omitted fields keep their current values) and restarts the printer's MQTT session so the new
    values take effect. A restart does NOT cancel an active print: the printer keeps printing
    autonomously while the session reconnects, but telemetry and control are unavailable until
    it does. With ``user_permission`` unset the tool changes nothing and returns the refusal
    string naming that consequence.

    Sibling disambiguation: ``update_printer_credentials`` edits selected fields of a printer that
    already has complete credentials. ``add_printer`` writes all three credentials from scratch
    (and overwrites without a check). ``start_printer`` reconnects without changing any credential.

    Args:
        name: Name of the configured printer to update.
        ip: New printer IP address, or None to keep the stored one.
        serial: New hardware serial number, or None to keep the stored one.
        access_code: New LAN access code, or None to keep the stored one.
        user_permission: Set to True only after the user has explicitly approved changing this
            printer's stored credentials and restarting its session.

    Returns:
        A ``str`` in every case, never a dict. Success: ``"Credentials updated and session
        restarted for '<name>'."`` (the old session was stopped and a new one launched; this does
        not confirm the new credentials authenticate or the printer is reachable). Errors:
        ``"Error: <detail>"`` when the printer has no complete
        stored credentials (not configured); ``"Error updating credentials for '<name>': <detail>"``
        when the credential store write fails; ``"Credentials updated for '<name>' but session
        restart failed: <detail>"`` when the credentials were saved but launching the session
        raised (credential lookup, session construction or session start failure; an unreachable
        ip does not); no session is left registered then.
        Refused (``user_permission`` False): ``"Error: user_permission must be True to perform this
        action. <consequence>"``.

    Notes:
        An unreachable ip leaves the new session in QUIT with no automatic retry and still returns
        success. Confirm with ``get_printer_connection_status`` (connected=True), and call
        ``start_printer`` once the address is corrected. Only the MQTT session is restarted: a
        running camera stream keeps the ip and access code it captured when it started; call
        ``stop_stream`` then ``start_stream`` for it to pick up the new values.
    """
    log.debug("update_printer_credentials: called for name=%s access_code=%s", name, "<redacted>" if access_code is not None else "unchanged")
    if not user_permission:
        log.debug("update_printer_credentials: permission denied for %s", name)
        return _permission_denied(
            "This would overwrite the stored ip, serial and/or access code for this printer "
            "and restart its MQTT session."
        )
    try:
        existing = auth.get_printer_credentials(name)
    except KeyError as e:
        return f"Error: {e}"
    new_ip = ip if ip is not None else existing["ip"]
    new_serial = serial if serial is not None else existing["serial"]
    new_code = access_code if access_code is not None else existing["access_code"]
    try:
        auth.save_printer_credentials(
            name=name, ip=new_ip, access_code=new_code, serial=new_serial
        )
        log.debug("update_printer_credentials: credentials saved for '%s'", name)
    except Exception as e:
        log.error("update_printer_credentials: error saving for %s: %s", name, e, exc_info=True)
        return f"Error updating credentials for '{name}': {e}"
    # Restart session with new credentials
    log.debug("update_printer_credentials: restarting session for '%s'", name)
    session_manager.stop_printer(name)
    try:
        session_manager.start_printer(name)
        log.debug("update_printer_credentials: restarting session for '%s'", name)
        return f"Credentials updated and session restarted for '{name}'."
    except Exception as e:
        log.error("update_printer_credentials: session restart failed for %s: %s", name, e, exc_info=True)
        return f"Credentials updated for '{name}' but session restart failed: {e}"


def disconnect_printer(name: str, user_permission: bool = False) -> str:
    """
    Disconnect a printer's camera stream and MQTT session, keeping it configured.

    WHEN to use: take a printer offline from this server (stop its telemetry and camera stream)
    while keeping its credentials so it can be reconnected later without re-entering them.

    WRITE GUARD: stops the printer's MJPEG camera stream (if running), then stops its MQTT
    session, in that order, mirroring server._shutdown(). Live telemetry and every tool that
    needs a session stop working for this printer until ``start_printer`` reconnects it. An
    active print is not cancelled. With ``user_permission`` unset the tool changes nothing and
    returns the refusal string naming that consequence. A name that is not configured is refused
    and nothing is stopped.

    Sibling disambiguation: ``disconnect_printer`` tears the session down and keeps the credentials;
    ``start_printer`` reverses it. ``remove_printer`` also deletes the credentials.
    ``pause_mqtt_session`` only suspends telemetry on a live session (reversible with
    ``resume_mqtt_session``) and does not tear the session down or stop the camera stream.

    Args:
        name: Name of the printer to disconnect.
        user_permission: Set to True only after the user has explicitly approved disconnecting
            this printer's camera stream and MQTT session.

    Returns:
        A ``str`` in every case, never a dict. Success: ``"Printer '<name>' disconnected.
        Configuration retained; use start_printer('<name>') to reconnect."`` (returned even when
        the configured printer had no session or stream, and even if stopping the stream raised,
        which is only logged). Errors: ``"Error: Printer '<name>' is not configured."`` when ``name``
        has no configuration. Refused (``user_permission`` False): ``"Error: user_permission must
        be True to perform this action. <consequence>"``.

    Notes:
        ``start_printer`` restores the MQTT session but NOT the camera stream this tool stopped;
        call ``start_stream`` for that. The disconnect is in-memory only: the printer stays
        configured, so the next server restart starts its session again. The per-printer job
        monitor is NOT stopped: camera.job_monitor exposes only a global stop_all(), no per-printer
        stop, so it keeps running. That applies only to printers registered at server startup; one
        added later with ``add_printer`` has no monitor.
    """
    log.debug("disconnect_printer: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("disconnect_printer: permission denied for %s", name)
        return _permission_denied(
            "This would stop the printer's camera stream and MQTT session; credentials are kept "
            "but no telemetry flows until start_printer is called."
        )
    if name not in auth.get_configured_printer_names():
        log.warning("disconnect_printer: printer not configured: %s", name)
        return f"Error: Printer '{name}' is not configured."
    try:
        from camera.mjpeg_server import mjpeg_server
        stream_stopped = mjpeg_server.stop(name)
        log.debug("disconnect_printer: stream_stopped=%s for %s", stream_stopped, name)
    except Exception as e:
        log.warning("disconnect_printer: error stopping stream for %s: %s", name, e, exc_info=True)
    session_manager.stop_printer(name)
    log.info("disconnect_printer: session stopped for %s", name)
    return f"Printer '{name}' disconnected. Configuration retained; use start_printer('{name}') to reconnect."


def start_printer(name: str, user_permission: bool = False) -> str:
    """
    Start (or restart) the MQTT session for an already-configured printer.

    WHEN to use: reconnect a printer that ``disconnect_printer`` tore down, or force a clean
    session restart, without needing its credentials again.

    WRITE GUARD: opens an MQTT session to the printer using its stored credentials. If a session
    object already exists it is stopped first and then started again, so the call is a real
    restart and normally leaves no orphaned MQTT client. (If stopping the old session raises, the
    error is only logged and the session object is dropped regardless, which can leave the
    previous client's threads running.) Telemetry is briefly interrupted during a restart. With
    ``user_permission`` unset the tool changes nothing and returns the refusal
    string naming that consequence.

    Sibling disambiguation: ``start_printer`` reconnects an already-configured printer using its
    stored credentials. ``add_printer`` configures a NEW printer (and saves credentials).
    ``resume_mqtt_session`` un-pauses a session that ``pause_mqtt_session`` suspended, without
    rebuilding it. ``disconnect_printer`` is the inverse of this tool.

    Args:
        name: Name of the configured printer to start.
        user_permission: Set to True only after the user has explicitly approved starting or
            restarting this printer's MQTT session.

    Returns:
        A ``str`` in every case, never a dict. Success: ``"Printer '<name>' session started."`` or
        ``"Printer '<name>' session restarted."`` (when a session already existed). Errors:
        ``"Error: Printer '<name>' is not configured."`` when ``name`` has no configuration;
        ``"Error starting session for '<name>': <detail>"`` for credential, session-construction
        or session-start failures (the library raising from ``start_session``), never for an
        unreachable host; a failed start leaves no session registered. Refused (``user_permission`` False): ``"Error: user_permission must be True
        to perform this action. <consequence>"``.

    Notes:
        The success string means the session object was created and a connection attempt was made.
        An unreachable host or wrong ip does NOT produce an error: the library catches the connect
        failure, leaves service_state at QUIT and starts no session thread or watchdog, so nothing
        retries. Verify with ``get_printer_connection_status`` and call ``start_printer`` again
        once the printer is reachable.
    """
    log.debug("start_printer: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("start_printer: permission denied for %s", name)
        return _permission_denied(
            "This would open an MQTT session to the printer, replacing any existing session for it."
        )
    if name not in auth.get_configured_printer_names():
        log.warning("start_printer: printer not configured: %s", name)
        return f"Error: Printer '{name}' is not configured."
    try:
        restarted = False
        if session_manager.get_printer(name) is not None:
            # A session object already exists: stop it here so the result string
            # can say "restarted". SessionManager.start_printer() would also stop
            # it, but stop_printer() pops the entry, so the old client is quit
            # exactly once either way.
            session_manager.stop_printer(name)
            restarted = True
        session_manager.start_printer(name)
        log.info("start_printer: session %s for %s", "restarted" if restarted else "started", name)
        return f"Printer '{name}' session {'restarted' if restarted else 'started'}."
    except Exception as e:
        log.error("start_printer: error starting session for %s: %s", name, e, exc_info=True)
        return f"Error starting session for '{name}': {e}"


def get_printer_connection_status(name: str) -> dict:
    """
    Report the configuration, session and connection state of a single named printer.

    WHEN to use: check whether one printer is configured, has a session, and is actually
    connected and streaming telemetry, for example before a control call or when diagnosing why a
    printer looks offline.

    Sibling disambiguation: ``get_printer_connection_status`` inspects one printer by name and
    reports the real BPM service state and stream health. ``get_configured_printers`` lists every
    configured printer at once but with a coarser session flag. ``get_session_status`` (system
    module) also reports session state but only for a printer that has a live session, and
    returns an error dict for one that does not.

    Args:
        name: Printer name to inspect. An unconfigured name is accepted and reported as
            configured=False.

    Returns:
        ``{"name": str, "configured": bool, "session_active": bool, "connected": bool,
        "service_state": str | None, "recent_update": bool | None}``. There is no error shape for
        an unknown name: it returns configured=False, session_active=False, connected=False and
        None for the two optional fields.

    Notes:
        ``connected`` is True when the MQTT session is active and in CONNECTED state.
        ``session_active`` is True when a session object exists regardless of state.
        ``configured`` is True when the printer has stored credentials.
        ``service_state`` is the BPM service state name, or None when no session object exists
        (or the state could not be read).
        ``recent_update`` is meaningful only while ``service_state`` is CONNECTED. There the watchdog
        sets it False once no message arrived for watchdog_timeout seconds, then waits for the
        printer to re-announce itself. In any other state (PAUSED, DISCONNECTED, QUIT) it keeps its
        last value (a paused session can still read True) and it reads False on a session whose
        report stream never went live. It is set True only when an info/module reply arrives, not
        on every report message. It flags a stalled telemetry connection, not printer activity.
        None when no session object exists.
    """
    log.debug("get_printer_connection_status: called for name=%s", name)
    configured_names = auth.get_configured_printer_names()
    configured = name in configured_names
    printer = session_manager.get_printer(name)
    session_active = printer is not None
    connected = session_manager.is_connected(name)
    service_state = None
    recent_update = None
    if printer is not None:
        try:
            service_state = printer.service_state.name
        except Exception:
            pass
        try:
            recent_update = bool(printer.recent_update)
        except Exception:
            pass
    log.debug("get_printer_connection_status: %s -> configured=%s session_active=%s connected=%s service_state=%s recent_update=%s", name, configured, session_active, connected, service_state, recent_update)
    return {
        "name": name,
        "configured": configured,
        "session_active": session_active,
        "connected": connected,
        "service_state": service_state,
        "recent_update": recent_update,
    }
