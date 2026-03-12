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

    Use this for user-initiated pauses (stg_cur=17), M400 GCode pauses (stg_cur=6),
    and non-AMS sensor pauses (cover removed, temp malfunction — after fixing the
    underlying condition).

    For AMS-triggered pauses (filament runout stg_cur=7, or active AMS HMS error),
    use send_ams_control_command(RESUME) instead — it unblocks the AMS feed and
    resumes the print in one operation.

    See get_knowledge_topic('behavioral_rules/print_state') for the full pause-cause
    decision table.
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


def clear_print_error(
    name: str,
    print_error: int = 0,
    subtask_id: str = "",
    user_permission: bool = False,
) -> str:
    """
    Clear an active print_error on the named printer.

    Sends a clean_print_error command to the printer. The printer acknowledges
    by pushing a push_status with print_error reset to 0. Use this to dismiss
    a lingering cancellation or fault error (e.g. HMS_0300-400C "task was
    canceled") before starting a new print.

    This tool sends TWO commands, matching the protocol BambuStudio uses when
    dismissing an error dialog:
      1. clean_print_error — clears the print_error value on the printer.
      2. uiop (UI operation) — signals "dialog acknowledged" to the printer.
         Without this second command, the printer remains in a UI-acknowledgment
         pending state and any open BambuStudio session will re-raise print_error
         on every push_status until it receives this signal.

    print_error: the integer error code to clear. Pass 0 to clear any active
        error without specifying a code. Use get_hms_errors() to find the
        current print_error value.
    subtask_id: optional subtask_id of the failed job from get_job_info().
        Pass empty string if not known.
    Requires user_permission=True.
    """
    log.debug(
        "clear_print_error: called for name=%s print_error=%s subtask_id=%s user_permission=%s",
        name, print_error, subtask_id, user_permission,
    )
    if not user_permission:
        log.debug("clear_print_error: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("clear_print_error: printer not connected: %s", name)
        return _no_printer(name)
    try:
        log.debug(
            "clear_print_error: calling printer.clean_print_error() for %s print_error=%s",
            name, print_error,
        )
        printer.clean_print_error(subtask_id=subtask_id, print_error=print_error)
        printer.clean_print_error_uiop(print_error=print_error)
        log.debug("clear_print_error: clean_print_error + uiop sent to %s print_error=%08X", name, print_error)
        return f"clear_print_error command sent to '{name}' (print_error={print_error})."
    except Exception as e:
        log.error("clear_print_error: error for %s: %s", name, e, exc_info=True)
        return f"Error clearing print error on '{name}': {e}"


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

    Sticky preference: before suggesting a speed level, look up the stored value:
      from user_prefs import get_pref
      speed_level = get_pref(f"{name}:speed_level", None)
    If a stored preference exists, present it pre-selected labeled "(your preference)".
    If no preference is stored, show all options without a pre-selection.
    After a successful call, store the confirmed speed level:
      from user_prefs import set_pref
      set_pref(f"{name}:speed_level", speed_level)
    """
    log.debug("set_print_speed: called for name=%s speed_level=%s user_permission=%s", name, speed_level, user_permission)
    if not user_permission:
        log.debug("set_print_speed: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("set_print_speed: printer not connected: %s", name)
        return _no_printer(name)
    try:
        from bpm.bambutools import SpeedLevel
        lvl = SpeedLevel[speed_level.upper()]
    except KeyError:
        valid = [e.name.lower() for e in SpeedLevel]
        return f"Error: Invalid speed_level '{speed_level}'. Choose from: {valid}"
    try:
        log.debug("set_print_speed: calling printer.speed_level=%s for %s", lvl, name)
        printer.speed_level = lvl
        log.debug("set_print_speed: command sent to %s", name)
        return f"Speed level set to '{lvl.name.lower()}' on '{name}'."
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
    Options:
    'auto_recovery' = resume print automatically after a power loss or hardware fault.
      Requires has_auto_recovery_support (always True on all printers).
    'filament_tangle_detect' = pause the print if AMS sensors detect a filament tangle.
      Requires has_filament_tangle_detect_support. Only meaningful when AMS is present
      and actively feeding — has no effect during external spool or standalone prints.
    'sound_enable' = enable audible beep notifications for print events.
      Requires has_sound_enable_support.
    'auto_switch_filament' = automatically switch to another AMS slot when the active
      spool runs out, provided a slot with the same filament type AND color is available.
      AMS-hosted spools only — external spool holder spools are not eligible.
      Requires has_auto_switch_filament_support (True when has_ams is True).
    'nozzle_blob_detect' = legacy firmware-level (home_flag) flag that pauses the print
      if a filament blob accumulates on the nozzle. This is the older control path.
      On printers that support it, prefer set_nozzle_clumping_detection() (xcam AI detector)
      which offers sensitivity control. Requires has_nozzle_blob_detect_support.
    'air_print_detect' = legacy firmware-level (home_flag) flag that pauses the print if
      the nozzle is detected extruding into open air (clog or grinding). Older control path.
      On printers that support it, prefer set_air_printing_detection() (xcam AI detector)
      which offers sensitivity control. Requires has_air_print_detect_support.
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


def send_gcode(
    name: str,
    gcode: str,
    user_permission: bool = False,
) -> str:
    """
    Send one or more raw G-code commands to the printer.

    G-code (also written gcode) is the machine instruction language used to
    control 3D printers. Each command is a short text instruction like 'G28'
    (home all axes), 'G0 X50 Y50' (move to position), or 'M104 S200' (set
    nozzle temperature). Commands are newline-separated when sending multiple.

    gcode: a string containing one or more G-code commands separated by newlines.
    Examples:
      'G28'                      — home all axes
      'G91\\nG0 X10\\nG90'        — relative mode, move 10mm on X, back to absolute
      'M104 S0\\nM140 S0'         — turn off nozzle and bed heaters

    WARNING: G-code commands bypass all print-job safety checks and are applied
    immediately to the hardware. Incorrect commands can crash the toolhead,
    damage the printer, or trigger a fault. Only use this for well-understood
    G-code. For standard print operations (pause, speed, fan), prefer the
    dedicated tools instead.
    Requires user_permission=True.

    ⛔ BLOCKED during active prints (gcode_state RUNNING or PREPARE).
    Firmware does NOT reject injected G-code — commands execute immediately
    and can crash the toolhead into the print or trigger hardware faults.
    """
    log.debug("send_gcode: called for name=%s gcode=%r user_permission=%s", name, gcode, user_permission)
    if not user_permission:
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        return _no_printer(name)
    from tools._guards import check_active_print_guard
    blocked = check_active_print_guard(printer, name, "send_gcode")
    if blocked:
        return blocked.get("error", "Blocked: active print in progress.")
    try:
        printer.send_gcode(gcode)
        log.debug("send_gcode: sent gcode to %s: %r", name, gcode)
        return f"G-code sent to '{name}'."
    except Exception as e:
        log.error("send_gcode: error for %s: %s", name, e, exc_info=True)
        return f"Error sending G-code to '{name}': {e}"


def select_extrusion_calibration(
    name: str,
    tray_id: int,
    cali_idx: int = -1,
    user_permission: bool = False,
) -> str:
    """
    Select an extrusion calibration profile for a specific filament spool.

    Bambu printers can store multiple extrusion calibration profiles per
    filament slot. Extrusion calibration (also called flow calibration) tunes
    the exact amount of filament pushed through the nozzle to ensure the
    printed lines match the intended dimensions.

    tray_id: the absolute tray identifier for the filament slot to calibrate.
    Encoding: ams_unit_index * 4 + slot (0–3). Examples:
      - 0 = AMS unit 0, slot 0 (first AMS, first slot)
      - 1 = AMS unit 0, slot 1
      - 4 = AMS unit 1, slot 0 (second AMS, first slot)
      - 254 = external spool holder
    cali_idx: the index of the saved calibration profile to activate. Use -1
    to let the printer automatically select the best matching profile for the
    loaded filament. Use get_spool_info() to see currently loaded filaments
    and their tray_ids before calling this.
    Requires user_permission=True.

    ⚠️ CAUTION: Using this tool while a print is active (gcode_state RUNNING/PREPARE)
    may interfere with the active job's flow settings. Prefer calling this only
    when the printer is idle.
    """
    log.debug("select_extrusion_calibration: called for name=%s tray_id=%s cali_idx=%s user_permission=%s", name, tray_id, cali_idx, user_permission)
    if not user_permission:
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        return _no_printer(name)
    try:
        printer.select_extrusion_calibration_profile(tray_id, cali_idx)
        log.debug("select_extrusion_calibration: command sent to %s tray_id=%s cali_idx=%s", name, tray_id, cali_idx)
        return f"Extrusion calibration profile selected for tray_id {tray_id} (cali_idx={cali_idx}) on '{name}'."
    except Exception as e:
        log.error("select_extrusion_calibration: error for %s: %s", name, e, exc_info=True)
        return f"Error selecting extrusion calibration profile on '{name}': {e}"
