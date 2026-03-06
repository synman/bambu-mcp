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


def get_monitoring_history(name: str) -> dict:
    """
    Return telemetry history for charting: temperature and fan speed time-series.

    Data is provided as rolling 60-minute collections sampled every ~2.5 seconds.
    Also includes gcode_state_durations (time spent in each print state per job).

    Note on gcode_state_durations: a FAILED entry does not mean the current job failed.
    The rolling window captures the prior job's terminal state before the current job
    started. A print that has been RUNNING continuously will show a small FAILED duration
    from the previous job alongside its dominant RUNNING duration.
    """
    log.debug("get_monitoring_history: called for name=%s", name)
    data = data_collector.get_all_data(name)
    if data is None:
        log.warning("get_monitoring_history: printer %s not connected", name)
        return _no_printer(name)
    log.debug("get_monitoring_history: returning data for %s", name)
    return data


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
