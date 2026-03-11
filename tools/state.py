"""
tools/state.py — Read-only state tools for Bambu Lab printers.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
from enum import Enum

import webcolors
from bpm.bambucommands import FILAMENT_CATALOG

log = logging.getLogger(__name__)

from session_manager import session_manager
from data_collector import data_collector

# Pre-build CSS3 color lookup: {(r, g, b): name}
_CSS3_COLORS: dict[tuple, str] = {
    tuple(webcolors.hex_to_rgb(webcolors.name_to_hex(n))): n
    for n in webcolors.names("css3")
}


def _hex_to_color_name(hex_color: str) -> str:
    """Convert a hex color (#RRGGBB or #RRGGBBAA) to the nearest CSS3 color name."""
    if not hex_color or not hex_color.startswith("#"):
        return hex_color or ""
    # Strip alpha channel if present (#RRGGBBAA → #RRGGBB)
    h = hex_color.lstrip("#")
    if len(h) == 8:
        h = h[:6]
    if len(h) != 6:
        return hex_color
    try:
        rgb = webcolors.hex_to_rgb(f"#{h}")
    except ValueError:
        return hex_color
    # Exact match first
    exact = _CSS3_COLORS.get(tuple(rgb))
    if exact:
        return exact
    # Nearest neighbor by Euclidean distance in RGB space
    nearest = min(
        _CSS3_COLORS.items(),
        key=lambda item: math.dist(item[0], rgb),
    )
    return nearest[1]


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

    This is a convenience tool that bundles all printer state into one response.
    For routine queries, prefer the targeted tools — they are smaller and faster:
      get_temperatures()     — nozzle, bed, and chamber temperatures
      get_spool_info()       — active spool and all AMS spools
      get_job_info()         — current print job progress
      get_nozzle_info()      — nozzle diameter, type, and tray state
      get_print_progress()   — print percentage, layer, and time remaining
      get_ams_units()        — AMS unit and slot details
      get_hms_errors()       — active and historical HMS errors
      get_fan_speeds()       — all fan speeds as percentages
      get_climate()          — temperatures and chamber door state

    Response may be gzip+base64 compressed if the payload is large. Decompress:
      import gzip, json, base64
      data = json.loads(gzip.decompress(base64.b64decode(r["data"])))
    If the compressed envelope itself exceeds the MCP response limit, fall back to:
      GET /api/printer?printer=<name>
    """
    from tools._response import compress_if_large
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
    return compress_if_large(result)


def get_job_info(name: str) -> dict:
    """
    Return the ActiveJobInfo for the current (or last) print job as a dict.

    Includes subtask name, gcode file, plate number, stage_id, layer counts,
    print percentage, and elapsed/remaining time in minutes.

    Field semantics:
    - stage_id: integer stage code —
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
    Note: gcode_state is NOT a field of ActiveJobInfo and is not returned by this
    tool. Read gcode_state from get_print_progress() or get_printer_state() instead.
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


# Simple tray_info_idx → display name lookup derived from the canonical BPM catalog.
_FILAMENT_LOOKUP: dict[str, str] = {e["tray_info_idx"]: e["name"] for e in FILAMENT_CATALOG}


def _spool_display_name(spool: dict) -> str:
    """Synthesize a human-readable name: catalog name + color_name, or type + color_name as fallback."""
    catalog = _FILAMENT_LOOKUP.get(spool.get("tray_info_idx", ""), "")
    profile = catalog or spool.get("type", "")
    color = spool.get("color_name") or spool.get("color", "")
    if profile and color:
        return f"{profile} ({color})"
    return profile or color or ""


def _enrich_spool(spool: dict) -> dict:
    """Add color_name and display_name fields to a serialized spool dict."""
    spool["color_name"] = _hex_to_color_name(spool.get("color", ""))
    spool["display_name"] = _spool_display_name(spool)
    return spool


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
    - Each spool dict: type (str), color (hex string e.g. '#FF0000'),
      remaining_percent (0–100), nozzle_temp_min/max (°C), drying_temp (°C), drying_time (hours).
    - name (if present): Bambu Lab vendor-specific brand label (e.g. "Bambu PLA Basic").
      Not present on third-party spools and not a reliable identifier. The true identity
      of a spool is color + tray_info_idx (base profile catalog code, e.g. "GFA00").
      When name is absent, the vendor name can be derived from tray_info_idx:
      GFA00="Bambu PLA Basic", GFA01="Bambu PLA Matte", GFB00="Bambu ABS", GFB01="Bambu ASA".
    - display_name: synthesized human-readable label always present in each spool dict.
      Rule: "{catalog or type} ({color_name})".
    - color_name: nearest CSS3 color name for the spool color hex (e.g. "darkorange").
      Derived from color field; alpha channel stripped before lookup. Use color for
      programmatic/swatch use; use color_name for human-readable descriptions.
    """
    log.debug("get_spool_info: called for printer=%s", name)
    state = session_manager.get_state(name)
    if state is None:
        log.warning("get_spool_info: printer %s not connected", name)
        return _no_printer(name)
    all_spools = [_enrich_spool(_serialize(s)) for s in (state.spools or [])]
    active = None
    for s in (state.spools or []):
        if s.ams_id == state.active_ams_id and s.slot_id == state.active_tray_id:
            active = _enrich_spool(_serialize(s))
            break
    log.debug("get_spool_info: returning result for %s", name)
    return {"active_spool": active, "spools": all_spools}


def get_ams_status(name: str) -> dict:
    """
    Return the status of all AMS units and their slots.

    Each unit includes temperature, humidity, heater state, drying state, and
    tray-existence flags. The global AMS status string is also included.

    humidity_index scale: 1=WET (alert, filament needs drying), 5=DRY (good, no
    action needed). Higher numbers mean DRIER — the scale is counterintuitive.
    Only values of 1 or 2 indicate a moisture problem. Value 5 = completely dry.
    Value 0 = sensor reading unavailable (do not treat as wet).
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
    - Historical errors do NOT indicate a current hardware problem and do NOT block
      printing. Only actively faulted errors (is_critical=True or severity≠"Historical")
      require attention before submitting a new job.
    - gcode_state="FAILED" combined with only historical HMS errors means the last
      job failed but the printer is idle and healthy — ready for a new print.
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

    Also includes elapsed time in minutes, the current stage name, gcode state, and
    skipped_objects (list of identify_id integers for objects skipped via skip_objects()).

    Field semantics:
    - gcode_state: string — "IDLE", "PREPARE", "RUNNING", "PAUSE", "FINISH",
      "FAILED", "SLICING", "INIT".
      IMPORTANT: "FAILED" means the *last* job failed — the printer is now idle and
      ready to accept a new print. It does NOT mean the printer is currently broken
      or blocked. Do NOT treat FAILED gcode_state as a reason to withhold a new job.
    - stage: integer stage code — see get_job_info() for the full table (0=idle,
      255=printing normally, 17=paused by user, etc.).
    - skipped_objects: list of identify_id integers skipped in the current print job.
      Empty list when no objects have been skipped or no print is active.
    """
    log.debug("get_print_progress: called for printer=%s", name)
    state = session_manager.get_state(name)
    job = session_manager.get_job(name)
    printer = session_manager.get_printer(name)
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
        "skipped_objects": printer._skipped_objects if printer else [],
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

    Response may be gzip+base64 compressed if the payload is large. Decompress:
      import gzip, json, base64
      data = json.loads(gzip.decompress(base64.b64decode(r["data"])))
    No HTTP fallback route exists for this tool. If the response exceeds the MCP
    limit, use get_monitoring_series(name, field) to fetch individual fields instead.
    """
    log.debug("get_monitoring_data: called for printer=%s", name)
    from tools._response import compress_if_large
    data = data_collector.get_all_data(name)
    log.debug("get_monitoring_data: data present=%s for %s", data is not None, name)
    if data is None:
        log.warning("get_monitoring_data: printer %s not connected", name)
        return _no_printer(name)
    log.debug("get_monitoring_data: returning result for %s", name)
    return compress_if_large(data)
