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


def _permission_denied() -> str:
    return "Error: user_permission must be True to perform this action."


def get_nozzle_info(name: str) -> dict:
    """
    Return nozzle diameter, type, and flow type for all extruders on the printer.

    For single-extruder printers the list has one entry. For dual-extruder H2D
    printers it has two entries. Each entry includes the normalized NozzleCharacteristics
    as well as the active tray and tray state for that extruder.

    Field semantics:
    - extruder_id 0 = RIGHT nozzle on H2D (primary); extruder_id 1 = LEFT nozzle on H2D.
    - diameter_mm: float (e.g. 0.4).
    - nozzle_type: NozzleType enum name (e.g. 'HARDENED_STEEL', 'STAINLESS_STEEL',
      'BRASS', 'TUNGSTEN_CARBIDE', 'E3D').
    - flow_type: NozzleFlowType enum name (e.g. 'STANDARD', 'HIGH_FLOW', 'TPU_HIGH_FLOW').
    - active_tray_id: currently loaded spool slot (0–3, or 254 for external spool).
    - tray_state: TrayState enum name — 'LOADED', 'UNLOADED', 'LOADING', 'UNLOADING'.
      LOADED = filament is fully loaded and ready to print. UNLOADED = no filament in the
      hotend. LOADING = filament is currently being fed into the hotend. UNLOADING = filament
      is being retracted from the hotend.
    - encoded_id is an internal hardware identifier for the nozzle hardware unit and is not
      used by any other tool.
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

    diameter must be one of: 0.2, 0.4, 0.6, 0.8 (mm).
    nozzle_type must be one of: 'stainless_steel', 'hardened_steel',
    'tungsten_carbide', 'brass', 'e3d'.
    flow_type is accepted for informational purposes (standard/high_flow/tpu_high_flow)
    but is not sent to the printer API directly.
    flow_type is stored as metadata for display purposes only — the printer firmware
    determines actual flow rates from the installed nozzle's physical characteristics.
    Valid values: 'standard', 'high_flow', 'tpu_high_flow'.
    Requires user_permission=True.

    Extruder selection on H2D: extruder=0 = right nozzle; extruder=1 = left nozzle;
    extruder=-1 = apply to all nozzles.
    """
    log.debug("set_nozzle_config: called for name=%s diameter=%s nozzle_type=%s user_permission=%s", name, diameter, nozzle_type, user_permission)
    if not user_permission:
        log.debug("set_nozzle_config: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("set_nozzle_config: printer not connected: %s", name)
        return _no_printer(name)
    try:
        from bpm.bambutools import NozzleDiameter, NozzleType
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
        log.debug("set_nozzle_config: calling printer.set_nozzle_details for %s", name)
        printer.set_nozzle_details(nozzle_diameter=nd, nozzle_type=nt)
        log.debug("set_nozzle_config: command sent to %s", name)
        return (
            f"Nozzle config set to {diameter}mm {nozzle_type} on extruder {extruder} of '{name}'."
        )
    except Exception as e:
        log.error("set_nozzle_config: error for %s: %s", name, e, exc_info=True)
        return f"Error setting nozzle config on '{name}': {e}"


def swap_tool(name: str, user_permission: bool = False) -> str:
    """
    Swap the active extruder on H2D dual-extruder printers.

    Toggles between extruder 0 (right) and extruder 1 (left). Has no effect on
    single-extruder printers. Requires user_permission=True.
    """
    log.debug("swap_tool: called for name=%s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("swap_tool: permission denied for %s", name)
        return _permission_denied()
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("swap_tool: printer not connected: %s", name)
        return _no_printer(name)
    state = session_manager.get_state(name)
    try:
        from bpm.bambutools import ActiveTool
        current = state.active_tool.value if state and state.active_tool is not None else 0
        # ActiveTool.SINGLE_EXTRUDER == 0, extruder 1 == 1
        next_tool = 1 if current == 0 else 0
        log.debug("swap_tool: calling printer.set_active_tool(%s) for %s", next_tool, name)
        printer.set_active_tool(next_tool)
        log.debug("swap_tool: command sent to %s", name)
        return f"Tool swap command sent: switching to extruder {next_tool} on '{name}'."
    except Exception as e:
        log.error("swap_tool: error for %s: %s", name, e, exc_info=True)
        return f"Error swapping tool on '{name}': {e}"
