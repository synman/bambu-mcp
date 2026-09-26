"""
tools/filament.py — AMS and filament management tools for Bambu Lab printers.

Read tools are always accessible. Write tools require user_permission=True.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
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


def _permission_denied(consequence: str) -> str:
    return f"Error: user_permission must be True to perform this action. {consequence}"


# Drying temperature range per dryer-capable AMS model, in °C (Bambu Studio AMSDryControl.cpp
# ams_limits: N3F = AMS 2 Pro, N3S = AMS HT). Neither bpm nor the firmware enforces these, and
# bpm publishes before it validates ams_id, so a start outside them is refused before sending.
_DRYER_TEMP_LIMITS = {3: (45, 65), 4: (45, 85)}  # bpm AMSModel.AMS_2_PRO, AMSModel.AMS_HT
# Longest dry, in hours, H2D firmware was measured to accept (2026-09-26). Studio's 24 h cap is
# enforced only in its own UI.
_DRYER_MAX_HOURS = 999
_HEATER_OFF = 0
_HEATER_DRYING = 2
_HEATER_ERROR = 5


def _dryer_request_error(unit, target_temp: int, duration_hours: int) -> str:
    """Why this dryer start must not be sent, or "" when it may be.

    Shared by the ``start_ams_dryer`` tool and the ``/api/turn_on_ams_dryer`` route."""
    model = int(getattr(unit, "model", 0))
    limits = _DRYER_TEMP_LIMITS.get(model)
    if limits is None:
        model_name = getattr(getattr(unit, "model", None), "name", str(model))
        return f"AMS unit ams_id={unit.ams_id} ({model_name}) has no dryer."
    low, high = limits
    if not low <= target_temp <= high:
        return f"target_temp {target_temp} is outside {low}-{high}°C for this AMS model."
    if not 1 <= duration_hours <= _DRYER_MAX_HOURS:
        return f"duration_hours {duration_hours} is outside 1-{_DRYER_MAX_HOURS}."
    return ""


def _await_dryer_start(get_unit, heater_before, timeout: float = 10.0):
    """Poll once per second, up to ``timeout``, for a dryer start to be confirmed.

    Returns ``(outcome, unit)`` where outcome is ``"drying"`` (confirmed), ``"unconfirmed"``
    (the unit was already DRYING and never reported anything else, so the reads cannot show
    whether the command was accepted) or ``"failed"``. ``get_unit`` returns the unit or None.

    heater_state is only rewritten when a telemetry frame carrying the AMS info word arrives,
    so until then it still holds whatever it held before the command. Reads equal to
    ``heater_before`` (snapshotted before publishing) are therefore ignored until one differs;
    from then on every read counts. DRYING is success; ERROR, or OFF after another
    post-command state, is taken as a rejection (assumed, never observed on hardware); a first
    post-command OFF (a unit that was COOLING, say) is waited out.

    Shared by the ``start_ams_dryer`` tool and the ``/api/turn_on_ams_dryer`` route.
    """
    deadline = time.time() + timeout
    unit = None
    frame_seen = False
    left_off = False
    while time.time() < deadline:
        time.sleep(1)
        current = get_unit()
        if current is None:
            continue
        unit = current
        hs = int(unit.heater_state)
        if not frame_seen:
            if hs == heater_before:
                continue
            frame_seen = True
        if hs == _HEATER_DRYING:
            return "drying", unit
        if hs == _HEATER_ERROR:
            break
        if hs != _HEATER_OFF:
            left_off = True
        elif left_off:
            break
    if unit is not None and not frame_seen and heater_before == _HEATER_DRYING:
        return "unconfirmed", unit
    return "failed", unit


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

    WHEN to use: read AMS temperature, humidity, heater and drying state and which slots hold
    a spool, or find the positional ``unit_id`` to pass to the AMS write tools in this module.

    Sibling disambiguation: ``get_ams_units`` and ``get_ams_status`` return the same
    ``ams_status`` / ``ams_count`` / ``units`` payload from the same printer state; this
    tool's description is the field reference for the units list, and the order of that list
    is what ``unit_id`` indexes. ``get_spool_info`` is filament-centric (spool type, color,
    remaining percentage). ``get_external_spool`` reports the external spool holder.

    Args:
        name: Configured printer name (see ``get_configured_printers``).

    Returns:
        ``{"ams_count": int, "ams_status": str, "units": [dict]}``. ``ams_count`` is the
        number of connected AMS units, ``ams_status`` is the global AMS status text, and
        ``units`` is an empty list when the printer reports none. Each unit dict includes
        temperature, humidity, heater state, drying status, and per-slot filament presence, as
        the fields ams_id, chip_id, model, temp_actual, temp_target, humidity_index,
        humidity_raw, ams_info, heater_state, dry_fan1_status, dry_fan2_status,
        dry_sub_status, dry_time (minutes left), tray_exists (list of four booleans; see
        Notes) and assigned_to_extruder; enum fields appear as their names. Error shape:
        ``{"error": "Printer '<name>' not connected"}``.

    Notes:
        Field semantics:
        - ams_id is the AMS unit id as reported by the printer (for example 0 for the first
          AMS 2 Pro, 128 for AMS HT). chip_id is a separate field holding the unit's hardware
          serial string; the two are not synonyms. The 0-based positional unit_id used by
          load_filament() and other write tools refers to the position of the unit in the
          ams_units list, not to either value.
        - tray_exists is always emitted as four booleans. A standard AMS unit has four slots
          (0–3) and all four are meaningful. bpm's data dictionary describes an AMS HT as a
          single-slot unit (one bit checked), so treat only tray_exists[0] as meaningful
          there; the parser still fills indices 1–3 and bpm's documentation does not define
          them.
        - The AMS model is identified by the `model` field (AMSModel enum name, e.g.
          'AMS_2_PRO', 'AMS_HT'). See the enums knowledge module for all values.
        - On H2D: AMS 2 Pro (ams_id=0, first in list) feeds the RIGHT extruder (extruder 0);
          AMS HT (ams_id=128, second in list) feeds the LEFT extruder (extruder 1).
        - humidity_index scale: 1=WET (alert, filament needs drying), 5=DRY (good, no action
          needed). IMPORTANT: higher numbers mean DRIER — the scale is counterintuitive.
          Only humidity_index values of 1 or 2 indicate a moisture problem. A value of 5
          means the filament is completely dry. 0 means the sensor reading is unavailable
          (uninitialized or not supported by this AMS model — do not treat as wet).
        - heater_state: AMSHeatingState enum name — OFF, CHECKING (transient), DRYING (active),
          COOLING, STOPPING, ERROR, CANNOT_STOP_HEAT_OOC, PRODUCT_TEST. CHECKING is a brief
          transition state after issuing a start_ams_dryer() command; DRYING with
          dry_sub_status=HEATING confirms active heating.
        - dry_sub_status: AMSDrySubStatus enum name — OFF, HEATING, DEHUMIDIFY. Indicates the
          current phase within an active drying cycle.
        - dry_fan1_status: AMSDryFanStatus enum name — OFF or ON. Primary drying fan (bits
          18–19 of ams_info). Only meaningful while heater_state=DRYING.
        - dry_fan2_status: AMSDryFanStatus enum name — OFF or ON. Secondary drying fan (bits
          20–21 of ams_info). Only meaningful while heater_state=DRYING.
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

    WHEN to use: record or correct the filament identity, color, and nozzle temperature range
    of one AMS slot, or clear the slot by passing filament_id 'no_filament'.

    WRITE GUARD: sends the slot's filament setting (material code, name, type, color, and
    nozzle temperature range) to the printer in a single command, overwriting what the slot
    currently holds; filament_id, filament_name and filament_type left at their defaults are
    sent as empty strings, an empty color is not sent (opaque white is written instead), and
    temperatures left at -1 are sent as -1. With ``user_permission`` False the tool changes
    nothing and returns the refusal string naming the overwrite.

    Sibling disambiguation: ``set_ams_filament_setting`` writes the slot's stored filament
    fields and moves no filament. ``calibrate_ams_remaining`` asks the printer to re-read the
    slot's RFID tag instead of writing fields, ``load_filament`` and ``unload_filament`` move
    filament, and ``get_spool_info`` reads the resulting spool data.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        unit_id: AMS unit index (0-based position in the ``get_ams_units`` ``units`` list). A
            value outside that range is matched against the raw hardware ams_id (for example
            128 for AMS HT).
        slot_id: Slot within that unit (0-3).
        filament_id: Bambu Lab catalog material code (tray_info_idx), e.g. 'GFA00' for Bambu
            PLA Basic. This is a primary identity field — a lookup key from Bambu's filament
            database that encodes temperature profiles, drying parameters, and flow
            characteristics. Pass 'no_filament' to clear the slot and mark it as empty.
            Default "" (sent as empty).
        filament_name: Bambu Lab vendor-specific brand label (e.g. 'Bambu PLA Basic'). It is
            optional, absent on third-party spools, and NOT a reliable spool identifier. The
            true identity of a spool is color + filament_id (base profile), not this name
            field. Default "" (sent as empty).
        filament_type: Short filament type string (e.g. 'PLA', 'PETG', 'ABS'). Default ""
            (sent as empty).
        color: CSS color name or RRGGBB hex string. Default "" is NOT sent as empty: the
            library skips an empty color, so the command keeps its template default FFFFFFFF
            and the slot color is set to opaque white. Always pass the color you want.
        nozzle_temp_min: Minimum nozzle temperature in °C. The default -1 is sent as-is;
            whether the printer treats -1 as "leave unchanged" is not established by this code.
        nozzle_temp_max: Maximum nozzle temperature in °C. The default -1 is sent as-is;
            whether the printer treats -1 as "leave unchanged" is not established by this code.
        user_permission: Must be True to execute. Default False.

    Returns:
        A ``str``. Success: ``"Filament setting updated for AMS unit <unit_id> slot
        <slot_id> on '<name>'."``. Errors are ``"Error: ..."`` strings, never a dict: the
        ``_permission_denied`` refusal when ``user_permission`` is False,
        ``"Error: Printer '<name>' not connected."``, ``"Error: AMS unit <unit_id> not found
        on '<name>'."``, or ``"Error setting filament on '<name>': <exception>"`` when the
        command fails.

    Notes:
        WARNING: This call sends ALL fields to the printer in a single command. Text fields
        left at their default go out as empty strings and temperatures left at -1 go out as
        -1; whether the printer treats an empty string as "clear this field" is not
        established by this code, and the refusal text's "empty fields clear existing values"
        is likewise unverified. Always pass ALL relevant fields in a single call (filament_id,
        filament_type, color, nozzle_temp_min, nozzle_temp_max) to avoid overwriting existing
        slot metadata.

        To clear a slot, pass filament_id='no_filament': the library then ignores
        filament_name, filament_type, color and both temperatures and writes name "", type "",
        color FFFFFF00 and temperatures 0/0.

        The tool computes the absolute tray id as ams_id + slot_id when the resolved ams_id is
        128 or higher (AMS HT), otherwise ams_id * 4 + slot_id. The printer library then
        re-derives the command's ams_id as tray_id // 4 and slot_id as tray_id % 4 (only
        254/255 are special-cased), so on AMS HT tray_id 128 goes out as ams_id 32, slot_id 0,
        tray_id 128. Whether that reaches an AMS HT slot is unverified against printer
        firmware; do not rely on it.
    """
    log.debug("set_ams_filament_setting: called for name=%s unit_id=%s slot_id=%s", name, unit_id, slot_id)
    if not user_permission:
        log.debug("set_ams_filament_setting: permission denied for %s", name)
        return _permission_denied(
            "This would overwrite the filament settings (material code, name, type, color, "
            "nozzle temperature range) stored on the selected AMS slot, and empty fields clear "
            "existing values."
        )
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("set_ams_filament_setting: printer not connected: %s", name)
        return _no_printer(name)
    ams_id = _resolve_ams_id(name, unit_id)
    if ams_id is None:
        return f"Error: AMS unit {unit_id} not found on '{name}'."
    tray_id = ams_id + slot_id if ams_id >= 128 else ams_id * 4 + slot_id
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

    WHEN to use: feed the filament in one AMS slot, or on the external spool holder, into the
    extruder while no print is active.

    WRITE GUARD: sends a load-filament command to the printer, which starts feeding filament
    from the chosen slot into the extruder. With ``user_permission`` False the tool changes
    nothing and returns the refusal string naming that consequence. A second gate blocks the
    call while the printer is printing (see Returns).

    Sibling disambiguation: ``load_filament`` feeds filament into the extruder;
    ``unload_filament`` retracts the loaded filament back into the AMS. ``set_ams_filament_setting``
    only changes the filament metadata stored for a slot and moves no filament.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        unit_id: AMS index (0-based position in the ``get_ams_units`` ``units`` list). A value
            outside that range is matched against the raw hardware ams_id.
        slot_id: Slot within the unit (0-3). Use 254 to load from the external spool holder.
            On a dual-nozzle printer 254 is the LEFT holder and 255 is the RIGHT holder;
            loading from 255 through this tool has not been verified. The external spool
            holder is a separate filament feeder that attaches to the printer's side,
            holding one spool outside the AMS unit. get_external_spool() reports what is
            loaded on it.
        user_permission: Must be True to execute. Default False.

    Returns:
        A ``str``. Success: ``"Load filament command sent for AMS unit <unit_id> slot
        <slot_id> on '<name>'."``. Errors are strings, never a dict: the
        ``_permission_denied`` refusal when ``user_permission`` is False,
        ``"Error: Printer '<name>' not connected."``, the active-print block message
        (``"Blocked: '<name>' is currently <gcode_state>. load_filament is not safe while
        a print is active. ..."``), ``"Error: AMS unit <unit_id> not found on '<name>'."``, or
        ``"Error loading filament on '<name>': <exception>"`` when the command fails.

    Notes:
        H2D dual-extruder pairing (AMS 2 Pro to the RIGHT extruder, AMS HT to the LEFT) is
        printer behavior that get_ams_units reports per unit as assigned_to_extruder; this
        code does not establish it. The tool sends only the resolved ams_id and slot_id: the
        printer library copies slot_id into the command's target field, which bpm's protocol
        reference describes as the extruder to load into, and it marks multi-unit loading
        unfinished (TODO: refactor to support multiple AMSs). Loading from a unit other than
        the first is therefore unverified.

        To find the correct unit_id: call get_ams_units() and use the positional
        index (0-based) of the desired unit in the returned list. ams_id values are assigned
        by the printer and should not be hardcoded. unit_id must resolve to a
        connected AMS unit even when slot_id is 254; otherwise the tool returns the
        "AMS unit not found" error.

        ⛔ BLOCKED during active prints (gcode_state RUNNING or PREPARE), because a filament
        change during a print risks toolhead crashes or failed prints.
    """
    log.debug("load_filament: called for name=%s unit_id=%s slot_id=%s user_permission=%s", name, unit_id, slot_id, user_permission)
    if not user_permission:
        log.debug("load_filament: permission denied for %s", name)
        return _permission_denied(
            "This would start loading filament from the selected AMS slot (or external spool "
            "holder) into the extruder."
        )
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("load_filament: printer not connected: %s", name)
        return _no_printer(name)
    from tools._guards import check_active_print_guard
    blocked = check_active_print_guard(printer, name, "load_filament")
    if blocked:
        return blocked.get("error", "Blocked: active print in progress.")
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

    WHEN to use: retract the filament that is currently loaded in the extruder back into the
    AMS while no print is active.

    WRITE GUARD: sends an unload-filament command to the printer, which starts retracting
    the loaded filament out of the extruder. With ``user_permission`` False the tool changes
    nothing and returns the refusal string naming that consequence. A second gate blocks the
    call while the printer is printing (see Returns).

    Sibling disambiguation: ``unload_filament`` retracts the loaded filament back into the
    AMS and takes no unit or slot; ``load_filament`` feeds a chosen slot into the extruder.
    ``get_spool_info`` shows which spool is currently loaded.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        user_permission: Must be True to execute. Default False.

    Returns:
        A ``str``. Success: ``"Unload filament command sent to '<name>'."``. Errors are
        strings, never a dict: the ``_permission_denied`` refusal when ``user_permission`` is
        False, ``"Error: Printer '<name>' not connected."``, the active-print block message
        (``"Blocked: '<name>' is currently <gcode_state>. unload_filament is not safe
        while a print is active. ..."``), or ``"Error unloading filament on '<name>':
        <exception>"`` when the command fails.

    Notes:
        ⛔ BLOCKED during active prints (gcode_state RUNNING or PREPARE), because a filament
        change during a print risks toolhead crashes or failed prints.

        The tool has no unit selector: it calls the printer library's unload with its
        default ams_id (0), so the command always names AMS unit 0. It cannot name another
        unit, such as AMS HT (ams_id 128, the left extruder), even though load_filament
        accepts an AMS HT unit_id; whether the printer then unloads whichever filament is
        loaded is not established by this code.
    """
    log.debug("unload_filament: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("unload_filament: permission denied for %s", name)
        return _permission_denied(
            "This would start unloading the filament currently loaded in the extruder back into "
            "the AMS."
        )
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("unload_filament: printer not connected: %s", name)
        return _no_printer(name)
    from tools._guards import check_active_print_guard
    blocked = check_active_print_guard(printer, name, "unload_filament")
    if blocked:
        return blocked.get("error", "Blocked: active print in progress.")
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

    WHEN to use: dry the filament in one AMS unit at a chosen temperature and duration, for
    example when ``get_ams_units`` shows a low ``humidity_index``.

    WRITE GUARD: sends a start-drying command that turns on the unit's dryer heater at the
    given temperature for the given duration. With ``user_permission`` False the tool changes
    nothing and returns the refusal string naming that consequence.

    Sibling disambiguation: ``start_ams_dryer`` turns the dryer on and waits up to 10
    seconds for the unit to report DRYING; ``stop_ams_dryer`` turns it off.
    ``get_ams_units`` reads the resulting heater_state and dry_sub_status.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        unit_id: AMS unit index (0-based position in the ``get_ams_units`` ``units`` list). A
            value outside that range is matched against the raw hardware ams_id.
        target_temp: Drying temperature in °C. Default 55. Must be 45-65 on an AMS 2 Pro and
            45-85 on an AMS HT (Bambu Studio's limits); anything else is refused, not clamped.
        duration_hours: Drying time in hours, 1-999, passed unchanged as the command's
            ``duration`` field. Default 4. Hours is settled by Bambu Studio's source and was
            measured on H2D firmware (72 h and 999 h accepted, 2026-09-26); there is no 24 h
            cap. The printer reports the time left (``dry_time``) in minutes.
        rotate_tray: Passed to the printer as the rotate-tray flag for the drying command.
            Default False.
        user_permission: Must be True to execute. Default False.

    Returns:
        A ``str``. Success: ``"AMS dryer started on unit <unit_id> (ams_id=<ams_id>):
        <target_temp>°C for <duration_hours>h on '<name>'. heater_state=DRYING"``. Errors are
        ``"Error: ..."`` strings, never a dict: the ``_permission_denied`` refusal when
        ``user_permission`` is False, ``"Error: Printer '<name>' not connected."``,
        ``"Error: AMS unit <unit_id> not found on '<name>'."``, ``"Error: <reason> Nothing was sent
        to '<name>'."`` when the unit has no dryer or the temperature or duration is out of
        range (checked before anything is published), ``"Error: AMS dryer command
        sent to unit <unit_id> (ams_id=<ams_id>) on '<name>' but heater_state did not reach
        DRYING within 10s (final state: <STATE or unknown>). Check get_ams_units for current
        state."`` (the command WAS sent in that case; it is returned after the full 10s, or
        sooner when heater_state reads ERROR or falls back to OFF after showing another
        state since the command), or ``"Error starting AMS dryer on '<name>': <exception>"``
        when the command fails. One more outcome that is not an error: ``"AMS dryer command
        sent to unit <unit_id> (ams_id=<ams_id>) on '<name>', but the unit was already DRYING
        before the command and heater_state never changed, so it cannot show whether the
        command was accepted ..."``, returned after the full 10s. Drying is in progress; whether
        it follows the new temperature and duration has to be read from ``get_ams_units``.

    Notes:
        Only an AMS 2 Pro or AMS HT is sent the command. Any other unit (AMS Lite, the original
        AMS, an unknown model) is refused before publishing, because bpm publishes before it
        validates and the firmware does not clamp the temperature. The HTTP route
        ``/api/turn_on_ams_dryer`` applies the same checks.

        filament_type is derived from the first spool in the target AMS unit that reports a
        type (spool.type, e.g. "ABS", "PLA") and falls back to "" if there is none. bpm
        documents it only as passed to firmware for validation; what the firmware does with
        an empty value is not established by this code.

        Heater state transition: after the command is sent, heater_state may briefly read
        CHECKING (a transitional state). Active drying is confirmed by heater_state=DRYING with
        dry_sub_status=HEATING. This tool polls once per second (first reading one second
        after the publish), up to 10 seconds, waiting for DRYING before returning. heater_state
        is only rewritten when a telemetry frame carrying the AMS info word arrives, so
        until then it still holds whatever it held before the command (OFF, COOLING, DRYING,
        ERROR, anything). The tool records that value just before publishing and ignores every
        read equal to it; the first read that differs shows a post-command frame has landed,
        and every read after that counts. DRYING then means success. The tool stops polling
        early, and returns the "did not reach DRYING" error, when a post-command read is ERROR,
        or is OFF after another post-command state (CHECKING, COOLING and so on). That is what
        the code assumes a rejected command looks like; no such sequence has been observed on
        hardware, so treat it as unmeasured. A first post-command read of OFF (a unit that was
        COOLING, say) is waited out, not treated as a rejection. If the unit was already DRYING
        and never reports anything else, the reads cannot show whether the command was
        accepted, and the tool says so instead of claiming a start. A command the printer
        accepts but whose first frame arrives later than 10 seconds still ends in the timeout
        error with the command in effect; confirm with get_ams_units.

        Sticky preferences: before presenting parameters to the user, look up stored values:
          from user_prefs import get_pref
          target_temp    = get_pref(f"{name}:ams{unit_id}:target_temp",    55)
          duration_hours = get_pref(f"{name}:ams{unit_id}:duration_hours", 4)
          rotate_tray    = get_pref(f"{name}:ams{unit_id}:rotate_tray",    False)
        Factory defaults: target_temp=55, duration_hours=4, rotate_tray=False.
        Label each "(your preference)" if stored value differs from factory default, "(default)" otherwise.
        After a successful call, store the confirmed values:
          from user_prefs import set_pref
          set_pref(f"{name}:ams{unit_id}:target_temp",    target_temp)
          set_pref(f"{name}:ams{unit_id}:duration_hours", duration_hours)
          set_pref(f"{name}:ams{unit_id}:rotate_tray",    rotate_tray)
    """
    log.debug("start_ams_dryer: called for name=%s unit_id=%s target_temp=%s duration_hours=%s user_permission=%s", name, unit_id, target_temp, duration_hours, user_permission)
    if not user_permission:
        log.debug("start_ams_dryer: permission denied for %s", name)
        return _permission_denied(
            "This would start the AMS dryer heater on the selected unit at the requested "
            "temperature and duration."
        )
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("start_ams_dryer: printer not connected: %s", name)
        return _no_printer(name)
    ams_id = _resolve_ams_id(name, unit_id)
    if ams_id is None:
        return f"Error: AMS unit {unit_id} not found on '{name}'."
    state = session_manager.get_state(name)
    unit_before = next((u for u in (state.ams_units or []) if u.ams_id == ams_id), None) if state else None
    if unit_before is None:
        return f"Error: AMS unit {unit_id} not found on '{name}'."
    refusal = _dryer_request_error(unit_before, target_temp, duration_hours)
    if refusal:
        log.debug("start_ams_dryer: refused for %s: %s", name, refusal)
        return f"Error: {refusal} Nothing was sent to '{name}'."
    filament_type = ""
    if state.spools:
        for spool in state.spools:
            if getattr(spool, "ams_id", -1) == ams_id and getattr(spool, "type", ""):
                filament_type = spool.type
                break
    # The value heater_state holds at this instant, before the command is published. An int
    # copy: bpm mutates the unit object in place.
    heater_before = int(unit_before.heater_state)

    def _unit():
        current = session_manager.get_state(name)
        if not (current and current.ams_units):
            return None
        return next((u for u in current.ams_units if u.ams_id == ams_id), None)

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
        outcome, unit = _await_dryer_start(_unit, heater_before)
        if outcome == "drying":
            return (
                f"AMS dryer started on unit {unit_id} (ams_id={ams_id}): "
                f"{target_temp}°C for {duration_hours}h on '{name}'. "
                f"heater_state={unit.heater_state.name}"
            )
        if outcome == "unconfirmed":
            return (
                f"AMS dryer command sent to unit {unit_id} (ams_id={ams_id}) on '{name}', but the "
                f"unit was already DRYING before the command and heater_state never changed, so "
                f"it cannot show whether the command was accepted (drying is in progress either "
                f"way). Check get_ams_units for the current temperature and remaining time."
            )
        return (
            f"Error: AMS dryer command sent to unit {unit_id} (ams_id={ams_id}) on '{name}' "
            f"but heater_state did not reach DRYING within 10s "
            f"(final state: {unit.heater_state.name if unit else 'unknown'}). "
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

    WHEN to use: end a drying cycle early on one AMS unit, or make sure its dryer is off.

    WRITE GUARD: sends a turn-off-drying command that switches the unit's dryer off and ends
    its drying cycle. With ``user_permission`` False the tool changes nothing and returns the
    refusal string naming that consequence.

    Sibling disambiguation: ``stop_ams_dryer`` turns the dryer off; ``start_ams_dryer`` turns
    it on. ``get_ams_units`` reads the resulting heater_state.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        unit_id: AMS unit index (0-based position in the ``get_ams_units`` ``units`` list). A
            value outside that range is matched against the raw hardware ams_id.
        user_permission: Must be True to execute. Default False.

    Returns:
        A ``str``. Success: ``"AMS dryer stopped on unit <unit_id> (ams_id=<ams_id>) on
        '<name>'."``. Errors are ``"Error: ..."`` strings, never a dict: the
        ``_permission_denied`` refusal when ``user_permission`` is False,
        ``"Error: Printer '<name>' not connected."``, ``"Error: AMS unit <unit_id> not found
        on '<name>'."``, or ``"Error stopping AMS dryer on '<name>': <exception>"`` when the
        command fails.
    """
    log.debug("stop_ams_dryer: called for name=%s unit_id=%s user_permission=%s", name, unit_id, user_permission)
    if not user_permission:
        log.debug("stop_ams_dryer: permission denied for %s", name)
        return _permission_denied(
            "This would turn off the AMS dryer heater on the selected unit and end its "
            "drying cycle."
        )
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

    WHEN to use: turn on or off spool-weight remaining estimation, the RFID scan at printer
    power-on, or the RFID scan when a spool is inserted.

    WRITE GUARD: sends the AMS user-setting command to the printer, which changes one of the
    three AMS user settings on the printer. With ``user_permission`` False the tool changes
    nothing and returns the refusal string naming that consequence.

    Sibling disambiguation: ``set_ams_user_setting`` changes a standing AMS behavior setting.
    ``calibrate_ams_remaining`` triggers a one-time RFID re-scan of a single slot, and
    ``set_ams_filament_setting`` writes one slot's filament fields.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        setting: One of 'calibrate_remain_flag', 'startup_read_option', 'tray_read_option'
            (case-insensitive).
        value: True to enable the setting, False to disable it.
        user_permission: Must be True to execute. Default False.

    Returns:
        A ``str``. Success: ``"AMS setting '<setting>' set to <value> on '<name>'."``. Errors
        are ``"Error: ..."`` strings, never a dict: the ``_permission_denied`` refusal when
        ``user_permission`` is False, ``"Error: Printer '<name>' not connected."``,
        ``"Error: Unknown setting '<setting>'. Supported: [...]"``, or ``"Error setting AMS
        user setting on '<name>': <exception>"`` when the command fails.

    Notes:
        Supported settings: 'calibrate_remain_flag' (spool-weight based remaining
        estimation), 'startup_read_option' (RFID scan on power-on), 'tray_read_option'
        (RFID scan on spool insert).
        'calibrate_remain_flag' = estimate remaining filament by tracking spool weight.
          Requires an AMS unit with built-in weight sensors (AMS 2 Pro only). AMS Lite
          and AMS HT do not have weight sensors — enabling this on those units has no effect.
        'startup_read_option' = scan RFID tags on all loaded spools when the printer powers on,
          to detect filament changes made while the printer was off.
        'tray_read_option' = scan the RFID tag when a spool is inserted into an AMS slot,
          auto-populating filament type, color, and temperature profile from the tag.

        The tool passes no unit selector: the printer library sends the command with its
        default ams_id (0). The command includes all three settings: the named one is set to
        value and the other two come from the library's last-seen telemetry values, not a
        fresh read from the printer. Those default to False until a status report arrives, so
        calling this early in a session can write False for the other two.
    """
    log.debug("set_ams_user_setting: called for name=%s setting=%s value=%s user_permission=%s", name, setting, value, user_permission)
    if not user_permission:
        log.debug("set_ams_user_setting: permission denied for %s", name)
        return _permission_denied(
            "This would change an AMS user setting (spool-weight remaining estimation, or RFID "
            "scan at startup or on spool insert) on the printer."
        )
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
    Return the filament info for the external spool holder.

    WHEN to use: check what filament sits on the external spool holder (or holders on a
    dual-nozzle printer), for example before ``load_filament`` with slot_id 254.

    Sibling disambiguation: ``get_external_spool`` returns the external holder trays alone.
    ``get_spool_info`` returns every spool on the printer plus the active one, and
    ``get_ams_units`` returns the AMS units and their slot states.

    Args:
        name: Configured printer name (see ``get_configured_printers``).

    Returns:
        ``{"loaded": True, "spool": <dict>, "spools": [<dict>, ...]}`` when any external tray
        entry exists, or ``{"loaded": False, "spool": None, "spools": []}`` when none does.
        ``loaded`` True means an entry exists, not that filament is on a holder: when
        telemetry carries ``vir_slot``, the printer library adds a placeholder entry (empty
        ``type``, ``slot_id`` -1) for each of 254 and 255 the printer reported no tray for.
        Check each entry's ``type``; an empty string means no filament type is reported for
        that holder. Identify the holder by ``id`` (254 or 255), not by ``slot_id``.
        "spool" is the entry that holds a filament type, preferring 254 over 255, and falls
        back to the first entry when none holds one. "spools" lists every external entry so a
        dual-nozzle caller can see both holders. Error shape:
        ``{"error": "Printer '<name>' not connected"}``.

    Notes:
        Telemetry names the holders 254 and 255. A single-nozzle printer has one physical
        holder, 254, but ``spools`` can still list both whenever ``vir_slot`` is reported.
        A dual-nozzle printer has a LEFT holder, 254, and a RIGHT holder, 255.
    """
    log.debug("get_external_spool: called for name=%s", name)
    state = session_manager.get_state(name)
    if state is None:
        log.warning("get_external_spool: printer %s not connected", name)
        return {"error": f"Printer '{name}' not connected"}
    external = [
        s for s in (state.spools or [])
        if s.id in (254, 255) or s.slot_id in (254, 255)
    ]
    external.sort(key=lambda s: (0 if getattr(s, "type", "") else 1, 0 if 254 in (s.id, s.slot_id) else 1))
    log.debug("get_external_spool: returning result for %s", name)
    if external:
        return {"loaded": True, "spool": _serialize(external[0]), "spools": [_serialize(s) for s in external]}
    return {"loaded": False, "spool": None, "spools": []}


def calibrate_ams_remaining(
    name: str,
    unit_id: int,
    slot_id: int,
    user_permission: bool = False,
) -> str:
    """
    Ask the printer to re-scan the RFID tag on the specified AMS slot.

    WHEN to use: have the printer re-read one slot's RFID tag so that slot's spool data, such
    as its remaining percentage, may be refreshed.

    WRITE GUARD: sends an RFID re-read request for the slot to the printer, which is asked to
    rescan the tag. With ``user_permission`` False the tool changes nothing and returns the
    refusal string naming that consequence.

    Sibling disambiguation: ``calibrate_ams_remaining`` triggers a one-time RFID re-scan of a
    single slot. ``set_ams_user_setting`` changes the standing RFID-scan and remaining-
    estimation settings, ``set_ams_filament_setting`` writes the slot's filament fields by
    hand, and ``get_spool_info`` reads the updated spool data.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        unit_id: AMS unit index (0-based position in the ``get_ams_units`` ``units`` list). A
            value outside that range is matched against the raw hardware ams_id.
        slot_id: Slot within the unit to scan (0-3).
        user_permission: Must be True to execute. Default False.

    Returns:
        A ``str``. Success: ``"RFID re-scan triggered for AMS unit <unit_id> slot <slot_id>
        on '<name>'."``. Errors are ``"Error: ..."`` strings, never a dict: the
        ``_permission_denied`` refusal when ``user_permission`` is False,
        ``"Error: Printer '<name>' not connected."``, ``"Error: AMS unit <unit_id> not found
        on '<name>'."``, or ``"Error triggering RFID scan on '<name>': <exception>"`` when
        the command fails.

    Notes:
        This asks the printer to re-read the RFID tag in the specified slot. Updated spool
        telemetry, which may include remaining_percent (see get_spool_info()), arrives later
        in a normal state push. The success string only means the request was published; it
        does not show that any value changed. Only RFID-equipped Bambu Lab spools carry tag
        data, and what the tag itself stores (remaining weight or otherwise) is not
        established by this code.
    """
    log.debug("calibrate_ams_remaining: called for name=%s unit_id=%s slot_id=%s user_permission=%s", name, unit_id, slot_id, user_permission)
    if not user_permission:
        log.debug("calibrate_ams_remaining: permission denied for %s", name)
        return _permission_denied(
            "This would make the printer re-read the RFID tag in the selected AMS slot and "
            "update that spool's data from it."
        )
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


def send_ams_control_command(
    name: str,
    cmd: str,
    user_permission: bool = False,
) -> str:
    """
    Send an AMS control command to pause, resume, or reset the AMS.

    WHEN to use: recover from an AMS-triggered pause (filament runout, AMS fault) with
    'RESUME', or pause the AMS feed or reset the AMS with 'PAUSE' or 'RESET'.

    WRITE GUARD: sends the chosen AMS control command to the printer, which pauses the AMS
    feed, resets the AMS, or, for 'RESUME', unblocks the AMS feed and resumes the halted
    print job. With ``user_permission`` False the tool changes nothing and returns the
    refusal string naming that consequence.

    Sibling disambiguation: ``send_ams_control_command`` acts on the AMS and, for 'RESUME',
    also resumes the print. ``pause_print`` and ``resume_print`` act on the print job itself;
    do not call ``resume_print`` after 'RESUME', as that would be a duplicate command.

    Args:
        name: Configured printer name (see ``get_configured_printers``).
        cmd: One of 'PAUSE', 'RESUME', 'RESET' (case-insensitive).
        user_permission: Must be True to execute. Default False.

    Returns:
        A ``str``. Success: ``"AMS control command <CMD> sent to '<name>'."`` with CMD in
        upper case. Errors are ``"Error: ..."`` strings, never a dict: the
        ``_permission_denied`` refusal when ``user_permission`` is False,
        ``"Error: Printer '<name>' not connected."``, ``"Error: Unknown AMS control command
        '<cmd>'. Must be one of: PAUSE, RESUME, RESET."``, or ``"Error sending AMS control
        command to '<name>': <exception>"`` when the command fails.

    Notes:
        - 'PAUSE'  — pause the AMS feed mid-print.
        - 'RESUME' — unblocks the AMS feed AND resumes the halted print job in a
          single operation. Use this for AMS-triggered pauses (filament runout,
          AMS fault). Do not also call resume_print() after this — that would be
          a duplicate command.
        - 'RESET'  — reset the AMS to its idle/ready state.
    """
    log.debug("send_ams_control_command: called for name=%s cmd=%s user_permission=%s", name, cmd, user_permission)
    if not user_permission:
        log.debug("send_ams_control_command: permission denied for %s", name)
        return _permission_denied(
            "This would send a PAUSE, RESUME or RESET command to the AMS; RESUME also resumes "
            "the halted print job."
        )
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("send_ams_control_command: printer not connected: %s", name)
        return _no_printer(name)
    try:
        from bpm.bambutools import AMSControlCommand
        cmd_upper = cmd.upper()
        log.debug("send_ams_control_command: resolved cmd=%s for %s", cmd_upper, name)
        ams_cmd = AMSControlCommand[cmd_upper]
        log.debug("send_ams_control_command: calling printer.send_ams_control_command(%s) for %s", ams_cmd, name)
        printer.send_ams_control_command(ams_cmd)
        log.debug("send_ams_control_command: command sent to %s", name)
        return f"AMS control command {cmd_upper} sent to '{name}'."
    except KeyError:
        log.error("send_ams_control_command: unknown cmd '%s' for %s", cmd, name)
        return f"Error: Unknown AMS control command '{cmd}'. Must be one of: PAUSE, RESUME, RESET."
    except Exception as e:
        log.error("send_ams_control_command: error for %s: %s", name, e, exc_info=True)
        return f"Error sending AMS control command to '{name}': {e}"
