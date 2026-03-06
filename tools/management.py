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


def _permission_denied() -> str:
    return "Error: user_permission must be True to perform this action."


def get_configured_printers() -> dict:
    """
    Return all configured printer names along with their current connection status.

    A printer is 'connected' when its MQTT session is active and the service state
    is CONNECTED. A printer may be configured but not connected if it is unreachable.
    connected=True means the MQTT session is active and receiving telemetry.
    session_active=True means the session object exists in memory (but may not yet be
    connected, e.g. during startup or reconnection).
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
) -> str:
    """
    Add a new Bambu Lab printer: save credentials and start an MQTT session.

    name is a user-chosen identifier. ip is the printer's local IP address.
    serial is the hardware serial number. access_code is the 8-character LAN code
    shown in the printer's network settings. The printer session starts immediately.
    access_code is found on the printer touchscreen: Settings → Network → LAN → Access Code.
    If the access_code is wrong, the MQTT session will fail to authenticate and
    connected will be False.
    """
    log.debug("add_printer: called for name=%s ip=%s serial=%s access_code=<redacted>", name, ip, serial)
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
    Remove a printer: stop its MQTT session and delete stored credentials.

    This permanently removes the printer from the configured printer list.
    The printer itself is not affected. Requires user_permission=True.
    """
    log.debug("remove_printer: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("remove_printer: permission denied for %s", name)
        return _permission_denied()
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
    Update one or more credentials for an already-configured printer.

    Only the provided fields (ip, serial, access_code) are updated; omitted fields
    retain their current values. The session is restarted after a successful update.
    Updating credentials restarts the MQTT session. If a print is active, the session
    restart does NOT cancel it — the printer continues printing autonomously while
    reconnection completes.
    Requires user_permission=True.
    """
    log.debug("update_printer_credentials: called for name=%s access_code=%s", name, "<redacted>" if access_code is not None else "unchanged")
    if not user_permission:
        log.debug("update_printer_credentials: permission denied for %s", name)
        return _permission_denied()
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


def get_printer_connection_status(name: str) -> dict:
    """
    Return the connection and service state for a single named printer.

    'connected' is True when the MQTT session is active and in CONNECTED state.
    'session_active' is True when a session object exists regardless of state.
    'configured' is True when the printer has stored credentials.
    """
    log.debug("get_printer_connection_status: called for name=%s", name)
    configured_names = auth.get_configured_printer_names()
    configured = name in configured_names
    printer = session_manager.get_printer(name)
    session_active = printer is not None
    connected = session_manager.is_connected(name)
    service_state = None
    if printer is not None:
        try:
            service_state = printer.service_state.name
        except Exception:
            pass
    log.debug("get_printer_connection_status: %s -> configured=%s session_active=%s connected=%s service_state=%s", name, configured, session_active, connected, service_state)
    return {
        "name": name,
        "configured": configured,
        "session_active": session_active,
        "connected": connected,
        "service_state": service_state,
    }
