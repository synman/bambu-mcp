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


def _permission_denied() -> str:
    return "Error: user_permission must be True to perform this action."


def get_climate(name: str) -> dict:
    """
    Return current and target temperatures for the bed, chamber, and all nozzles.

    Also includes the chamber door/lid open state and air conditioning mode when
    the printer hardware supports those features.
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

    temp is the target in °C. extruder selects which toolhead (0 = default/right,
    1 = left on H2D). Pass extruder=-1 to apply to all nozzles.
    extruder: 0 = the only extruder on single-nozzle printers, or the right nozzle on H2D
    (dual-extruder model). 1 = left nozzle on H2D only. -1 = set all nozzles.
    Requires user_permission=True.

    Idle nozzle timeout warning: in IDLE, FINISH, or FAILED gcode_states, the H2D firmware
    silently resets the nozzle target to 38°C after a calibrated timeout (~170s, [PROVISIONAL]).
    Camera scripts that heat nozzles while IDLE must use the heat_and_wait() pattern:
    two concurrent checks — proactive timer (re-assert at 75% of timeout) and reactive poll
    (verify target via GET /api/printer every 10s). Both use PATCH /api/set_tool_target_temp
    (HTTP Tier 1) — never raw send_gcode/M104. See behavioral_rules_camera_calibration
    knowledge module § "Idle Nozzle Heat Timeout".
    """
    log.debug("set_nozzle_temp: called for name=%s temp=%s extruder=%s user_permission=%s", name, temp, extruder, user_permission)
    if not user_permission:
        log.debug("set_nozzle_temp: permission denied for %s", name)
        return _permission_denied()
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

    temp is the target in °C. Use 0 to turn off bed heating.
    Pass 0 to turn off bed heating. The bed will cool passively — the print is not
    affected unless adhesion requires heat.
    Requires user_permission=True.
    """
    log.debug("set_bed_temp: called for name=%s temp=%s user_permission=%s", name, temp, user_permission)
    if not user_permission:
        log.debug("set_bed_temp: permission denied for %s", name)
        return _permission_denied()
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

    On printers with active chamber heating (e.g. H2D), this sets the chamber
    temperature target and sends an MQTT command to activate it. On printers without
    managed chamber heating (A1, P1S), this stores the target value — useful for
    external chamber management solutions that read the stored target and drive their
    own heating hardware. Requires user_permission=True.
    """
    log.debug("set_chamber_temp: called for name=%s temp=%s user_permission=%s", name, temp, user_permission)
    if not user_permission:
        log.debug("set_chamber_temp: permission denied for %s", name)
        return _permission_denied()
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

    Controls all available light nodes: chamber_light, chamber_light2, column_light.
    Requires user_permission=True.
    """
    log.debug("set_chamber_light: called for name=%s on=%s user_permission=%s", name, on, user_permission)
    if not user_permission:
        log.debug("set_chamber_light: permission denied for %s", name)
        return _permission_denied()
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

    Returns a dict with a single key 'on' (bool): True if the chamber light is
    currently on, False if off.
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

    fan must be one of: 'part_cooling', 'aux', 'exhaust'.
    - 'part_cooling': the fan that blows directly on the printed part to cool it.
      Critical for PLA and PETG; often disabled for ABS to prevent warping.
    - 'aux': the auxiliary recirculation fan inside the chamber. Helps regulate
      chamber temperature and filter air on printers with HEPA filters.
    - 'exhaust': the exhaust fan that vents chamber air out of the printer.
      Used to expel fumes when printing ABS, ASA, or other engineering filaments.
    speed_percent: integer 0–100. 0 = fan off, 100 = full speed.
    Requires user_permission=True.

    Note: These fan controls send M106 G-code commands internally. Fan speed
    set here may be overridden by the active print job's slicer settings.
    """
    log.debug("set_fan_speed: called for name=%s fan=%s speed_percent=%s user_permission=%s", name, fan, speed_percent, user_permission)
    if not user_permission:
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        return _no_printer(name)
    if not 0 <= speed_percent <= 100:
        return f"Error: speed_percent must be between 0 and 100, got {speed_percent}."
    fan_map = {
        "part_cooling": "set_part_cooling_fan_speed_target_percent",
        "aux": "set_aux_fan_speed_target_percent",
        "exhaust": "set_exhaust_fan_speed_target_percent",
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
