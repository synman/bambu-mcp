"""
tools/print_control.py — Print control tools for Bambu Lab printers.

All tools in this module require user_permission=True to execute.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

from session_manager import session_manager


def _no_printer(name: str) -> str:
    return f"Error: Printer '{name}' not connected."


def _permission_denied() -> str:
    return "Error: user_permission must be True to perform this action."


# Speed level mapping: human-readable → firmware value string
_SPEED_LEVELS = {"quiet": "1", "standard": "2", "sport": "3", "ludicrous": "4"}


def pause_print(name: str, user_permission: bool = False) -> str:
    """
    Pause the current print job on the named printer.

    Sends a pause command via MQTT. The printer will finish the current move
    before stopping. Resume with resume_print(). Requires user_permission=True.
    """
    log.debug("pause_print: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("pause_print: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("pause_print: printer not connected: %s", name)
        return _no_printer(name)
    try:
        log.debug("pause_print: calling printer.pause_printing() for %s", name)
        printer.pause_printing()
        log.debug("pause_print: command sent to %s", name)
        return f"Pause command sent to '{name}'."
    except Exception as e:
        log.error("pause_print: error for %s: %s", name, e, exc_info=True)
        return f"Error pausing '{name}': {e}"


def resume_print(name: str, user_permission: bool = False) -> str:
    """
    Resume a paused print job on the named printer.

    Requires user_permission=True. Has no effect if the printer is not paused.
    """
    log.debug("resume_print: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("resume_print: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("resume_print: printer not connected: %s", name)
        return _no_printer(name)
    try:
        log.debug("resume_print: calling printer.resume_printing() for %s", name)
        printer.resume_printing()
        log.debug("resume_print: command sent to %s", name)
        return f"Resume command sent to '{name}'."
    except Exception as e:
        log.error("resume_print: error for %s: %s", name, e, exc_info=True)
        return f"Error resuming '{name}': {e}"


def stop_print(name: str, user_permission: bool = False) -> str:
    """
    Stop (cancel) the current print job on the named printer.

    This is a destructive operation — the print cannot be resumed after stopping.
    Requires user_permission=True.
    """
    log.debug("stop_print: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("stop_print: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("stop_print: printer not connected: %s", name)
        return _no_printer(name)
    try:
        log.debug("stop_print: calling printer.stop_printing() for %s", name)
        printer.stop_printing()
        log.debug("stop_print: command sent to %s", name)
        return f"Stop command sent to '{name}'."
    except Exception as e:
        log.error("stop_print: error for %s: %s", name, e, exc_info=True)
        return f"Error stopping '{name}': {e}"


def set_print_speed(
    name: str,
    speed_level: str,
    user_permission: bool = False,
) -> str:
    """
    Set the print speed profile on the named printer.

    speed_level must be one of: 'quiet', 'standard', 'sport', 'ludicrous'.
    These correspond to firmware speed codes 1–4. Requires user_permission=True.
    Quiet = reduced speed and acceleration (quieter operation, good for overnight prints).
    Standard = default balanced speed. Sport = faster than standard, slightly louder.
    Ludicrous = maximum speed, highest vibration and noise.
    """
    log.debug("set_print_speed: called for name=%s speed_level=%s user_permission=%s", name, speed_level, user_permission)
    if not user_permission:
        log.debug("set_print_speed: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("set_print_speed: printer not connected: %s", name)
        return _no_printer(name)
    code = _SPEED_LEVELS.get(speed_level.lower())
    if code is None:
        return f"Error: Invalid speed_level '{speed_level}'. Choose from: {list(_SPEED_LEVELS)}"
    try:
        log.debug("set_print_speed: calling printer.speed_level=%s for %s", code, name)
        printer.speed_level = code
        log.debug("set_print_speed: command sent to %s", name)
        return f"Speed level set to '{speed_level}' on '{name}'."
    except Exception as e:
        log.error("set_print_speed: error for %s: %s", name, e, exc_info=True)
        return f"Error setting speed on '{name}': {e}"


def skip_objects(
    name: str,
    object_list: list[int],
    user_permission: bool = False,
) -> str:
    """
    Skip (cancel) one or more objects during the current print job.

    object_list contains the identify_id values from the 3mf slice metadata.
    The printhead will physically avoid the skipped objects for the remainder
    of the print. Requires user_permission=True.

    How to get identify_id values: call get_project_info() for the current file and
    plate, then read metadata.map.bbox_objects[].id. Filter out entries whose name
    contains 'wipe_tower' to get the human-readable part list.
    Only works while a print is actively running (gcode_state="RUNNING").
    Objects cannot be un-skipped once skipped in the current print job.
    identify_id values are plate-specific and file-specific — do not reuse across
    different prints or different plates in the same file.
    """
    log.debug("skip_objects: called for name=%s object_list=%s user_permission=%s", name, object_list, user_permission)
    if not user_permission:
        log.debug("skip_objects: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("skip_objects: printer not connected: %s", name)
        return _no_printer(name)
    if not object_list:
        return "Error: object_list must not be empty."
    try:
        log.debug("skip_objects: calling printer.skip_objects(%s) for %s", object_list, name)
        printer.skip_objects(object_list)
        log.debug("skip_objects: command sent to %s", name)
        return f"Skip-objects command sent for IDs {object_list} on '{name}'."
    except Exception as e:
        log.error("skip_objects: error for %s: %s", name, e, exc_info=True)
        return f"Error skipping objects on '{name}': {e}"


def set_print_option(
    name: str,
    option: str,
    enabled: bool,
    user_permission: bool = False,
) -> str:
    """
    Enable or disable a print option on the named printer.

    Supported options: 'auto_recovery', 'filament_tangle_detect', 'sound_enable',
    'auto_switch_filament', 'nozzle_blob_detect', 'air_print_detect'.
    Requires user_permission=True.
    Options: 'auto_recovery' = resume print automatically after a power loss or failure.
    'filament_tangle_detect' = pause the print if sensors detect a filament tangle in the AMS.
    'sound_enable' = enable audible beep notifications. 'auto_switch_filament' = automatically
    load the next AMS slot when a spool runs out. 'nozzle_blob_detect' = pause the print if a
    filament blob accumulates on the nozzle. 'air_print_detect' = pause the print if the nozzle
    is detected extruding into open air (indicates a clog or grinding condition).
    """
    log.debug("set_print_option: called for name=%s option=%s enabled=%s user_permission=%s", name, option, enabled, user_permission)
    if not user_permission:
        log.debug("set_print_option: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("set_print_option: printer not connected: %s", name)
        return _no_printer(name)
    try:
        from bpm.bambutools import PrintOption
        option_map = {
            "auto_recovery": PrintOption.AUTO_RECOVERY,
            "filament_tangle_detect": PrintOption.FILAMENT_TANGLE_DETECT,
            "sound_enable": PrintOption.SOUND_ENABLE,
            "auto_switch_filament": PrintOption.AUTO_SWITCH_FILAMENT,
            "nozzle_blob_detect": PrintOption.NOZZLE_BLOB_DETECT,
            "air_print_detect": PrintOption.AIR_PRINT_DETECT,
        }
        po = option_map.get(option.lower())
        if po is None:
            return f"Error: Unknown option '{option}'. Supported: {list(option_map)}"
        log.debug("set_print_option: calling printer.set_print_option(%s, %s) for %s", option, enabled, name)
        printer.set_print_option(po, enabled)
        log.debug("set_print_option: command sent to %s", name)
        return f"Option '{option}' set to {enabled} on '{name}'."
    except Exception as e:
        log.error("set_print_option: error for %s: %s", name, e, exc_info=True)
        return f"Error setting option on '{name}': {e}"
