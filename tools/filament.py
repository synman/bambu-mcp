"""
tools/filament.py — AMS and filament management tools for Bambu Lab printers.

Read tools are always accessible. Write tools require user_permission=True.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from enum import Enum

log = logging.getLogger(__name__)

from session_manager import session_manager


def _to_dict(obj):
    """Recursively convert a dataclass to a JSON-safe dict, preserving Enum names.

    Must check Enum before dataclass/int because IntEnum is both an Enum and an int;
    dataclasses.asdict() strips IntEnum to plain int before Enum checks can fire.
    """
    if isinstance(obj, Enum):
        return obj.name
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_dict(v) for v in obj]
    return obj


def _serialize(obj):
    """Convert a dataclass (with nested Enum fields) to a JSON-safe dict."""
    if obj is None:
        return None
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return json.loads(json.dumps(_to_dict(obj), default=str))
    if isinstance(obj, Enum):
        return obj.name
    return obj


def _no_printer(name: str) -> str:
    return f"Error: Printer '{name}' not connected."


def _permission_denied() -> str:
    return "Error: user_permission must be True to perform this action."


def _resolve_ams_id(name: str, unit_id: int) -> int | None:
    """Resolve hardware ams_id from a 0-based positional unit_id or a raw ams_id."""
    log.debug("_resolve_ams_id: called for name=%s unit_id=%s", name, unit_id)
    state = session_manager.get_state(name)
    if state is None or not state.ams_units:
        log.debug("_resolve_ams_id: no state/ams_units for %s", name)
        return None
    # Try positional index first
    if 0 <= unit_id < len(state.ams_units):
        result = state.ams_units[unit_id].ams_id
        log.debug("_resolve_ams_id: positional index result ams_id=%s for %s unit_id=%s", result, name, unit_id)
        return result
    # Fall back: treat unit_id as a raw hardware ams_id
    unit = next((u for u in state.ams_units if u.ams_id == unit_id), None)
    result = unit.ams_id if unit is not None else None
    log.debug("_resolve_ams_id: raw ams_id lookup result=%s for %s unit_id=%s", result, name, unit_id)
    return result


def get_ams_units(name: str) -> dict:
    """
    Return all AMS units and their slot states for the named printer.

    Each unit includes temperature, humidity, heater state, drying status, and
    the presence/absence of filament in each of the four slots.

    Field semantics:
    - unit_id in the returned data is the 0-based user-facing index (0 = first AMS,
      1 = second, etc.). This is NOT the same as the internal chip_id used in spool
      active_ams_id fields (AMS 2 Pro chip_id starts at 0; AMS HT chip_id starts at 128).
    - Each unit has 4 slots (0–3). Slot presence is indicated by tray_exist flags.
    - The AMS model is identified by the `model` field (AMSModel enum name, e.g.
      'AMS_2_PRO', 'AMS_HT'). See the enums knowledge module for all values.
    - On H2D: AMS 2 Pro (chip_id 0, unit_id 0) feeds the RIGHT extruder (extruder 0);
      AMS HT (chip_id 128, unit_id 1) feeds the LEFT extruder (extruder 1).
    """
    log.debug("get_ams_units: called for name=%s", name)
    state = session_manager.get_state(name)
    if state is None:
        log.warning("get_ams_units: printer %s not connected", name)
        return {"error": f"Printer '{name}' not connected"}
    log.debug("get_ams_units: returning result for %s", name)
    return {
        "ams_count": state.ams_connected_count,
        "ams_status": state.ams_status_text,
        "units": [_serialize(u) for u in (state.ams_units or [])],
    }


def set_ams_filament_setting(
    name: str,
    unit_id: int,
    slot_id: int,
    filament_id: str = "",
    filament_name: str = "",
    filament_type: str = "",
    color: str = "",
    nozzle_temp_min: int = -1,
    nozzle_temp_max: int = -1,
    user_permission: bool = False,
) -> str:
    """
    Set filament details for a specific AMS slot on the named printer.

    unit_id is the AMS unit index (0-based). slot_id is the slot within that unit (0-3).
    filament_id is the Bambu Lab tray_info_idx (e.g. 'GFA00'). Pass 'no_filament' to
    clear the slot. color may be a CSS name or RRGGBB hex string.
    filament_id is the Bambu Lab catalog material code (tray_info_idx), e.g. 'GFA00' for
    Bambu PLA Basic. This is a lookup key from Bambu's filament database and determines
    temperature profiles and color defaults. Pass 'no_filament' to mark the slot as empty.
    Pass -1 for nozzle_temp_min or nozzle_temp_max to keep the existing value or let the
    printer use the filament_id defaults.
    Requires user_permission=True.
    """
    log.debug("set_ams_filament_setting: called for name=%s unit_id=%s slot_id=%s", name, unit_id, slot_id)
    if not user_permission:
        log.debug("set_ams_filament_setting: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("set_ams_filament_setting: printer not connected: %s", name)
        return _no_printer(name)
    ams_id = _resolve_ams_id(name, unit_id)
    if ams_id is None:
        return f"Error: AMS unit {unit_id} not found on '{name}'."
    tray_id = ams_id * 4 + slot_id
    try:
        log.debug("set_ams_filament_setting: calling printer.set_spool_details for %s", name)
        printer.set_spool_details(
            tray_id=tray_id,
            tray_info_idx=filament_id,
            tray_id_name=filament_name,
            tray_type=filament_type,
            tray_color=color,
            nozzle_temp_min=nozzle_temp_min,
            nozzle_temp_max=nozzle_temp_max,
        )
        log.debug("set_ams_filament_setting: command sent to %s", name)
        return f"Filament setting updated for AMS unit {unit_id} slot {slot_id} on '{name}'."
    except Exception as e:
        log.error("set_ams_filament_setting: error for %s: %s", name, e, exc_info=True)
        return f"Error setting filament on '{name}': {e}"


def load_filament(
    name: str,
    unit_id: int,
    slot_id: int,
    user_permission: bool = False,
) -> str:
    """
    Load filament from a specific AMS unit and slot into the extruder.

    unit_id is the AMS index (0-based), slot_id is the slot (0-3).
    Use slot_id=254 to load from the external spool holder (same source as get_external_spool()).
    The external spool holder is a separate filament feeder that attaches to the printer's side,
    holding one spool outside the AMS unit. Use slot_id=254 to load from it.
    Requires user_permission=True.

    H2D dual-extruder wiring (fixed by hardware):
    - AMS 2 Pro (chip_id 0, unit_id 0) → RIGHT extruder (extruder 0).
    - AMS HT (chip_id 128, unit_id 1) → LEFT extruder (extruder 1).
    Loading from a given AMS unit automatically targets its paired extruder.
    """
    log.debug("load_filament: called for name=%s unit_id=%s slot_id=%s user_permission=%s", name, unit_id, slot_id, user_permission)
    if not user_permission:
        log.debug("load_filament: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("load_filament: printer not connected: %s", name)
        return _no_printer(name)
    ams_id = _resolve_ams_id(name, unit_id)
    if ams_id is None:
        return f"Error: AMS unit {unit_id} not found on '{name}'."
    try:
        log.debug("load_filament: calling printer.load_filament(slot_id=%s, ams_id=%s) for %s", slot_id, ams_id, name)
        printer.load_filament(slot_id=slot_id, ams_id=ams_id)
        log.debug("load_filament: command sent to %s", name)
        return f"Load filament command sent for AMS unit {unit_id} slot {slot_id} on '{name}'."
    except Exception as e:
        log.error("load_filament: error for %s: %s", name, e, exc_info=True)
        return f"Error loading filament on '{name}': {e}"


def unload_filament(
    name: str,
    user_permission: bool = False,
) -> str:
    """
    Unload the currently loaded filament from the extruder back into the AMS.

    Requires user_permission=True.
    """
    log.debug("unload_filament: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("unload_filament: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("unload_filament: printer not connected: %s", name)
        return _no_printer(name)
    try:
        log.debug("unload_filament: calling printer.unload_filament() for %s", name)
        printer.unload_filament()
        log.debug("unload_filament: command sent to %s", name)
        return f"Unload filament command sent to '{name}'."
    except Exception as e:
        log.error("unload_filament: error for %s: %s", name, e, exc_info=True)
        return f"Error unloading filament on '{name}': {e}"


def start_ams_dryer(
    name: str,
    unit_id: int,
    target_temp: int = 55,
    duration_hours: int = 4,
    rotate_tray: bool = False,
    user_permission: bool = False,
) -> str:
    """
    Start the AMS filament dryer on the specified unit.

    target_temp is in °C (default 55°C). duration_hours is the drying time
    (default 4 hours). Only supported on AMS 2 Pro and AMS HT models.
    If the AMS unit model does not support drying (e.g. AMS Lite), the command is
    silently ignored by the printer.
    Requires user_permission=True.
    """
    log.debug("start_ams_dryer: called for name=%s unit_id=%s target_temp=%s duration_hours=%s user_permission=%s", name, unit_id, target_temp, duration_hours, user_permission)
    import time

    if not user_permission:
        log.debug("start_ams_dryer: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("start_ams_dryer: printer not connected: %s", name)
        return _no_printer(name)
    ams_id = _resolve_ams_id(name, unit_id)
    if ams_id is None:
        return f"Error: AMS unit {unit_id} not found on '{name}'."
    state = session_manager.get_state(name)
    filament_type = ""
    if state and state.spools:
        for spool in state.spools:
            if getattr(spool, "ams_id", -1) == ams_id and getattr(spool, "type", ""):
                filament_type = spool.type
                break
    try:
        log.debug("start_ams_dryer: calling printer.turn_on_ams_dryer for %s", name)
        printer.turn_on_ams_dryer(
            target_temp=target_temp,
            duration=duration_hours,
            ams_id=ams_id,
            rotate_tray=rotate_tray,
            filament_type=filament_type,
        )
        log.debug("start_ams_dryer: command sent to %s", name)
        time.sleep(2)
        state = session_manager.get_state(name)
        if state and state.ams_units:
            unit = next((u for u in state.ams_units if u.ams_id == ams_id), None)
            if unit and unit.heater_state != 0:  # 0 = AMSHeatingState.OFF
                return (
                    f"AMS dryer started on unit {unit_id} (ams_id={ams_id}): "
                    f"{target_temp}°C for {duration_hours}h on '{name}'. "
                    f"heater_state={unit.heater_state.name}"
                )
        return (
            f"Error: AMS dryer command sent to unit {unit_id} (ams_id={ams_id}) on '{name}' "
            f"but heater_state did not change — printer rejected the command. "
            f"Check get_ams_units for current state."
        )
    except Exception as e:
        log.error("start_ams_dryer: error for %s: %s", name, e, exc_info=True)
        return f"Error starting AMS dryer on '{name}': {e}"


def stop_ams_dryer(
    name: str,
    unit_id: int,
    user_permission: bool = False,
) -> str:
    """
    Stop the AMS filament dryer on the specified unit.

    Requires user_permission=True.
    """
    log.debug("stop_ams_dryer: called for name=%s unit_id=%s user_permission=%s", name, unit_id, user_permission)
    if not user_permission:
        log.debug("stop_ams_dryer: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("stop_ams_dryer: printer not connected: %s", name)
        return _no_printer(name)
    ams_id = _resolve_ams_id(name, unit_id)
    if ams_id is None:
        return f"Error: AMS unit {unit_id} not found on '{name}'."
    try:
        log.debug("stop_ams_dryer: calling printer.turn_off_ams_dryer for %s", name)
        printer.turn_off_ams_dryer(ams_id=ams_id)
        log.debug("stop_ams_dryer: command sent to %s", name)
        return f"AMS dryer stopped on unit {unit_id} (ams_id={ams_id}) on '{name}'."
    except Exception as e:
        log.error("stop_ams_dryer: error for %s: %s", name, e, exc_info=True)
        return f"Error stopping AMS dryer on '{name}': {e}"


def set_ams_user_setting(
    name: str,
    setting: str,
    value: bool,
    user_permission: bool = False,
) -> str:
    """
    Enable or disable an AMS user setting on the named printer.

    Supported settings: 'calibrate_remain_flag' (spool-weight based remaining
    estimation), 'startup_read_option' (RFID scan on power-on), 'tray_read_option'
    (RFID scan on spool insert). Requires user_permission=True.
    'calibrate_remain_flag' = estimate remaining filament by spool weight (requires AMS with
    weight sensors). 'startup_read_option' = scan RFID tags on all loaded spools when the
    printer powers on (to detect filament changes while powered off). 'tray_read_option' = scan
    RFID tag when a spool is inserted into an AMS slot.
    """
    log.debug("set_ams_user_setting: called for name=%s setting=%s value=%s user_permission=%s", name, setting, value, user_permission)
    if not user_permission:
        log.debug("set_ams_user_setting: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("set_ams_user_setting: printer not connected: %s", name)
        return _no_printer(name)
    try:
        from bpm.bambutools import AMSUserSetting
        setting_map = {
            "calibrate_remain_flag": AMSUserSetting.CALIBRATE_REMAIN_FLAG,
            "startup_read_option": AMSUserSetting.STARTUP_READ_OPTION,
            "tray_read_option": AMSUserSetting.TRAY_READ_OPTION,
        }
        ams_setting = setting_map.get(setting.lower())
        if ams_setting is None:
            return f"Error: Unknown setting '{setting}'. Supported: {list(setting_map)}"
        log.debug("set_ams_user_setting: calling printer.set_ams_user_setting for %s", name)
        printer.set_ams_user_setting(ams_setting, value)
        log.debug("set_ams_user_setting: command sent to %s", name)
        return f"AMS setting '{setting}' set to {value} on '{name}'."
    except Exception as e:
        log.error("set_ams_user_setting: error for %s: %s", name, e, exc_info=True)
        return f"Error setting AMS user setting on '{name}': {e}"


def get_external_spool(name: str) -> dict:
    """
    Return the filament info for the external spool holder (tray id 254).

    Returns the spool dict if a filament is configured on the external tray,
    or a dict with 'loaded': False if not.
    """
    log.debug("get_external_spool: called for name=%s", name)
    state = session_manager.get_state(name)
    if state is None:
        log.warning("get_external_spool: printer %s not connected", name)
        return {"error": f"Printer '{name}' not connected"}
    for spool in (state.spools or []):
        if spool.id == 254 or spool.slot_id == 254:
            log.debug("get_external_spool: returning result for %s", name)
            return {"loaded": True, "spool": _serialize(spool)}
    log.debug("get_external_spool: returning result for %s", name)
    return {"loaded": False, "spool": None}


def calibrate_ams_remaining(
    name: str,
    unit_id: int,
    slot_id: int,
    user_permission: bool = False,
) -> str:
    """
    Trigger an RFID re-scan on the specified AMS slot to update remaining filament data.

    Bambu Lab filament spools include an RFID tag. The tag stores remaining filament weight.
    This call triggers a re-read of the tag for the specified slot and updates the spool's
    remaining percentage shown in get_ams_units() and get_spool_info().
    The printer will push updated spool telemetry after scanning. Only RFID-equipped
    Bambu Lab spools carry tag data. Requires user_permission=True.
    """
    log.debug("calibrate_ams_remaining: called for name=%s unit_id=%s slot_id=%s user_permission=%s", name, unit_id, slot_id, user_permission)
    if not user_permission:
        log.debug("calibrate_ams_remaining: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("calibrate_ams_remaining: printer not connected: %s", name)
        return _no_printer(name)
    ams_id = _resolve_ams_id(name, unit_id)
    if ams_id is None:
        return f"Error: AMS unit {unit_id} not found on '{name}'."
    try:
        log.debug("calibrate_ams_remaining: calling printer.refresh_spool_rfid for %s", name)
        printer.refresh_spool_rfid(slot_id=slot_id, ams_id=ams_id)
        log.debug("calibrate_ams_remaining: command sent to %s", name)
        return f"RFID re-scan triggered for AMS unit {unit_id} slot {slot_id} on '{name}'."
    except Exception as e:
        log.error("calibrate_ams_remaining: error for %s: %s", name, e, exc_info=True)
        return f"Error triggering RFID scan on '{name}': {e}"
