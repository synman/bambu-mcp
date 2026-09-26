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
    """Return the full live BambuState for the named printer as a dict.

    WHEN to use: you need many state fields at once (extruders, AMS units, spools, climate,
    HMS errors, print progress) and one bundled response is cheaper than several calls.

    Sibling disambiguation: ``get_printer_state`` bundles all printer state into one large
    response. For routine queries prefer the targeted tools, which are smaller and faster:
    ``get_temperatures`` (nozzle, bed, and chamber temperatures), ``get_spool_info`` (active
    spool and all AMS spools), ``get_job_info`` (current print job details),
    ``get_nozzle_info`` (nozzle diameter, type, and tray state), ``get_print_progress``
    (print percentage, layer, and time remaining), ``get_ams_units`` (AMS unit and slot
    details), ``get_hms_errors`` (active and historical HMS errors), ``get_fan_speeds`` (all
    fan speeds as percentages), and ``get_climate`` (temperatures and chamber door state).

    Args:
        name: Configured printer name (see ``get_configured_printers``).

    Returns:
        The serialized BambuState dict, with enum fields as their names, ``hms_errors`` passed
        through the active/historical rule, ``extension_tool.mounted`` (bool, whether the
        enhanced cooling fan is mounted) added, and ``recent_update`` (bool) added. Any payload
        over 300 characters is returned as a gzip+base64 envelope instead:
        ``{"compressed": True, "encoding": "gzip+base64", "original_size_bytes": int,
        "compressed_size_bytes": int, "data": str}``. Error shape: ``{"error": "Printer '<name>'
        not connected"}``.

    Notes:
        recent_update: bool. True when BPM's watchdog considers the MQTT report stream live;
        False when the watchdog found no message for watchdog_timeout seconds (or none yet),
        asked the printer to re-announce its version/push info, and is waiting for that reply.
        Ordinary report messages only reset the watchdog's staleness clock while this is True;
        it flips back to True only when a fresh info/module reply arrives. An idle
        (non-printing) printer with a healthy report stream still reads recent_update=True.
        This field flags a stalled telemetry connection, not printer activity.

        Decompress an envelope with:
          import gzip, json, base64
          data = json.loads(gzip.decompress(base64.b64decode(r["data"])))
        If the compressed envelope itself exceeds the MCP response limit, fall back to
        GET /api/printer?printer=<name>. That route returns a DIFFERENT, larger document (a
        serialization of the whole BambuPrinter object, not this BambuState dict) and answers
        HTTP 304 ("no data yet") while recent_update is False.

        Incidental side effect: building the response records its size in
        ~/.bambu-mcp/response_size_tracker.json when it sets a new high-water mark, and may
        then rewrite MAX_MCP_OUTPUT_TOKENS in ~/.copilot/mcp-config.json. It does not touch
        the printer.
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
    # _serialize() already emits state.extension_tool (tool_type and
    # mount_state as enum names, calibration_raw, type_raw) in the same shape
    # as every other enum in this payload; add only the derived flag.
    result.setdefault("extension_tool", {})["mounted"] = (
        state.extension_tool.is_enhanced_cooling_fan_mounted
    )
    printer = session_manager.get_printer(name)
    result["recent_update"] = printer.recent_update if printer else False
    log.debug("get_printer_state: hms_errors count=%d for %s", len(result.get("hms_errors", [])), name)
    log.debug("get_printer_state: returning result for %s", name)
    return compress_if_large(result)


def get_job_info(name: str) -> dict:
    """Return the ActiveJobInfo for the current (or last) print job as a dict.

    WHEN to use: you need the job's identity and detail (subtask name, gcode file, plate,
    stage code, layer counts, elapsed/remaining minutes), for example to locate its project
    file or to decode why a job is paused.

    Sibling disambiguation: ``get_job_info`` returns the full ActiveJobInfo record, including
    the job's identity fields. ``get_print_progress`` returns a compact progress summary and is
    the one that carries ``gcode_state``. ``get_printer_state`` bundles every state field in
    one large response.

    Args:
        name: Configured printer name (see ``get_configured_printers``).

    Returns:
        The whole serialized ActiveJobInfo dict, uncompressed: subtask_name, gcode_file,
        plate_num (-1 when unknown), plate_type, stage_id, stage_name, current_layer,
        total_layers, print_percentage, elapsed_minutes, remaining_minutes, wall_start_time,
        print_type, project_file_command, project_info_fetch_attempted, and project_info. While
        a job runs, project_info.metadata carries ``thumbnail`` and ``topimg`` as full base64
        PNG data URIs, so the payload can be very large; for project data prefer
        ``get_current_job_project_info(include_images=False)``. Error shape:
        ``{"error": "Printer '<name>' not connected"}``.

    Notes:
        Field semantics:
        - stage_id: integer stage code. ``stage_name`` is the decoded label (bpm parseStage);
            read it rather than decoding the code yourself. 0 and -1 decode to "";
            1=Auto bed leveling, 2=Heatbed preheating, 3=Sweeping XY mech mode,
            4=Changing filament, 5=M400 pause, 6=Filament runout pause, 7=Heating hotend,
            8=Calibrating extrusion, 9=Scanning bed surface, 10=Inspecting first layer,
            11=Identifying build plate, 12=Calibrating Micro Lidar, 13=Homing toolhead,
            14=Cleaning nozzle tip, 15=Temp check, 16=Paused by user,
            17=Front cover falling, 18=Lidar calibration (alt), 19=Calibrating flow,
            20=Nozzle temp malfunction, 21=Bed temp malfunction, 22=Filament unloading,
            23=Skip step pause, 24=Filament loading; 25-58 and 70-77 name further
            calibration, check, pause and AMS filament-change steps; 100=Printing;
            255=Completed. An unlisted code decodes as "Stage [N]".

        Empty result interpretation:
        - Every field is empty or zeroed (subtask_name="", gcode_file="",
          print_percentage=0, stage_id=0; a -1 stage_id, when it appears, is the printer's
          own stg_cur) until this session's first status report. A just-connected,
          restarted or re-created session therefore reads empty even if the printer has run
          jobs; "no job since the printer's last power cycle" is one possible explanation,
          not a guarantee.

        gcode_state is NOT a field of ActiveJobInfo and is not returned by this
        tool. Read gcode_state from get_print_progress() or get_printer_state() instead.

        Shortcuts for agent efficiency:
        - Current plate number: parse gcode_file path — pattern is /data/Metadata/plate_N.gcode
          where N is the plate number. Example: "/data/Metadata/plate_3.gcode" → plate 3.
          No extra tool call needed.
        - Find the project file on the SD card: use get_3mf_entry_by_name(name, subtask_name
          + ".gcode.3mf") to look it up by filename instead of scanning list_sdcard_files().
          That call runs a LIVE FTPS listing of the SD card (it contacts the printer; it
          does not read a cache). On a miss, try subtask_name + ".3mf", the form bpm falls
          back to.

        Values are the last telemetry received; they go stale, with no error, while the MQTT
        session is paused (pause_mqtt_session) or the connection has dropped.
    """
    log.debug("get_job_info: called for printer=%s", name)
    job = session_manager.get_job(name)
    if job is None:
        log.warning("get_job_info: printer %s not connected", name)
        return _no_printer(name)
    log.debug("get_job_info: returning result for %s", name)
    return _serialize(job)


def get_temperatures(name: str) -> dict:
    """Return current and target temperatures for all nozzles, the bed, and chamber.

    WHEN to use: check whether the nozzle(s), bed, or chamber have reached their target
    temperatures, for example before starting a print or while it heats.

    Sibling disambiguation: ``get_temperatures`` returns temperatures only, in a fixed
    nozzles/bed/chamber shape. ``get_climate`` returns temperatures and chamber door state.
    ``get_fan_speeds`` returns fan percentages. ``get_printer_state`` bundles everything in one
    large response.

    Args:
        name: Configured printer name (see ``get_configured_printers``).

    Returns:
        ``{"nozzles": [{"id", "temp", "target"}], "bed": {"temp", "target"},
        "chamber": {"temp", "target"}}``. For single-extruder printers the nozzles list has one
        entry; for dual-extruder (H2D) printers it has two. Error shape:
        ``{"error": "Printer '<name>' not connected"}``.

    Notes:
        Values are the last telemetry this server received, not a fresh read. They go stale,
        with no error returned, while the MQTT session is paused (``pause_mqtt_session``) or
        the connection has dropped. Check ``get_session_status`` /
        ``get_printer_connection_status``, or ``recent_update`` in ``get_printer_state``, when
        freshness matters.
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
    """Return the current fan speeds as percentages for all fans on the printer.

    WHEN to use: check the part-cooling, aux, exhaust, or heatbreak fan speed. For the
    enhanced-cooling fan this reports the last value commanded through this server's current
    session, not a measured run state.

    Sibling disambiguation: ``get_fan_speeds`` returns fan percentages only. ``get_climate``
    returns temperatures and chamber door state, and ``get_temperatures`` returns temperatures
    only. ``set_fan_speed`` is the tool that changes a fan.

    Args:
        name: Configured printer name (see ``get_configured_printers``).

    Returns:
        ``{"part_cooling_pct", "aux_pct", "exhaust_pct", "heatbreak_pct",
        "enhanced_cooling_pct"}``, each a percentage. Fans reported: part_cooling, aux
        (recirculation), exhaust (chamber), heatbreak, enhanced_cooling (Toolhead Enhanced
        Cooling Fan, H2-series extension-tool only). Error shape:
        ``{"error": "Printer '<name>' not connected"}``.

    Notes:
        enhanced_cooling_pct is NOT a measured speed — the printer publishes no run-state
        telemetry for this fan. It is the last target commanded through THIS server's
        current session (sticky): it persists unchanged across telemetry updates, reads 0
        after a session start or restart and on printers with no extension-tool module, is
        zeroed when the extension tool leaves the MOUNTED state, and never reflects a command
        sent by another client. The other fan values are the last telemetry received and go
        stale, with no error, while the MQTT session is paused or the connection has dropped.
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
        "enhanced_cooling_pct": c.enhanced_cooling_fan_target_percent,
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
    """Return the active spool and a list of all spools associated with the printer.

    WHEN to use: find out which filament is loaded and in use, or list every spool with its
    type, color, remaining percentage, nozzle temperature range, and drying parameters.

    Sibling disambiguation: ``get_spool_info`` is filament-centric (one dict per spool, plus
    the active one). ``get_ams_units`` and ``get_ams_status`` return the same unit payload
    (temperature, humidity, heater and drying state, tray-existence flags), not filament.
    ``get_external_spool`` returns the external holder trays alone: 254, and also 255 on a
    dual-nozzle printer.

    Args:
        name: Configured printer name (see ``get_configured_printers``).

    Returns:
        ``{"active_spool": dict | None, "spools": [dict]}``. The active spool is the one in the
        active AMS unit (ams_id equal to the state's active_ams_id) whose slot_id or ``id`` equals
        the state's active_tray_id; the id is tried so a single-extruder printer's absolute tray
        id reaches units after the first, and the external holders 254 and 255 are matched by
        slot_id alone. It is None when nothing matches or no tray is active. The list has an
        entry for every AMS slot the printer reports, empty ones included, plus the external
        holder entries, so its length is not the number of physical spools; an entry with an empty ``type`` holds no filament. Per-spool keys:
        ``id`` (0-23 for AMS trays, 254/255 for external holders), ``slot_id`` (slot within
        the unit, or the holder id; -1 on a placeholder), ``ams_id`` (firmware unit id; -1 for
        external), name, type, sub_brands, color, tray_info_idx, k, bed_temp,
        nozzle_temp_min/max, drying_temp, drying_time, remaining_percent, state, total_length,
        tray_weight, plus the added color_name and display_name. Error shape:
        ``{"error": "Printer '<name>' not connected"}``.

    Notes:
        Field semantics:
        - active_ams_id, active_tray_id: BambuState values used to select active_spool; this
          tool does not return them (read them from ``get_printer_state``).
        - active_ams_id: internal chip_id of the AMS unit. 0 = first AMS unit (AMS 2 Pro);
          128 = AMS HT unit (Bambu's internal ID for AMS HT). NOT the same as the
          0-based unit_id used by get_ams_units() / load_filament().
        - active_tray_id: -1 when no tray is active. On a single-extruder printer it is the
          printer's raw tray_now value (255 mapped to -1), an absolute tray id: AMS unit n slot
          s is 4n+s. On a dual-extruder printer it is the low byte of the active extruder's
          report, the slot inside the active unit: the debug log shows the H2D's AMS HT as
          32768 (unit 128, slot 0), which reads as active_ams_id 128 with active_tray_id 0.
          That is why a spool is chosen by unit first (see Returns). 254 and 255 are the
          external spool holders (254 = the only holder on a single-nozzle printer, the LEFT
          holder on a dual-nozzle one; 255 = the RIGHT holder).
        - Verification limit: the log shows tray_now only as 0, 1 and 255, so no absolute id
          above 3 (a second AMS unit, or an AMS HT as spool id 16) has been observed on a
          single-extruder printer; that path follows bpm's code and the spool numbering and
          is UNVERIFIED on hardware (loading filament to see one would be a printer write).
        - Each spool dict: type (str), remaining_percent (0–100, or -1 when the tray reports
          no 'remain' value; always -1 for a reported external holder, 0 on the placeholder
          for an absent one), nozzle_temp_min/max (°C), drying_temp (°C), drying_time (hours).
        - color: a CSS3 colour NAME when the spool's RGB matches one exactly (e.g. "red",
          "black", "white"), otherwise an 8-digit "#RRGGBBAA" string that includes alpha. It
          is not always a hex string, so handle both forms. An empty external holder reports
          tray_color "00000000", which resolves to "black".
        - name (if present): Bambu Lab vendor-specific brand label (e.g. "Bambu PLA Basic").
          Not present on third-party spools and not a reliable identifier. The true identity
          of a spool is color + tray_info_idx (base profile catalog code, e.g. "GFA00").
          When name is absent, the vendor name can be derived from tray_info_idx:
          GFA00="Bambu PLA Basic", GFA01="Bambu PLA Matte", GFB00="Bambu ABS",
          GFB01="Bambu ASA".
        - display_name: synthesized human-readable label always present in each spool dict.
          Rule: "{catalog or type} ({color_name})".
        - color_name: nearest CSS3 color name for the spool color (e.g. "darkorange").
          Derived from the color field with the alpha channel stripped; when color is already
          a name, color_name equals it. Use color_name for human-readable descriptions.

        Values are the last telemetry received; they go stale, with no error, while the MQTT
        session is paused (pause_mqtt_session) or the connection has dropped.
    """
    log.debug("get_spool_info: called for printer=%s", name)
    state = session_manager.get_state(name)
    if state is None:
        log.warning("get_spool_info: printer %s not connected", name)
        return _no_printer(name)
    all_spools = [_enrich_spool(_serialize(s)) for s in (state.spools or [])]
    tray = state.active_tray_id
    # A spool is active when it sits in the active unit (ams_id == active_ams_id) and the tray
    # names it either way bpm reports it: as the slot inside that unit (dual-extruder printers
    # report slot & 0xFF, so the H2D AMS HT reads ams 128 tray 0) or as the absolute tray id
    # (single-extruder printers report the raw tray_now, so unit n slot s reads 4n+s). Matching
    # the id on its own, or before the unit, would pick AMS unit 0 slot 0 (id 0) for the H2D AMS
    # HT. 254/255 (external holders) keep the slot rule alone. Tray -1 means no tray is active, so
    # nothing is searched: the empty external placeholder bpm appends (slot_id -1, ams_id -1)
    # would otherwise match a -1/-1 state and come back as the active spool.
    by_id = tray not in (-1, 254, 255)
    active = None if tray == -1 else next(
        (s for s in (state.spools or [])
         if s.ams_id == state.active_ams_id and (s.slot_id == tray or (by_id and s.id == tray))),
        None,
    )
    if active is not None:
        active = _enrich_spool(_serialize(active))
    log.debug("get_spool_info: returning result for %s", name)
    return {"active_spool": active, "spools": all_spools}


def get_ams_status(name: str) -> dict:
    """Return the status of all AMS units.

    WHEN to use: check AMS health, such as humidity, heater and drying state, or the global
    AMS status, and how many AMS units are connected.

    Sibling disambiguation: ``get_ams_status`` and ``get_ams_units`` return the identical
    ``{ams_status, ams_count, units}`` payload from the same printer state; they differ only
    in name, and ``get_ams_units`` carries the field reference for the units list. A unit
    reports slot presence (``tray_exists``, one boolean per slot: four, or one on an AMS HT)
    and its dryer (``dryer``, null on a unit that cannot dry), not per-slot filament: use
    ``get_spool_info`` for filament type, color, and remaining percentage.

    Args:
        name: Configured printer name (see ``get_configured_printers``).

    Returns:
        ``{"ams_status": str, "ams_count": int, "units": [dict]}``. Each unit includes
        temperature, humidity, heater state, drying state, and tray-existence flags; ams_status
        is the global AMS status string and ams_count the number of connected AMS units. Error
        shape: ``{"error": "Printer '<name>' not connected"}``.

    Notes:
        humidity_index scale: 1=WET (alert, filament needs drying), 5=DRY (good, no
        action needed). Higher numbers mean DRIER — the scale is counterintuitive.
        Only values of 1 or 2 indicate a moisture problem. Value 5 = completely dry.
        Value 0 = sensor reading unavailable (do not treat as wet).

        Values are the last telemetry received; they go stale, with no error, while the MQTT
        session is paused (pause_mqtt_session) or the connection has dropped.
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
    """Return the printer's HMS (Health Management System) errors, labelled active or Historical.

    WHEN to use: decide whether the printer has a live hardware fault before submitting a
    job, or explain a failed or paused print.

    Sibling disambiguation: ``get_hms_errors`` returns only the HMS error list and the raw
    print_error code, with the active/historical rule already applied. ``get_printer_state``
    also carries the same ``hms_errors`` inside its full payload. ``get_pending_alerts``
    returns pending state-change alerts rather than the current error list.

    Args:
        name: Configured printer name (see ``get_configured_printers``).

    Returns:
        ``{"hms_errors": [dict], "print_error": int}``. Each hms_errors entry is
        ``{"code": str (e.g. "HMS_0300-0400-0002-000C"), "msg": str (human-readable
        description), "module": str, "severity": str, "is_critical": bool, "type":
        "device_hms" | "device_error", "url": str}``; the code is a string, not a number. The
        list holds both the active entry and the Historical-labelled ones (see Notes), and is
        empty only when the printer reports no HMS entries and print_error is 0. Filter on
        ``severity != "Historical"`` (or ``is_critical``) to see only the active fault.
        print_error is the printer's numeric print error code (0 when none). Error shape:
        ``{"error": "Printer '<name>' not connected"}``.

    Notes:
        Active vs. historical rule (applied by THIS SERVER, not reported by the printer):
        - A `device_error` entry exists only when print_error != 0, and is listed first.
        - If a `device_error` is present, the FIRST `device_hms` entry is returned as
          reported and every later `device_hms` entry is relabelled severity="Historical",
          is_critical=False. If none is present, EVERY `device_hms` entry is relabelled
          Historical. Codes are never compared, and `device_error` entries are never relabelled.
        - "Historical" is a positional label, not a printer-reported cleared state. With
          print_error == 0, every entry the printer sends is relabelled Historical. Do not read
          it as proof the hardware is healthy: check print_error and the non-Historical
          entries, and consider ``clear_print_error`` before submitting a new job.
        - gcode_state="FAILED" means the last job failed; it says nothing about whether the
          printer will accept a new one.
        - `device_hms` codes follow HMS_XXXX-XXXX-XXXX-XXXX. The first segment carries the
          module byte (0x03=Mainboard, 0x05/0x12=AMS, 0x07=Toolhead, 0x0B=Webcam, 0x10=HMS) in
          its high byte and the severity mask in its low byte; the other segments are
          module-specific identifiers. Entries derived from print_error (type "device_error")
          have two segments: HMS_XXXX-XXXX.
        - Values are the last telemetry received; they go stale, with no error, while the MQTT
          session is paused (pause_mqtt_session) or the connection has dropped.
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
    """Return print progress: percentage complete, current/total layers, and time remaining.

    WHEN to use: poll how far along a print is and whether the printer is idle, running,
    paused, or finished, without pulling the whole job record.

    Sibling disambiguation: ``get_print_progress`` is the compact progress summary and the one
    that returns ``gcode_state``. ``get_job_info`` returns the full ActiveJobInfo (gcode file,
    plate number, stage_id) but no gcode_state. ``get_printer_state`` bundles all state in one
    large response.

    Args:
        name: Configured printer name (see ``get_configured_printers``).

    Returns:
        ``{"gcode_state", "print_percentage", "current_layer", "total_layers",
        "elapsed_minutes", "remaining_minutes", "stage_name", "subtask_name",
        "skipped_objects"}``. Elapsed and remaining time are in minutes. When the printer is
        connected but has no job record, the job-derived fields read 0 (or "" for stage_name and
        subtask_name). Error shape: ``{"error": "Printer '<name>' not connected"}``.

    Notes:
        Field semantics:
        - gcode_state: string — "IDLE", "PREPARE", "RUNNING", "PAUSE", "FINISH",
          "FAILED", "SLICING", "INIT".
          "FAILED" means the *last* job ended in failure; it says nothing about whether the
          printer will accept a new one. Check get_hms_errors() (print_error and non-Historical
          entries) and, if a fault error is lingering, clear_print_error(), before submitting
          a job.
        - stage: this tool returns the decoded stage as the string ``stage_name``, not as a
          code. See get_job_info() for the code table (100=Printing, 255=Completed).
        - skipped_objects: list of identify_id integers skipped in the current print job
          (objects skipped via skip_objects()). Empty list when no objects have been
          skipped or no print is active.

        Values are the last telemetry received; they go stale, with no error, while the MQTT
        session is paused (pause_mqtt_session) or the connection has dropped.
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
    """Return the hardware capabilities dict for the printer.

    WHEN to use: check what the printer supports (AMS, dual extruder, camera, chamber
    temperature control, detector and auto-recovery support) before choosing a tool or option.

    Sibling disambiguation: ``get_capabilities`` returns the feature flags discovered for this
    printer. ``get_printer_info`` returns the model, serial number, and firmware version, and
    ``get_detector_settings`` returns the current detector settings rather than what is
    supported.

    Args:
        name: Configured printer name (see ``get_configured_printers``).

    Returns:
        The serialized capabilities dict of boolean flags, for example has_ams,
        has_dual_extruder, has_camera, has_lidar, has_air_filtration, has_chamber_temp, and the
        has_*_support flags. Capabilities are discovered during the initial MQTT handshake and
        telemetry analysis. Error shape: ``{"error": "Printer '<name>' not connected"}``.
    """
    log.debug("get_capabilities: called for printer=%s", name)
    config = session_manager.get_config(name)
    if config is None:
        log.warning("get_capabilities: printer %s not connected", name)
        return _no_printer(name)
    log.debug("get_capabilities: returning result for %s", name)
    return _serialize(config.capabilities)


def get_printer_info(name: str) -> dict:
    """Return the printer model, serial number, and firmware version.

    WHEN to use: identify which printer this is (model and serial) and which firmware it runs.

    Sibling disambiguation: ``get_printer_info`` returns identity plus firmware in one call.
    ``get_firmware_version`` returns the firmware versions alone, and ``get_capabilities``
    returns what the hardware supports rather than which unit it is.

    Args:
        name: Configured printer name (see ``get_configured_printers``).

    Returns:
        ``{"model": str, "serial": str, "firmware_version": str, "ams_firmware_version": str}``.
        model is the printer model enum name, or "UNKNOWN" when the model is not set.
        firmware_version and ams_firmware_version are strings and read "" until the printer's
        version/module handshake reply arrives; ams_firmware_version stays "" when no module
        reports an AMS version. Neither is ever None. Error shape:
        ``{"error": "Printer '<name>' not connected"}``.
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
    """Return the Wi-Fi signal strength for the printer in dBm.

    WHEN to use: diagnose flaky telemetry or dropped MQTT messages by checking how strong the
    printer's Wi-Fi link is.

    Sibling disambiguation: ``get_wifi_signal`` returns only the signal strength. The same
    value is the ``wifi_signal_strength`` field of ``get_printer_state``.
    ``get_session_status`` and ``get_printer_connection_status`` report the MQTT session and
    connection state, not radio signal.

    Args:
        name: Configured printer name (see ``get_configured_printers``).

    Returns:
        ``{"wifi_signal": str}``, the signal strength as the printer reports it. A stronger
        (less negative) value indicates a better signal. Error shape:
        ``{"error": "Printer '<name>' not connected"}``.
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
