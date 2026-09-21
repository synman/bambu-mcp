"""
tools/nozzle.py — Nozzle configuration tools for Bambu Lab printers.

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


def _permission_denied(consequence: str) -> str:
    return f"Error: user_permission must be True to perform this action. {consequence}"


def get_nozzle_info(name: str) -> dict:
    """
    Return nozzle diameter, type, and flow type for all extruders on the printer.

    WHEN to use: check which nozzle hardware the MCP state believes is installed on each
    extruder, together with the active tray and tray state, before printing or before
    calling set_nozzle_config.

    Sibling disambiguation: get_nozzle_info only reads the cached printer state.
    refresh_nozzles sends a command asking the printer to push back its current nozzle
    state, so call that first after physically swapping a nozzle; get_climate returns nozzle
    temperatures, not nozzle hardware.

    Args:
        name: Printer name as returned by get_configured_printers.

    Returns:
        ``{"nozzles": [{extruder_id, diameter_mm, nozzle_type, flow_type, encoded_id,
        active_tray_id, tray_state}]}`` on success, or ``{"error": "Printer '<name>' not
        connected"}`` when the printer has no session. Single-extruder printers return one
        entry; dual-extruder H2D printers return two. Each entry carries the normalized
        NozzleCharacteristics plus the active tray and tray state for that extruder.

    Notes:
        Field semantics:

        - extruder_id: -1 on single-extruder printers; 0 = RIGHT nozzle (primary) and
          1 = LEFT nozzle on H2D. A single-extruder entry with extruder_id 0 appears only
          before any telemetry has arrived. Do NOT pass a reported -1 to set_nozzle_config's
          extruder parameter: there it means "apply to all nozzles".
        - diameter_mm: float (e.g. 0.4).
        - nozzle_type: NozzleType enum name (e.g. 'HARDENED_STEEL', 'STAINLESS_STEEL',
          'BRASS', 'TUNGSTEN_CARBIDE', 'E3D', 'UNKNOWN').
        - flow_type: NozzleFlowType enum name (e.g. 'STANDARD', 'HIGH_FLOW', 'TPU_HIGH_FLOW',
          'UNKNOWN'). Single-extruder printers always report 'STANDARD' (fixed by bpm).
        - active_tray_id: absolute tray id currently selected for that extruder, as reported
          by the printer: ams_unit_index * 4 + slot for a 4-slot AMS (so it can exceed 3),
          128 + slot for AMS HT; 254 = the external spool holder of a single-nozzle printer
          or the LEFT holder of a dual-nozzle printer; 255 = the RIGHT holder; -1 = no tray
          active.
        - tray_state: TrayState enum name: 'LOADED', 'UNLOADED', 'LOADING', 'UNLOADING'. It
          is DERIVED by bpm, not a measurement of filament in the hotend. Single-extruder:
          LOADING/UNLOADING come from the job stage, LOADED means an AMS tray is selected,
          and UNLOADED means none is selected or the external spool holder is in use, so a
          print from the external spool reports UNLOADED. Dual-extruder: derived from that
          extruder's reported filament state and status.
        - encoded_id is the raw nozzle identifier string from telemetry (may be a bare value
          such as "0") and is not used by any other tool.
        - When the printer has reported no nozzle data, diameter_mm is 0.0, nozzle_type and
          flow_type are "UNKNOWN" and encoded_id is "".
    """
    log.debug("get_nozzle_info: called for name=%s", name)
    state = session_manager.get_state(name)
    if state is None:
        log.warning("get_nozzle_info: printer %s not connected", name)
        return {"error": f"Printer '{name}' not connected"}
    extruders = state.extruders or []
    result = []
    for e in extruders:
        nozzle = _serialize(e.nozzle) or {}
        result.append({
            "extruder_id": e.id,
            "diameter_mm": nozzle.get("diameter_mm", 0.0),
            "nozzle_type": nozzle.get("material", "UNKNOWN"),
            "flow_type": nozzle.get("flow", "UNKNOWN"),
            "encoded_id": nozzle.get("encoded_id", ""),
            "active_tray_id": e.active_tray_id,
            "tray_state": e.tray_state.name if isinstance(e.tray_state, Enum) else str(e.tray_state),
        })
    if not result:
        active = _serialize(state.active_nozzle) or {}
        result.append({
            "extruder_id": 0,
            "diameter_mm": active.get("diameter_mm", 0.0),
            "nozzle_type": active.get("material", "UNKNOWN"),
            "flow_type": active.get("flow", "UNKNOWN"),
            "encoded_id": active.get("encoded_id", ""),
            "active_tray_id": state.active_tray_id,
            "tray_state": state.active_tray_state.name
            if isinstance(state.active_tray_state, Enum)
            else str(state.active_tray_state),
        })
    log.debug("get_nozzle_info: returning result for %s", name)
    return {"nozzles": result}


def set_nozzle_config(
    name: str,
    diameter: float,
    nozzle_type: str,
    flow_type: str = "standard",
    extruder: int = 0,
    user_permission: bool = False,
) -> str:
    """
    Inform the printer of the currently installed nozzle diameter and material type.

    WHEN to use: after physically installing a different nozzle, to tell the printer its
    diameter, material and flow type so the firmware applies the correct temperature
    limits, flow rates and material compatibility checks.

    WRITE GUARD: sends the nozzle configuration to the printer over MQTT (SET_ACCESSORIES on
    single-extruder printers, SET_NOZZLE on dual-extruder printers) and, when the target
    extruder differs from the cached active tool, first switches the active extruder with
    set_active_tool(), so the active extruder may change as a side effect. Blocked during
    active prints (gcode_state RUNNING or PREPARE) because a tool swap mid-print is
    catastrophic on H2D. With user_permission False the tool changes nothing and returns the
    refusal string "Error: user_permission must be True to perform this action." followed by
    a sentence naming this consequence.

    Sibling disambiguation: set_nozzle_config records which nozzle hardware is installed;
    set_nozzle_temp sets a nozzle temperature target and does not describe the hardware.
    swap_tool also switches the active extruder but sends no nozzle settings. To read the
    current configuration use get_nozzle_info.

    Args:
        name: Printer name as returned by get_configured_printers.
        diameter: Nozzle diameter in mm. Pass one of 0.2, 0.4, 0.6, 0.8. Not fully enforced:
            0 is accepted (NozzleDiameter.UNKNOWN) and sent to the printer, and a value that
            cannot be converted to a float (such as None) skips the "Invalid diameter" string
            and returns the generic "Error setting nozzle config" string.
        nozzle_type: Nozzle material, case-insensitive. One of 'stainless_steel',
            'hardened_steel', 'tungsten_carbide', 'brass', 'e3d'. 'brass' and 'e3d' work only
            on single-extruder printers. On a dual-extruder printer they have no encoded
            nozzle identifier, so the call returns "Error setting nozzle config on '<name>':
            Unsupported nozzle_type for encoded ID: NozzleType.BRASS" (or E3D) AFTER
            set_active_tool() has already switched the active extruder.
        flow_type: Nozzle flow type, case-insensitive. Must be one of 'standard',
            'high_flow', 'tpu_high_flow'. Default 'standard'. On dual-extruder printers this
            IS sent to the printer: it is encoded into the SET_NOZZLE command's
            nozzle-identifier SKU alongside nozzle_type. On single-extruder printers the
            underlying SET_ACCESSORIES command carries no flow field, so flow_type has no
            effect there.
        extruder: Extruder to configure on H2D: 0 = right nozzle, 1 = left nozzle, -1 = apply
            to all nozzles (extruder 0 then extruder 1). Default 0. Not range-checked: any
            other integer is forwarded to set_active_tool() and to the nozzle command.
        user_permission: Must be True to perform the change. Default False.

    Returns:
        A plain string, never a dict. Success: "Nozzle config set to <diameter>mm
        <nozzle_type> on <extruder N | all extruders> of '<name>'." This means the MQTT
        command(s) were published; the printer's acceptance is not awaited, so verify with
        get_nozzle_info after the next telemetry update. On dual-extruder printers the
        SET_NOZZLE command also carries a fixed wear of 0. Errors, all strings:
        the "Error: user_permission must be True ..." refusal; "Error: Printer '<name>' not
        connected."; the active-print block message ("Blocked: '<name>' is currently
        <state>. ..."); "Error: Invalid diameter <diameter>. Valid values: [...]";
        "Error: Unknown nozzle_type '<nozzle_type>'. Valid: [...]"; "Error: Unknown
        flow_type '<flow_type>'. Valid: [...]"; "Error setting nozzle config on '<name>':
        <exception>".

    Notes:
        The switch decision compares extruder with the active tool read ONCE before the
        call. On a single-extruder printer the active tool reads -1, so a select_extruder
        command for extruder 0 is published before EVERY SET_ACCESSORIES (which applies to
        the currently active extruder), not only when extruders differ. On a dual-extruder
        printer SET_NOZZLE names its target extruder by id, so the set_active_tool() call is
        a side effect of this tool rather than a requirement of the command. For
        extruder=-1 the printer is left on extruder 1 when it started on extruder 0 or had no
        active tool (-1 or 15), and on extruder 0 when it started on extruder 1.
    """
    log.debug("set_nozzle_config: called for name=%s diameter=%s nozzle_type=%s extruder=%s user_permission=%s", name, diameter, nozzle_type, extruder, user_permission)
    if not user_permission:
        log.debug("set_nozzle_config: permission denied for %s", name)
        return _permission_denied(
            "This would tell the printer which nozzle diameter, material and flow type are "
            "installed, switching the active extruder first if needed."
        )
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("set_nozzle_config: printer not connected: %s", name)
        return _no_printer(name)
    from tools._guards import check_active_print_guard
    blocked = check_active_print_guard(printer, name, "set_nozzle_config")
    if blocked:
        return blocked.get("error", "Blocked: active print in progress.")
    state = session_manager.get_state(name)
    try:
        from bpm.bambutools import NozzleDiameter, NozzleFlowType, NozzleType
        try:
            nd = NozzleDiameter(float(diameter))
        except ValueError:
            valid = [v.value for v in NozzleDiameter if v != NozzleDiameter.UNKNOWN]
            return f"Error: Invalid diameter {diameter}. Valid values: {valid}"
        try:
            nt = NozzleType[nozzle_type.upper()]
        except KeyError:
            valid = [v.name.lower() for v in NozzleType if v != NozzleType.UNKNOWN]
            return f"Error: Unknown nozzle_type '{nozzle_type}'. Valid: {valid}"
        try:
            nf = NozzleFlowType[flow_type.upper()]
        except KeyError:
            valid = [v.name.lower() for v in NozzleFlowType if v != NozzleFlowType.UNKNOWN]
            return f"Error: Unknown flow_type '{flow_type}'. Valid: {valid}"

        current_tool = state.active_tool.value if state and state.active_tool is not None else 0

        def _apply_to_extruder(target: int):
            if target != current_tool:
                log.debug("set_nozzle_config: switching to extruder %s for %s", target, name)
                printer.set_active_tool(target)
            log.debug("set_nozzle_config: calling printer.set_nozzle_details for extruder %s on %s", target, name)
            printer.set_nozzle_details(
                nozzle_diameter=nd, nozzle_type=nt, nozzle_flow=nf, extruder_id=target
            )

        if extruder == -1:
            _apply_to_extruder(0)
            _apply_to_extruder(1)
            applied = "all extruders"
        else:
            _apply_to_extruder(extruder)
            applied = f"extruder {extruder}"

        log.debug("set_nozzle_config: command(s) sent to %s", name)
        return f"Nozzle config set to {diameter}mm {nozzle_type} on {applied} of '{name}'."
    except Exception as e:
        log.error("set_nozzle_config: error for %s: %s", name, e, exc_info=True)
        return f"Error setting nozzle config on '{name}': {e}"


def swap_tool(name: str, extruder_id: int | None = None, user_permission: bool = False) -> str:
    """
    Swap the active extruder on H2D dual-extruder printers.

    WHEN to use: switch which extruder (0 = right, 1 = left) the printer uses for subsequent
    moves and extrusions, either by toggling or by selecting a specific extruder.

    WRITE GUARD: sends a SET_ACTIVE_TOOL command over MQTT that changes the printer's active
    extruder. Blocked during active prints (gcode_state RUNNING or PREPARE): on H2D,
    swapping the active extruder mid-print crashes the inactive nozzle into the active
    print, and firmware provides NO protection against this because low-level command
    injection bypasses all print-job safety checks. With user_permission False the tool
    changes nothing and returns the refusal string "Error: user_permission must be True to
    perform this action." followed by a sentence naming this consequence.

    Sibling disambiguation: swap_tool only selects the active extruder. set_nozzle_config
    describes the installed nozzle hardware (and switches the active extruder itself only as
    a side effect of that); set_nozzle_temp sets a nozzle temperature target.

    Args:
        name: Printer name as returned by get_configured_printers.
        extruder_id: None (default) toggles using the CACHED active tool: extruder 0 becomes
            1, and any other cached value (-1 single-extruder, 15 transitional, or unknown)
            becomes 0, so it is a true toggle only when the cached value is 0 or 1. 0 or 1
            selects that extruder directly, regardless of the current state; the value is not
            range-checked and any integer is published as the extruder index.
        user_permission: Must be True to perform the swap. Default False.

    Returns:
        A plain string, never a dict. Success: "Tool selection command sent: extruder <N> now
        active on '<name>'." Errors, all strings: the "Error: user_permission must be True
        ..." refusal; "Error: Printer '<name>' not connected."; the active-print block
        message ("Blocked: '<name>' is currently <state>. ..."); "Error swapping tool on
        '<name>': <exception>".

    Notes:
        The command is published whatever the printer type: there is no dual-extruder check,
        so a single-extruder printer still receives it and the tool still returns the success
        string. Whether that firmware ignores it is not established by this code. The success
        string means the command was published, not that the extruder changed.
    """
    log.debug("swap_tool: called for name=%s extruder_id=%s user_permission=%s", name, extruder_id, user_permission)
    if not user_permission:
        log.debug("swap_tool: permission denied for %s", name)
        return _permission_denied(
            "This would change the printer's active extruder."
        )
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("swap_tool: printer not connected: %s", name)
        return _no_printer(name)
    from tools._guards import check_active_print_guard
    blocked = check_active_print_guard(printer, name, "swap_tool")
    if blocked:
        return blocked.get("error", "Blocked: active print in progress.")
    state = session_manager.get_state(name)
    try:
        from bpm.bambutools import ActiveTool
        if extruder_id is not None:
            target = int(extruder_id)
            log.debug("swap_tool: directly selecting extruder %s on %s", target, name)
        else:
            current = state.active_tool.value if state and state.active_tool is not None else 0
            target = 1 if current == 0 else 0
            log.debug("swap_tool: toggling from %s to %s on %s", current, target, name)
        printer.set_active_tool(target)
        log.debug("swap_tool: command sent to %s", name)
        return f"Tool selection command sent: extruder {target} now active on '{name}'."
    except Exception as e:
        log.error("swap_tool: error for %s: %s", name, e, exc_info=True)
        return f"Error swapping tool on '{name}': {e}"


def refresh_nozzles(name: str, user_permission: bool = False) -> str:
    """
    Ask the printer to push back its current nozzle state.

    WHEN to use: after physically swapping a nozzle on an H2D or any other dual-extruder
    printer, to request that the printer report the nozzle now installed.

    WRITE GUARD: publishes a REFRESH_NOZZLE command to the printer's request topic, asking
    the printer to push back its current nozzle state, which updates get_nozzle_info() once
    reported. The tool itself performs no active-print check. With
    user_permission False the tool changes nothing and returns the refusal string "Error:
    user_permission must be True to perform this action." followed by a sentence naming this
    consequence.

    Sibling disambiguation: refresh_nozzles asks the printer to push back its current nozzle
    state; get_nozzle_info only reads the state already held by the MCP server.
    trigger_printer_refresh (string return) and force_state_refresh (dict return) are both
    guarded and re-request the printer's full state rather than nozzle state specifically.

    Args:
        name: Printer name as returned by get_configured_printers.
        user_permission: Must be True to send the refresh command. Default False.

    Returns:
        A plain string, never a dict. Success: "Nozzle refresh command sent to '<name>'."
        Errors, all strings: the "Error: user_permission must be True ..." refusal; "Error:
        Printer '<name>' not connected."; "Error refreshing nozzles on '<name>':
        <exception>".

    Notes:
        Whether the firmware re-detects nozzle hardware is not established by this code. The
        command is published without checking the MQTT session state, but a paused session
        has unsubscribed from the report topic, so then the command is sent and
        get_nozzle_info() does not change. The success string means the command was published.
    """
    log.debug("refresh_nozzles: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        return _permission_denied(
            "This would send a REFRESH_NOZZLE command asking the printer to re-read its "
            "installed nozzle hardware and push back the nozzle state."
        )
    printer = session_manager.get_printer(name)
    if printer is None:
        return _no_printer(name)
    try:
        printer.refresh_nozzles()
        log.debug("refresh_nozzles: command sent to %s", name)
        return f"Nozzle refresh command sent to '{name}'."
    except Exception as e:
        log.error("refresh_nozzles: error for %s: %s", name, e, exc_info=True)
        return f"Error refreshing nozzles on '{name}': {e}"
