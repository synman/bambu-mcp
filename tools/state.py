"""
tools/state.py — Read-only state tools for Bambu Lab printers.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from enum import Enum

log = logging.getLogger(__name__)

from session_manager import session_manager
from data_collector import data_collector


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


def _no_printer(name: str) -> dict:
    return {"error": f"Printer '{name}' not connected"}


def _apply_hms_historical(errors: list) -> list:
    """
    Active error state = one device_error + one device_hms (the first of each).
    Any additional device_hms entries are historical. If there is no device_error,
    all device_hms entries are historical (the fault is no longer active).
    """
    has_device_error = any(e.get("type") == "device_error" for e in errors)
    seen_first_hms = False
    result = []
    for e in errors:
        if e.get("type") == "device_hms":
            if has_device_error and not seen_first_hms:
                seen_first_hms = True
                result.append(e)
            else:
                result.append({**e, "is_critical": False, "severity": "Historical"})
        else:
            result.append(e)
    return result


def get_printer_state(name: str) -> dict:
    """
    Return the full live BambuState for the named printer as a dict.

    Includes extruder info, AMS units, spools, climate, HMS errors, and
    print-progress fields. Returns an error dict if the printer is not connected.
    """
    log.debug("get_printer_state: called for printer=%s", name)
    state = session_manager.get_state(name)
    if state is None:
        log.warning("get_printer_state: printer %s not connected", name)
        return _no_printer(name)
    log.debug("get_printer_state: serializing state for %s", name)
    result = _serialize(state)
    if "hms_errors" in result:
        result["hms_errors"] = _apply_hms_historical(result["hms_errors"])
    log.debug("get_printer_state: hms_errors count=%d for %s", len(result.get("hms_errors", [])), name)
    log.debug("get_printer_state: returning result for %s", name)
    return result


def get_job_info(name: str) -> dict:
    """
    Return the ActiveJobInfo for the current (or last) print job as a dict.

    Includes subtask name, gcode file, plate number, stage, layer counts,
    print percentage, and elapsed/remaining time in minutes.

    Field semantics:
    - gcode_state: string — known values: "IDLE", "PREPARE", "RUNNING", "PAUSE",
      "FINISH", "FAILED", "SLICING", "INIT".
    - stage: integer stage code —
        0=idle/finished, 1=auto-leveling, 2=heatbed preheating,
        3=sweeping XY mech, 4=changing filament, 6=M400 pause,
        7=paused by filament runout, 8=heating nozzle,
        9=calibrating extrusion, 10=scanning bed surface,
        11=inspecting first layer, 12=identifying build plate,
        13=calibrating micro lidar, 14=homing toolhead, 15=cleaning nozzle,
        16=checking extruder temp, 17=paused by user,
        18=paused by front cover removal, 19=calibrating extrusion flow,
        20=paused by nozzle temp malfunction,
        21=paused by heat bed temp malfunction, 255=printing normally.
    """
    log.debug("get_job_info: called for printer=%s", name)
    job = session_manager.get_job(name)
    if job is None:
        log.warning("get_job_info: printer %s not connected", name)
        return _no_printer(name)
    log.debug("get_job_info: returning result for %s", name)
    return _serialize(job)


def get_temperatures(name: str) -> dict:
    """
    Return current and target temperatures for all nozzles, the bed, and chamber.

    For single-extruder printers the nozzles list has one entry.
    For dual-extruder (H2D) printers it has two entries.
    """
    log.debug("get_temperatures: called for printer=%s", name)
    state = session_manager.get_state(name)
    if state is None:
        log.warning("get_temperatures: printer %s not connected", name)
        return _no_printer(name)
    nozzles = [
        {"id": e.id, "temp": e.temp, "target": e.temp_target}
        for e in (state.extruders or [])
    ]
    if not nozzles:
        nozzles = [{"id": 0, "temp": state.active_nozzle_temp, "target": state.active_nozzle_temp_target}]
    climate = state.climate
    log.debug("get_temperatures: returning result for %s", name)
    return {
        "nozzles": nozzles,
        "bed": {"temp": climate.bed_temp, "target": climate.bed_temp_target},
        "chamber": {"temp": climate.chamber_temp, "target": climate.chamber_temp_target},
    }


def get_fan_speeds(name: str) -> dict:
    """
    Return the current fan speeds as percentages for all fans on the printer.

    Fans reported: part_cooling, aux (recirculation), exhaust (chamber), heatbreak.
    """
    log.debug("get_fan_speeds: called for printer=%s", name)
    state = session_manager.get_state(name)
    if state is None:
        log.warning("get_fan_speeds: printer %s not connected", name)
        return _no_printer(name)
    c = state.climate
    log.debug("get_fan_speeds: returning result for %s", name)
    return {
        "part_cooling_pct": c.part_cooling_fan_speed_percent,
        "aux_pct": c.aux_fan_speed_percent,
        "exhaust_pct": c.exhaust_fan_speed_percent,
        "heatbreak_pct": c.heatbreak_fan_speed_percent,
    }


def get_spool_info(name: str) -> dict:
    """
    Return the active spool and a list of all spools associated with the printer.

    The active spool is identified by matching active_ams_id / active_tray_id from
    BambuState. Each spool dict includes filament type, color, remaining percentage,
    nozzle temp range, and drying parameters.

    Field semantics:
    - active_ams_id: internal chip_id of the AMS unit. 0 = first AMS unit (AMS 2 Pro);
      128 = AMS HT unit (Bambu's internal ID for AMS HT). NOT the same as the
      0-based unit_id used by get_ams_units() / load_filament().
    - active_tray_id: slot index within the AMS (0–3). 254 = external spool holder.
    - Each spool dict: filament_type (str), color (hex string e.g. '#FF0000'),
      remaining_pct (0–100), nozzle_temp_min/max (°C), dry_temp (°C), dry_time (hours).
    """
    log.debug("get_spool_info: called for printer=%s", name)
    state = session_manager.get_state(name)
    if state is None:
        log.warning("get_spool_info: printer %s not connected", name)
        return _no_printer(name)
    all_spools = [_serialize(s) for s in (state.spools or [])]
    active = None
    for s in (state.spools or []):
        if s.ams_id == state.active_ams_id and s.slot_id == state.active_tray_id:
            active = _serialize(s)
            break
    log.debug("get_spool_info: returning result for %s", name)
    return {"active_spool": active, "spools": all_spools}


def get_ams_status(name: str) -> dict:
    """
    Return the status of all AMS units and their slots.

    Each unit includes temperature, humidity, heater state, drying state, and
    tray-existence flags. The global AMS status string is also included.
    """
    log.debug("get_ams_status: called for printer=%s", name)
    state = session_manager.get_state(name)
    if state is None:
        log.warning("get_ams_status: printer %s not connected", name)
        return _no_printer(name)
    log.debug("get_ams_status: returning result for %s", name)
    return {
        "ams_status": state.ams_status_text,
        "ams_count": state.ams_connected_count,
        "units": [_serialize(u) for u in (state.ams_units or [])],
    }


def get_hms_errors(name: str) -> dict:
    """
    Return the list of active HMS (Health Management System) errors.

    Each entry is a dict with numeric error code and a human-readable description.
    Returns an empty list when no errors are active.

    Active vs. historical error logic:
    - An error is ACTIVELY FAULTED only when BOTH a `device_hms` entry AND a
      `device_error` entry are present for the same code. Only the first device_hms
      entry paired with a device_error is treated as actively faulted.
    - A `device_hms` entry with no matching `device_error` = historical / cleared
      error (no longer active). These are returned with severity="Historical" and
      is_critical=False.
    - Error codes follow pattern HMS_XXXX-XXXX-XXXX-XXXX. The first segment encodes
      the hardware module (e.g. 0x05=AMS, 0x07=Toolhead); the second segment encodes
      the error category and severity.
    """
    log.debug("get_hms_errors: called for printer=%s", name)
    state = session_manager.get_state(name)
    if state is None:
        log.warning("get_hms_errors: printer %s not connected", name)
        return _no_printer(name)
    errors = _apply_hms_historical(list(state.hms_errors or []))
    log.debug("get_hms_errors: errors count=%d, print_error=%s for %s", len(errors), state.print_error, name)
    log.debug("get_hms_errors: returning result for %s", name)
    return {"hms_errors": errors, "print_error": state.print_error}


def get_print_progress(name: str) -> dict:
    """
    Return print progress: percentage complete, current/total layers, and time remaining.

    Also includes elapsed time in minutes, the current stage name, and gcode state.

    Field semantics:
    - gcode_state: string — "IDLE", "PREPARE", "RUNNING", "PAUSE", "FINISH",
      "FAILED", "SLICING", "INIT".
    - stage: integer stage code — see get_job_info() for the full table (0=idle,
      255=printing normally, 17=paused by user, etc.).
    """
    log.debug("get_print_progress: called for printer=%s", name)
    state = session_manager.get_state(name)
    job = session_manager.get_job(name)
    if state is None:
        log.warning("get_print_progress: printer %s not connected", name)
        return _no_printer(name)
    log.debug("get_print_progress: returning result for %s", name)
    return {
        "gcode_state": state.gcode_state,
        "print_percentage": job.print_percentage if job else 0,
        "current_layer": job.current_layer if job else 0,
        "total_layers": job.total_layers if job else 0,
        "elapsed_minutes": job.elapsed_minutes if job else 0,
        "remaining_minutes": job.remaining_minutes if job else 0,
        "stage_name": job.stage_name if job else "",
        "subtask_name": job.subtask_name if job else "",
    }


def get_capabilities(name: str) -> dict:
    """
    Return the hardware capabilities dict for the printer.

    Capabilities are discovered during the initial MQTT handshake and telemetry
    analysis. Fields include has_ams, has_dual_extruder, has_camera, etc.
    """
    log.debug("get_capabilities: called for printer=%s", name)
    config = session_manager.get_config(name)
    if config is None:
        log.warning("get_capabilities: printer %s not connected", name)
        return _no_printer(name)
    log.debug("get_capabilities: returning result for %s", name)
    return _serialize(config.capabilities)


def get_printer_info(name: str) -> dict:
    """
    Return the printer model, serial number, and firmware version.

    Also includes the AMS firmware version when available.
    """
    log.debug("get_printer_info: called for printer=%s", name)
    config = session_manager.get_config(name)
    if config is None:
        log.warning("get_printer_info: printer %s not connected", name)
        return _no_printer(name)
    log.debug("get_printer_info: returning result for %s", name)
    return {
        "model": config.printer_model.name if config.printer_model else "UNKNOWN",
        "serial": config.serial_number,
        "firmware_version": config.firmware_version,
        "ams_firmware_version": config.ams_firmware_version,
    }


def get_wifi_signal(name: str) -> dict:
    """
    Return the Wi-Fi signal strength for the printer in dBm.

    A stronger (less negative) value indicates a better signal.
    """
    log.debug("get_wifi_signal: called for printer=%s", name)
    state = session_manager.get_state(name)
    if state is None:
        log.warning("get_wifi_signal: printer %s not connected", name)
        return _no_printer(name)
    log.debug("get_wifi_signal: returning result for %s", name)
    return {"wifi_signal": state.wifi_signal_strength}


def get_monitoring_data(name: str) -> dict:
    """
    Return telemetry history for charting: temperature and fan speed time-series.

    Data is provided as rolling 60-minute collections sampled every ~2.5 seconds.
    Also includes gcode_state_durations (time spent in each print state per job).

    Note on gcode_state_durations: a FAILED entry does not mean the current job failed.
    The rolling window captures the prior job's terminal state before the current job
    started. A print that has been RUNNING continuously will show a small FAILED duration
    from the previous job alongside its dominant RUNNING duration.
    """
    log.debug("get_monitoring_data: called for printer=%s", name)
    data = data_collector.get_all_data(name)
    log.debug("get_monitoring_data: data present=%s for %s", data is not None, name)
    if data is None:
        log.warning("get_monitoring_data: printer %s not connected", name)
        return _no_printer(name)
    log.debug("get_monitoring_data: returning result for %s", name)
    return data
