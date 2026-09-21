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


def _permission_denied(consequence: str) -> str:
    return f"Error: user_permission must be True to perform this action. {consequence}"


def pause_print(name: str, user_permission: bool = False) -> str:
    """
    Pause the current print job on the named printer.

    WHEN to use: hold a running print so the operator can inspect it, change filament, or clear
    a problem, with the intent to continue the same job afterwards.

    WRITE GUARD: sends a pause command over MQTT that halts the running print job; the printer
    finishes the current move before stopping. With ``user_permission`` unset the tool changes
    nothing and returns the refusal string naming that consequence.

    Sibling disambiguation: ``pause_print`` is reversible with ``resume_print``; ``stop_print``
    cancels the job and it cannot be resumed. ``send_ams_control_command`` with cmd 'PAUSE'
    pauses the AMS feed rather than the print job.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        user_permission: Set to True after the operator approves pausing the print.

    Returns:
        A string. Success: ``"Pause command sent to '<name>'."``. Errors are strings starting
        with ``"Error"``: the refusal ``"Error: user_permission must be True to perform this
        action. <consequence>"``, ``"Error: Printer '<name>' not connected."``, or
        ``"Error pausing '<name>': <exception>"``.
    """
    log.debug("pause_print: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("pause_print: permission denied for %s", name)
        return _permission_denied(
            "This would pause the running print job; the printer finishes its current move and "
            "then holds until resume_print is called."
        )
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

    WHEN to use: continue a print that was paused by the user (stg_cur=17), by an M400 GCode
    pause (stg_cur=6), or by a non-AMS sensor pause (cover removed, temp malfunction) after the
    underlying condition is fixed. Has no effect if the printer is not paused.

    WRITE GUARD: sends a resume command over MQTT that restarts the paused job, so the toolhead
    starts moving again. With ``user_permission`` unset the tool changes nothing and returns the
    refusal string naming that consequence.

    Sibling disambiguation: for AMS-triggered pauses (filament runout stg_cur=7, or an active AMS
    HMS error) use ``send_ams_control_command`` with cmd 'RESUME' instead; it unblocks the AMS
    feed and resumes the print in one operation. ``pause_print`` is the opposite action.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        user_permission: Set to True after the operator approves resuming the print.

    Returns:
        A string. Success: ``"Resume command sent to '<name>'."``. Errors are strings starting
        with ``"Error"``: the refusal ``"Error: user_permission must be True to perform this
        action. <consequence>"``, ``"Error: Printer '<name>' not connected."``, or
        ``"Error resuming '<name>': <exception>"``.

    Notes:
        See kb_get('bambu-pause-state-recovery') for the full pause-cause decision table.
    """
    log.debug("resume_print: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("resume_print: permission denied for %s", name)
        return _permission_denied(
            "This would resume the paused print job, and the toolhead would start moving again."
        )
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

    WHEN to use: abandon a print for good, for example after a failure or a wrong file. It is
    destructive, so use ``pause_print`` instead when the job should continue later.

    WRITE GUARD: sends a stop command over MQTT that cancels the current print job; the print
    cannot be resumed after stopping. With ``user_permission`` unset the tool changes nothing
    and returns the refusal string naming that consequence.

    Sibling disambiguation: ``stop_print`` ends the whole job permanently; ``pause_print`` holds
    it and ``resume_print`` continues it. ``skip_objects`` cancels only chosen objects and lets
    the rest of the job finish.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        user_permission: Set to True after the operator approves cancelling the print.

    Returns:
        A string. Success: ``"Stop command sent to '<name>'."``. Errors are strings starting
        with ``"Error"``: the refusal ``"Error: user_permission must be True to perform this
        action. <consequence>"``, ``"Error: Printer '<name>' not connected."``, or
        ``"Error stopping '<name>': <exception>"``.
    """
    log.debug("stop_print: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("stop_print: permission denied for %s", name)
        return _permission_denied(
            "This would cancel the current print job permanently; a stopped print cannot be resumed."
        )
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

    WHEN to use: dismiss a lingering cancellation or fault error (e.g. HMS_0300-400C "task was
    canceled") before starting a new print.

    WRITE GUARD: sends two MQTT commands that reset the printer's print_error value and
    acknowledge the error dialog. With ``user_permission`` unset the tool changes nothing and
    returns the refusal string naming that consequence.

    Sibling disambiguation: ``get_hms_errors`` only reads the current HMS errors and the raw
    print_error code and changes nothing; ``clear_print_error`` sends the commands that dismiss
    the error. It does not resume or restart a job (see ``resume_print``).

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        print_error: The integer error code to clear. Pass 0 (default) to clear any active error
            without specifying a code. Use ``get_hms_errors`` to find the current print_error
            value.
        subtask_id: Optional subtask_id of the failed job. ``get_job_info`` does not return this
            field (its job info carries subtask_name, not subtask_id). Pass an empty string
            (default) if not known.
        user_permission: Set to True after the operator approves clearing the error.

    Returns:
        A string. Success: ``"clear_print_error command sent to '<name>' (print_error=<n>)."``.
        Errors are strings starting with ``"Error"``: the refusal ``"Error: user_permission must
        be True to perform this action. <consequence>"``, ``"Error: Printer '<name>' not
        connected."``, or ``"Error clearing print error on '<name>': <exception>"``.

    Notes:
        The tool sends TWO commands, matching the protocol BambuStudio uses when dismissing an
        error dialog. (1) clean_print_error clears the print_error value on the printer; the
        printer acknowledges by pushing a push_status with print_error reset to 0. (2) uiop (UI
        operation) signals "dialog acknowledged" to the printer. Without this second command the
        printer remains in a UI-acknowledgment pending state, and any open BambuStudio session
        re-raises print_error on every push_status until it receives this signal.
    """
    log.debug(
        "clear_print_error: called for name=%s print_error=%s subtask_id=%s user_permission=%s",
        name, print_error, subtask_id, user_permission,
    )
    if not user_permission:
        log.debug("clear_print_error: permission denied for %s", name)
        return _permission_denied(
            "This would send commands that clear the printer's active print_error and "
            "acknowledge its error dialog."
        )
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

    WHEN to use: change how fast the printer runs, for example a quiet profile for an overnight
    print or a faster one for a draft.

    WRITE GUARD: sends a print-speed command over MQTT that changes the printer's speed
    profile (toolhead speed, acceleration, noise and vibration). With ``user_permission`` unset
    the tool changes nothing and returns the refusal string naming that consequence.

    Sibling disambiguation: ``set_print_speed`` sets the overall speed profile (firmware codes
    1-4); ``set_fan_speed`` changes the speed of a single fan and leaves the speed profile as it
    is.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        speed_level: One of 'quiet', 'standard', 'sport', 'ludicrous' (case-insensitive),
            corresponding to firmware speed codes 1-4. Quiet = reduced speed and acceleration
            (quieter operation, good for overnight prints). Standard = default balanced speed.
            Sport = faster than standard, slightly louder. Ludicrous = maximum speed, highest
            vibration and noise.
        user_permission: Set to True after the operator approves changing the speed profile.

    Returns:
        A string. Success: ``"Speed level set to '<level>' on '<name>'."``. Errors are strings
        starting with ``"Error"``: the refusal ``"Error: user_permission must be True to perform
        this action. <consequence>"``, ``"Error: Printer '<name>' not connected."``,
        ``"Error: Invalid speed_level '<x>'. Choose from: [...]"``, or ``"Error setting speed on
        '<name>': <exception>"``.

    Notes:
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
        return _permission_denied(
            "This would change the printer's print speed profile, which changes toolhead speed, "
            "acceleration and noise."
        )
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

    WHEN to use: drop one or more failed or unwanted parts from a multi-object plate while the
    rest of the plate keeps printing.

    WRITE GUARD: sends a skip-objects command over MQTT; the printhead physically avoids the
    skipped objects for the remainder of the print, and objects cannot be un-skipped once
    skipped in the current print job. With ``user_permission`` unset the tool changes nothing
    and returns the refusal string naming that consequence.

    Sibling disambiguation: ``skip_objects`` cancels only the listed objects and the job keeps
    running; ``stop_print`` cancels the whole job. ``get_project_info`` supplies the object ids.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        object_list: The identify_id values from the 3mf slice metadata, one per object to skip.
            To get them, call ``get_project_info`` for the current file and plate, then read
            metadata.map.bbox_objects[].id. Filter out entries whose name contains 'wipe_tower'
            to get the human-readable part list. identify_id values are plate-specific and
            file-specific: do not reuse them across different prints or different plates in the
            same file. An empty list is rejected.
        user_permission: Set to True after the operator approves skipping the objects.

    Returns:
        A string. Success: ``"Skip-objects command sent for IDs <list> on '<name>'."``. Errors
        are strings starting with ``"Error"``: the refusal ``"Error: user_permission must be True
        to perform this action. <consequence>"``, ``"Error: Printer '<name>' not connected."``,
        ``"Error: object_list must not be empty."``, or ``"Error skipping objects on '<name>':
        <exception>"``.

    Notes:
        Only works while a print is actively running (gcode_state="RUNNING").
    """
    log.debug("skip_objects: called for name=%s object_list=%s user_permission=%s", name, object_list, user_permission)
    if not user_permission:
        log.debug("skip_objects: permission denied for %s", name)
        return _permission_denied(
            "This would cancel the listed objects in the running print; skipped objects cannot "
            "be un-skipped for that job."
        )
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

    WHEN to use: turn one of the printer-level print options on or off by name, for example
    auto recovery after a power loss or the AMS filament tangle detector.

    WRITE GUARD: sends an MQTT command that changes the named option on the printer, which
    changes how the printer reacts to faults, runouts and events. With ``user_permission`` unset
    the tool changes nothing and returns the refusal string naming that consequence. The printer
    library builds the MQTT payload from a shared, accumulating command template, so each call
    also re-sends every print option set earlier in this server process (and, once auto_recovery
    has been set, its ``option`` field), possibly re-applying a value since changed at the
    printer. The library updates its local config as soon as the command is published; that is
    not a confirmation from the printer.

    Sibling disambiguation: ``set_print_option`` sets one option by name from the list below and
    returns a string; ``set_print_options`` sets only the auto_recovery and sound flags together
    and returns a dict. For the AI-vision nozzle-clump and air-print detectors with sensitivity
    control, use ``set_nozzle_clumping_detection`` and ``set_air_printing_detection``.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        option: One of 'auto_recovery', 'filament_tangle_detect', 'sound_enable',
            'auto_switch_filament', 'nozzle_blob_detect', 'air_print_detect' (case-insensitive).
            The has_<x>_support capability flags named below are preconditions this tool does NOT
            check: an unsupported option is still published and still returns the success string.
            'auto_recovery' = resume print automatically after a power loss or hardware fault.
            Expects has_auto_recovery_support, which becomes True only once home_flag telemetry
            has been received.
            'filament_tangle_detect' = pause the print if AMS sensors detect a filament tangle.
            Requires has_filament_tangle_detect_support. Only meaningful when AMS is present and
            actively feeding: has no effect during external spool or standalone prints.
            'sound_enable' = enable audible beep notifications for print events. Requires
            has_sound_enable_support.
            'auto_switch_filament' = automatically switch to another AMS slot when the active
            spool runs out, provided a slot with the same filament type AND color is available.
            AMS-hosted spools only: external spool holder spools are not eligible. Requires
            has_auto_switch_filament_support (True when has_ams is True).
            'nozzle_blob_detect' = legacy firmware-level (home_flag) flag that pauses the print
            if a filament blob accumulates on the nozzle. This is the older control path. On
            printers that support it, prefer set_nozzle_clumping_detection() (xcam AI detector)
            which offers sensitivity control. Requires has_nozzle_blob_detect_support.
            'air_print_detect' = legacy firmware-level (home_flag) flag that pauses the print if
            the nozzle is detected extruding into open air (clog or grinding). Older control
            path. On printers that support it, prefer set_air_printing_detection() (xcam AI
            detector) which offers sensitivity control. Requires has_air_print_detect_support.
        enabled: True to enable the option, False to disable it.
        user_permission: Set to True after the operator approves changing the option.

    Returns:
        A string. Success: ``"Option '<option>' set to <enabled> on '<name>'."``. Errors are
        strings starting with ``"Error"``: the refusal ``"Error: user_permission must be True to
        perform this action. <consequence>"``, ``"Error: Printer '<name>' not connected."``,
        ``"Error: Unknown option '<option>'. Supported: [...]"``, or ``"Error setting option on
        '<name>': <exception>"``.
    """
    log.debug("set_print_option: called for name=%s option=%s enabled=%s user_permission=%s", name, option, enabled, user_permission)
    if not user_permission:
        log.debug("set_print_option: permission denied for %s", name)
        return _permission_denied(
            "This would turn the named print option (for example auto recovery or filament "
            "tangle detection) on or off on the printer."
        )
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

    WHEN to use: run a well-understood G-code command (homing, a manual move, heater off) that
    no dedicated tool covers, while the printer is idle. For standard operations (pause, speed,
    fan) prefer the dedicated tools instead.

    WRITE GUARD: sends raw G-code that the printer applies immediately to the hardware,
    bypassing all print-job safety checks. Incorrect commands can crash the toolhead, damage the
    printer, or trigger a fault. With ``user_permission`` unset the tool changes nothing and
    returns the refusal string naming that consequence. It is also blocked during active prints,
    judged from the last telemetry this server received: it blocks only when gcode_state reads
    RUNNING or PREPARE, and does NOT block when the state is empty or unreadable (before the
    first status report, or while the MQTT session is paused and the cached state is stale).
    Every other state, including PAUSE, is allowed through. When the block does not apply, the
    G-code is sent as-is and could crash the toolhead into the print or trigger hardware faults.

    Sibling disambiguation: ``send_gcode`` is the raw escape hatch. ``set_nozzle_temp``,
    ``set_bed_temp``, ``set_fan_speed`` and ``pause_print`` are the dedicated tools for the
    common cases; ``send_mqtt_command`` sends a raw MQTT JSON command instead of G-code and is not
    subject to the active-print block.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        gcode: A string containing one or more G-code commands separated by newlines. G-code
            (also written gcode) is the machine instruction language used to control 3D
            printers. Each command is a short text instruction like 'G28' (home all axes),
            'G0 X50 Y50' (move to position), or 'M104 S200' (set nozzle temperature).
            Examples:
              'G28'                      — home all axes
              'G91\\nG0 X10\\nG90'        — relative mode, move 10mm on X, back to absolute
              'M104 S0\\nM140 S0'         — turn off nozzle and bed heaters
        user_permission: Set to True after the operator approves sending the G-code.

    Returns:
        A string. Success: ``"G-code sent to '<name>'."``. Errors are strings: the refusal
        ``"Error: user_permission must be True to perform this action. <consequence>"``,
        ``"Error: Printer '<name>' not connected."``, the active-print block ``"Blocked: '<name>'
        is currently <RUNNING|PREPARE>. send_gcode is not safe while a print is active. Wait for
        the print to finish, pause it first, or cancel it."``, or ``"Error sending G-code to
        '<name>': <exception>"``.
    """
    log.debug("send_gcode: called for name=%s gcode=%r user_permission=%s", name, gcode, user_permission)
    if not user_permission:
        return _permission_denied(
            "This would send raw G-code that the printer executes immediately, bypassing "
            "print-job safety checks, and can crash the toolhead or fault the hardware."
        )
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

    WHEN to use: switch which saved extrusion (flow) calibration profile a filament slot uses,
    typically when the printer is idle and a spool has more than one saved profile.

    WRITE GUARD: sends an MQTT command that selects which saved extrusion (k-factor, flow
    dynamics) calibration profile the printer uses for the chosen tray.
    There is no active-print guard: using this tool while a print is active (gcode_state
    RUNNING/PREPARE) may interfere with the active job's flow settings, so prefer calling it only
    when the printer is idle. With ``user_permission`` unset the tool changes nothing and returns
    the refusal string naming that consequence.

    Sibling disambiguation: ``select_extrusion_calibration`` picks among saved calibration
    profiles only; ``set_ams_filament_setting`` changes the slot's filament identity fields, and
    ``get_spool_info`` (read-only) lists the loaded filaments and their tray_ids to use here.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        tray_id: The absolute tray identifier for the filament slot to calibrate, computed per AMS
            type from the hardware ams_id read live from ``get_ams_units`` (never hardcode):
              - 4-slot AMS (ams_id < 128): tray_id = ams_id * 4 + slot_id. AMS 2 Pro ams_id=0,
                slot 1 → 1; a second unit ams_id=1, slot 0 → 4.
              - AMS HT (ams_id >= 128): tray_id = ams_id + slot_id. ams_id=128, slot 0 → 128.
              - External spool holder → 254 (the only holder on a single-nozzle printer, the LEFT
                holder on a dual-nozzle printer); the RIGHT holder is 255.
            ``unit_index * 4 + slot_id`` is wrong for AMS HT. For an AMS HT tray_id the printer
            library derives the command's ams_id as floor(tray_id / 4) (32 for 128); this is not
            verified against firmware.
        cali_idx: The index of the saved calibration profile to activate. -1 (default) selects
            the default profile (bpm: "defaults to -1 (the default profile)"); whether the printer
            instead auto-selects a best match for the loaded filament is not established by this
            code. Use ``get_spool_info`` to see currently loaded filaments and their tray_ids
            before calling this.
        user_permission: Set to True after the operator approves changing the calibration.

    Returns:
        A string. Success: ``"Extrusion calibration profile selected for tray_id <n>
        (cali_idx=<m>) on '<name>'."``. Errors are strings starting with ``"Error"``: the refusal
        ``"Error: user_permission must be True to perform this action. <consequence>"``,
        ``"Error: Printer '<name>' not connected."``, or ``"Error selecting extrusion calibration
        profile on '<name>': <exception>"``.

    Notes:
        Bambu printers can store multiple extrusion calibration profiles per filament slot.
        Extrusion calibration (also called flow calibration) tunes the amount of filament pushed
        through the nozzle so the printed lines match the intended dimensions. The refusal string
        describes the change as altering the flow rate applied to the filament.
    """
    log.debug("select_extrusion_calibration: called for name=%s tray_id=%s cali_idx=%s user_permission=%s", name, tray_id, cali_idx, user_permission)
    if not user_permission:
        return _permission_denied(
            "This would change the extrusion calibration profile the printer uses for the "
            "chosen filament tray, which changes the flow rate applied to that filament."
        )
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
