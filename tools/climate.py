"""
tools/climate.py — Temperature and climate control tools for Bambu Lab printers.

Read tools are always accessible. Write tools require user_permission=True.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

from session_manager import session_manager


def _no_printer(name: str) -> str:
    return f"Error: Printer '{name}' not connected."


def _permission_denied(consequence: str) -> str:
    return f"Error: user_permission must be True to perform this action. {consequence}"


def get_climate(name: str) -> dict:
    """
    Return current and target temperatures for the bed, chamber, and all nozzles.

    WHEN to use: check the thermal state of a printer, including the chamber door/lid
    open state and the air conditioning mode, before or while heating.

    Sibling disambiguation: ``get_climate`` returns the same nozzle, bed and chamber
    temperatures as ``get_temperatures`` and adds the chamber door/lid open state and
    the air conditioning mode (with the COOL_MODE caveat in Returns);
    ``get_temperatures`` returns temperatures only.
    ``get_fan_speeds`` reports fan percentages, which this tool does not.

    Args:
        name: Configured printer name (see ``get_configured_printers``).

    Returns:
        ``{nozzles: [{id, temp, target}], bed: {temp, target}, chamber: {temp, target},
        chamber_door_open, chamber_lid_open, air_conditioning_mode}`` on success.
        Single-nozzle printers return one nozzle entry with ``id`` -1; dual-extruder
        (H2D) printers return ``id`` 0 (right) and 1 (left). An entry with ``id`` 0
        built from the active nozzle temperature appears only before the first
        telemetry frame has been parsed. ``chamber_door_open`` and ``chamber_lid_open``
        are always present but meaningful only on printers with a chamber door sensor;
        without one they read False regardless of the real door/lid position.
        ``air_conditioning_mode`` is the mode name, except that ``"COOL_MODE"`` is
        reported as ``"NOT_SUPPORTED"``: the code tests the enum's truthiness and
        COOL_MODE is 0 (falsy). ``"NOT_SUPPORTED"`` therefore cannot tell a printer with
        no chamber AC from one in cool mode, including right after ``set_chamber_temp``
        below 40°C. Only ``"HEAT_MODE"`` is reliably reported by name.
        ``{"error": str}`` when the printer is not connected.
    """
    log.debug("get_climate: called for name=%s", name)
    state = session_manager.get_state(name)
    if state is None:
        log.warning("get_climate: printer %s not connected", name)
        return {"error": f"Printer '{name}' not connected"}
    c = state.climate
    nozzles = [
        {"id": e.id, "temp": e.temp, "target": e.temp_target}
        for e in (state.extruders or [])
    ]
    if not nozzles:
        nozzles = [{"id": 0, "temp": state.active_nozzle_temp, "target": state.active_nozzle_temp_target}]
    log.debug("get_climate: returning result for %s", name)
    return {
        "nozzles": nozzles,
        "bed": {"temp": c.bed_temp, "target": c.bed_temp_target},
        "chamber": {"temp": c.chamber_temp, "target": c.chamber_temp_target},
        "chamber_door_open": c.is_chamber_door_open,
        "chamber_lid_open": c.is_chamber_lid_open,
        "air_conditioning_mode": c.air_conditioning_mode.name
        if c.air_conditioning_mode
        else "NOT_SUPPORTED",
    }


def set_nozzle_temp(
    name: str,
    temp: float,
    extruder: int = 0,
    user_permission: bool = False,
) -> str:
    """
    Set the nozzle temperature target on the named printer.

    WHEN to use: heat a nozzle to a target, or set the target to 0 to stop heating it
    (for example preheating before a filament change or a calibration step).

    WRITE GUARD: sends an M104 G-code over MQTT that sets the nozzle temperature target
    and starts heating the nozzle. With ``user_permission`` False the tool changes
    nothing and returns the refusal string naming that consequence.

    Sibling disambiguation: ``set_nozzle_temp`` sets the nozzle target only;
    ``set_bed_temp`` sets the heated bed target and ``set_chamber_temp`` sets the chamber
    target. ``get_temperatures`` and ``get_climate`` read the resulting temperatures.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        temp: Target in °C. Truncated to an integer; negative values are clamped to 0
            by the printer library.
        extruder: Which toolhead to target. 0 = the only extruder on single-nozzle
            printers, or the right nozzle on H2D (dual-extruder model). 1 = the left
            nozzle on H2D only. -1 omits the T argument from the M104, so the printer
            applies the target itself (in practice the currently active tool); it does
            not send one command per nozzle and is not verified to heat both H2D
            nozzles. To set both, call twice with extruder 0 and 1. Default 0.
        user_permission: Must be True to execute. Default False.

    Returns:
        A ``str``. Success: ``"Nozzle temp target set to <temp>°C (extruder <n>) on
        '<name>'."``, where ``<temp>`` is the value as passed, not the value sent (the
        command carries int(temp); a negative target is clamped to 0 by the printer
        library). Read the target back with ``get_temperatures`` or ``get_climate``.
        Errors are ``"Error: ..."`` strings, never a dict: the
        ``_permission_denied`` refusal when ``user_permission`` is False,
        ``"Error: Printer '<name>' not connected."``, or
        ``"Error setting nozzle temp on '<name>': <exception>"`` when the command fails.

    Notes:
        Idle nozzle timeout warning: in IDLE, FINISH, or FAILED gcode_states, the H2D
        firmware silently resets the nozzle target to 38°C after a calibrated timeout
        (~170s, [PROVISIONAL]). Camera scripts that heat nozzles while IDLE must use the
        heat_and_wait() pattern: two concurrent checks — proactive timer (re-assert at 75%
        of timeout) and reactive poll (verify target via GET /api/printer every 10s). Both
        use PATCH /api/set_tool_target_temp (HTTP Tier 1) — never raw send_gcode/M104. See
        ``calibration/calibrate_idle_nozzle_timeout.py`` in this repo, or the corresponding
        node-kb-mcp ``bambu-*`` article.
    """
    log.debug("set_nozzle_temp: called for name=%s temp=%s extruder=%s user_permission=%s", name, temp, extruder, user_permission)
    if not user_permission:
        log.debug("set_nozzle_temp: permission denied for %s", name)
        return _permission_denied(
            "This would set the nozzle temperature target and start heating the nozzle."
        )
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("set_nozzle_temp: printer not connected: %s", name)
        return _no_printer(name)
    try:
        log.debug("set_nozzle_temp: calling printer.set_nozzle_temp_target(%s, tool_num=%s) for %s", int(temp), extruder, name)
        printer.set_nozzle_temp_target(int(temp), tool_num=extruder)
        log.debug("set_nozzle_temp: command sent to %s", name)
        return f"Nozzle temp target set to {temp}°C (extruder {extruder}) on '{name}'."
    except Exception as e:
        log.error("set_nozzle_temp: error for %s: %s", name, e, exc_info=True)
        return f"Error setting nozzle temp on '{name}': {e}"


def set_bed_temp(
    name: str,
    temp: float,
    user_permission: bool = False,
) -> str:
    """
    Set the heated bed temperature target on the named printer.

    WHEN to use: preheat the bed, change its target, or turn bed heating off (temp 0).

    WRITE GUARD: sends an M140 G-code over MQTT that sets the bed temperature target and
    starts heating the bed (temp 0 turns bed heating off). With ``user_permission`` False
    the tool changes nothing and returns the refusal string naming that consequence.

    Sibling disambiguation: ``set_bed_temp`` sets the heated bed target only;
    ``set_nozzle_temp`` sets a nozzle target and ``set_chamber_temp`` sets the chamber
    target. ``get_temperatures`` and ``get_climate`` read the resulting temperatures.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        temp: Target in °C. Truncated to an integer; negative values are clamped to 0
            by the printer library. Use 0 to turn off bed heating.
        user_permission: Must be True to execute. Default False.

    Returns:
        A ``str``. Success: ``"Bed temp target set to <temp>°C on '<name>'."``, where
        ``<temp>`` is the value as passed, not the value sent (the command carries
        int(temp); a negative target is clamped to 0 by the printer library). Read it
        back with ``get_temperatures`` or ``get_climate``. Errors are
        ``"Error: ..."`` strings, never a dict: the ``_permission_denied`` refusal when
        ``user_permission`` is False, ``"Error: Printer '<name>' not connected."``, or
        ``"Error setting bed temp on '<name>': <exception>"`` when the command fails.

    Notes:
        With temp 0 the bed cools passively — the print is not affected unless adhesion
        requires heat.
    """
    log.debug("set_bed_temp: called for name=%s temp=%s user_permission=%s", name, temp, user_permission)
    if not user_permission:
        log.debug("set_bed_temp: permission denied for %s", name)
        return _permission_denied(
            "This would set the bed temperature target and start heating the bed."
        )
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("set_bed_temp: printer not connected: %s", name)
        return _no_printer(name)
    try:
        log.debug("set_bed_temp: calling printer.set_bed_temp_target(%s) for %s", int(temp), name)
        printer.set_bed_temp_target(int(temp))
        log.debug("set_bed_temp: command sent to %s", name)
        return f"Bed temp target set to {temp}°C on '{name}'."
    except Exception as e:
        log.error("set_bed_temp: error for %s: %s", name, e, exc_info=True)
        return f"Error setting bed temp on '{name}': {e}"


def set_chamber_temp(
    name: str,
    temp: float,
    user_permission: bool = False,
) -> str:
    """
    Set the chamber temperature target on the named printer.

    WHEN to use: set the chamber heating target on a printer with active chamber
    heating, or record a chamber target for external chamber management on a printer
    without it.

    WRITE GUARD: on printers with active chamber heating (e.g. H2D) this sends MQTT
    commands that set the chamber temperature target and the chamber air conditioning
    mode (mode 0 for a target below 40°C, mode 1 otherwise), which starts or stops chamber
    heating. On printers without managed chamber heating (A1, P1S) it only stores the
    target value in the server's copy of the printer state; nothing is sent to the
    printer. With ``user_permission`` False the tool changes nothing and returns the
    refusal string naming that consequence.

    Sibling disambiguation: ``set_chamber_temp`` sets the chamber target only;
    ``set_bed_temp`` sets the heated bed target and ``set_nozzle_temp`` sets a nozzle
    target. ``get_climate`` reads the chamber target and, subject to the COOL_MODE caveat
    in its Returns, the air conditioning mode.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        temp: Target in °C, truncated to an integer.
        user_permission: Must be True to execute. Default False.

    Returns:
        A ``str``. Success: ``"Chamber temp target set to <temp>°C on '<name>'."``, where
        ``<temp>`` is the value as passed, not the value sent (the command carries
        int(temp)). Read it back with ``get_climate``. Errors
        are ``"Error: ..."`` strings, never a dict: the ``_permission_denied`` refusal when
        ``user_permission`` is False, ``"Error: Printer '<name>' not connected."``, or
        ``"Error setting chamber temp on '<name>': <exception>"`` when the command fails.

    Notes:
        The stored target on printers without managed chamber heating is useful for
        external chamber management solutions that read it and drive their own heating
        hardware.
    """
    log.debug("set_chamber_temp: called for name=%s temp=%s user_permission=%s", name, temp, user_permission)
    if not user_permission:
        log.debug("set_chamber_temp: permission denied for %s", name)
        return _permission_denied(
            "This would set the chamber temperature target and, on printers with "
            "active chamber heating, start or stop chamber heating."
        )
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("set_chamber_temp: printer not connected: %s", name)
        return _no_printer(name)
    try:
        log.debug("set_chamber_temp: calling printer.set_chamber_temp_target(%s) for %s", int(temp), name)
        printer.set_chamber_temp_target(int(temp))
        log.debug("set_chamber_temp: command sent to %s", name)
        return f"Chamber temp target set to {temp}°C on '{name}'."
    except Exception as e:
        log.error("set_chamber_temp: error for %s: %s", name, e, exc_info=True)
        return f"Error setting chamber temp on '{name}': {e}"


def set_chamber_light(
    name: str,
    on: bool,
    user_permission: bool = False,
) -> str:
    """
    Turn the chamber light(s) on or off on the named printer.

    WHEN to use: switch the printer's lights, for example off before a camera calibration
    capture or on before viewing the chamber.

    WRITE GUARD: sends the light command over MQTT to every light node (chamber_light,
    chamber_light2, column_light), turning them all on or all off. With ``user_permission``
    False the tool changes nothing and returns the refusal string naming that consequence.

    Sibling disambiguation: ``set_chamber_light`` switches the lights;
    ``get_chamber_light`` only reads the state reported by the first light node.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        on: True to turn the lights on, False to turn them off.
        user_permission: Must be True to execute. Default False.

    Returns:
        A ``str``. Success: ``"Chamber light turned on|off on '<name>'."``. Errors are
        ``"Error: ..."`` strings, never a dict: the ``_permission_denied`` refusal when
        ``user_permission`` is False, ``"Error: Printer '<name>' not connected."``, or
        ``"Error setting chamber light on '<name>': <exception>"`` when the command fails.

    Notes:
        A ``get_chamber_light`` read straight after this call can still return the
        previous value: the setter does not update the state that tool reads, which
        changes only when the printer next reports its lights.
    """
    log.debug("set_chamber_light: called for name=%s on=%s user_permission=%s", name, on, user_permission)
    if not user_permission:
        log.debug("set_chamber_light: permission denied for %s", name)
        return _permission_denied(
            "This would turn all of the printer's chamber lights on or off."
        )
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("set_chamber_light: printer not connected: %s", name)
        return _no_printer(name)
    try:
        log.debug("set_chamber_light: calling printer.light_state=%s for %s", on, name)
        printer.light_state = on
        state_str = "on" if on else "off"
        log.debug("set_chamber_light: command sent to %s", name)
        return f"Chamber light turned {state_str} on '{name}'."
    except Exception as e:
        log.error("set_chamber_light: error for %s: %s", name, e, exc_info=True)
        return f"Error setting chamber light on '{name}': {e}"


def get_chamber_light(name: str) -> dict:
    """
    Return whether the chamber light is currently on for the named printer.

    WHEN to use: check the light state before capturing a camera frame or before
    toggling it.

    Sibling disambiguation: ``get_chamber_light`` only reads the light state, as reported
    by the printer's first light node; ``set_chamber_light`` changes all light nodes and
    requires ``user_permission``.

    Args:
        name: Configured printer name (see ``get_configured_printers``).

    Returns:
        ``{"on": bool}`` on success: True if the chamber light is currently on, False
        otherwise. False also means no light report has arrived yet. ``{"error": str}``
        when the printer is not connected or reading the state raises.

    Notes:
        The value comes from the first entry of the printer's ``lights_report``
        telemetry, so it reflects only that one light node. ``set_chamber_light`` does
        not update it: a read immediately after a set returns the previous value until
        the printer reports back.
    """
    log.debug("get_chamber_light: called for name=%s", name)
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("get_chamber_light: printer not connected: %s", name)
        return {"error": f"Printer '{name}' not connected"}
    try:
        result = {"on": printer.light_state}
        log.debug("get_chamber_light: returning result for %s", name)
        return result
    except Exception as e:
        return {"error": str(e)}


def set_fan_speed(
    name: str,
    fan: str,
    speed_percent: int,
    user_permission: bool = False,
) -> str:
    """
    Set the speed of a specific fan on the printer.

    WHEN to use: change the part cooling, auxiliary, exhaust or enhanced cooling fan
    speed on a printer, for example to vent fumes or adjust cooling for the filament.

    WRITE GUARD: sends an M106 G-code over MQTT that changes the chosen fan's speed
    immediately, which can alter part cooling or chamber ventilation for a running print.
    With ``user_permission`` False the tool changes nothing and returns the refusal string
    naming that consequence.

    Sibling disambiguation: ``set_fan_speed`` changes one fan's speed; ``get_fan_speeds``
    reads the speeds of all fans. ``set_nozzle_temp``, ``set_bed_temp`` and
    ``set_chamber_temp`` set temperature targets, not fans.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        fan: One of 'part_cooling', 'aux', 'exhaust', 'enhanced_cooling' (case-insensitive).
            - 'part_cooling': the fan that blows directly on the printed part to cool it.
              Critical for PLA and PETG; often disabled for ABS to prevent warping.
            - 'aux': the auxiliary recirculation fan inside the chamber. Helps regulate
              chamber temperature and filter air on printers with HEPA filters.
            - 'exhaust': the exhaust fan that vents chamber air out of the printer.
              Used to expel fumes when printing ABS, ASA, or other engineering filaments.
            - 'enhanced_cooling': the Toolhead Enhanced Cooling Fan (M106 P9), present only
              on H2-series printers with the extension-tool module attached. The printer
              publishes no run-state telemetry for this fan — the commanded value is
              sticky (see get_fan_speeds()'s enhanced_cooling_pct). Firmware-observed
              behavior is effectively on/off; a command sent while the fan is unplugged
              is acknowledged by the printer as a harmless no-op.
        speed_percent: Integer 0–100. 0 = fan off, 100 = full speed.
        user_permission: Must be True to execute. Default False.

    Returns:
        A ``str``. Success: ``"<fan> fan set to <speed_percent>% on '<name>'."``. Errors are
        ``"Error: ..."`` strings, never a dict: the ``_permission_denied`` refusal when
        ``user_permission`` is False, ``"Error: Printer '<name>' not connected."``,
        ``"Error: speed_percent must be between 0 and 100, got <n>."``,
        ``"Error: Unknown fan '<fan>'. Valid values: [...]"``, or
        ``"Error setting <fan> fan speed on '<name>': <exception>"`` when the command fails.

    Notes:
        These fan controls send M106 G-code commands internally. Fan speed set here may be
        overridden by the active print job's slicer settings.
    """
    log.debug("set_fan_speed: called for name=%s fan=%s speed_percent=%s user_permission=%s", name, fan, speed_percent, user_permission)
    if not user_permission:
        return _permission_denied(
            "This would change the speed of the selected fan, which can alter cooling "
            "or ventilation for a running print."
        )
    printer = session_manager.get_printer(name)
    if printer is None:
        return _no_printer(name)
    if not 0 <= speed_percent <= 100:
        return f"Error: speed_percent must be between 0 and 100, got {speed_percent}."
    fan_map = {
        "part_cooling": "set_part_cooling_fan_speed_target_percent",
        "aux": "set_aux_fan_speed_target_percent",
        "exhaust": "set_exhaust_fan_speed_target_percent",
        "enhanced_cooling": "set_enhanced_cooling_fan_speed_target_percent",
    }
    fan_key = fan.lower()
    if fan_key not in fan_map:
        return f"Error: Unknown fan '{fan}'. Valid values: {list(fan_map.keys())}"
    try:
        method = getattr(printer, fan_map[fan_key])
        method(speed_percent)
        log.debug("set_fan_speed: set %s fan to %s%% on %s", fan_key, speed_percent, name)
        return f"{fan_key} fan set to {speed_percent}% on '{name}'."
    except Exception as e:
        log.error("set_fan_speed: error for %s: %s", name, e, exc_info=True)
        return f"Error setting {fan_key} fan speed on '{name}': {e}"
