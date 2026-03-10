"""
bambu-mcp-api — HTTP REST API server for the bambu-mcp MCP service.

Exposes 67 routes mirroring the bambu-printer-app container API so existing clients
work unchanged. Backed directly by session_manager / BambuPrinter — no dependency
on the bambu-printer-app container.

Service details:
  Port:       Dynamically allocated from the shared ephemeral port pool (IANA RFC 6335
              range 49152–65535).  If BAMBU_API_PORT is set it is used as a preferred-port
              hint (tried first; rotates to next available pool port if taken).
  Base URL:   http://localhost:{port}/api  — call get_server_info() or GET /api/server_info
              to discover the actual port at runtime.
  Auth:       HTTP Basic — credentials from BAMBU_API_USER / BAMBU_API_PASS env vars.
  Swagger UI: http://localhost:{port}/api/docs
  OpenAPI:    http://localhost:{port}/api/openapi.json

Lifecycle:
  Auto-started by server.py _startup() when the MCP server initialises.
  start(port)  — launch in a background non-daemon thread; returns the bound port.
  stop()       — shut down the wsgiref server and release the thread.
  is_running() — True when the server thread is alive.
  get_url()    — returns "http://localhost:{port}".
  get_port()   — returns the currently bound port integer (0 if not running).

Route categories (67 routes total):
  Printer state  (6)  — full state, progress, temperatures, spools, nozzle, AMS
  Print control  (8)  — print 3mf, pause, resume, stop, speed, skip objects, options
  AMS/filament   (7)  — load, unload, set filament, dryer start/stop, RFID calibrate
  Climate        (7)  — bed/nozzle/chamber temp, fan speeds, chamber light
  Hardware       (8)  — nozzle config, refresh nozzles, 5 AI detector routes, swap tool
  File mgmt     (12)  — list, upload, download, delete, rename, mkdir, project info, print
  System         (6)  — health, session CRUD, printer discovery, OpenAPI docs

Agent reference: call get_knowledge_topic('http_api') for the full route inventory.
"""

from __future__ import annotations

import inspect
import logging
import os
import re
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from http import HTTPStatus
from io import BytesIO

log = logging.getLogger(__name__)

# The MCP stdio transport exits when stdin closes (nohup / Claude Desktop shutdown).
# Python would then run logging.shutdown() via atexit, closing the file handler while
# the api_server non-daemon thread is still serving requests.  Unregistering it keeps
# all handlers open for the lifetime of the server thread.  The process is killed by
# the operator (SIGTERM/SIGKILL) so handler cleanup on exit is not needed.
import atexit as _atexit
_atexit.unregister(logging.shutdown)

# ── lazy imports (avoid import-time BPM cost) ─────────────────────────────────
_flask_app = None
_server_thread: threading.Thread | None = None
_werkzeug_server = None  # holds make_server instance for clean shutdown
_port: int = 0           # set by start(); 0 = not running
_UPLOADS = os.path.join(os.path.dirname(__file__), "uploads")

DEFAULT_PRINTER = os.environ.get("BAMBU_API_PRINTER", "")


# ── OpenAPI helpers (lifted from bambu-printer-app/api/openapi_local.py) ──────

def _infer_param_type(source: str, name: str) -> str:
    log.debug("_infer_param_type: name=%s", name)
    if re.search(rf"int\(\s*(?:request\.args|_rargs\(\))\.get\(\s*['\"]{ re.escape(name)}['\"]", source):
        return "integer"
    if re.search(rf"float\(\s*(?:request\.args|_rargs\(\))\.get\(\s*['\"]{ re.escape(name)}['\"]", source):
        return "number"
    if re.search(rf"(?:request\.args|_rargs\(\))\.get\(\s*['\"]{ re.escape(name)}['\"].*\)\s*==\s*['\"]true['\"]", source):
        return "boolean"
    return "string"


def _bpm_enum_names(enum_cls_path: str, exclude: list | None = None) -> list[str]:
    """Lazily import a bpm enum and return lowercase member names, excluding any in exclude."""
    try:
        import importlib
        module_path, cls_name = enum_cls_path.rsplit(".", 1)
        cls = getattr(importlib.import_module(module_path), cls_name)
        return [e.name.lower() for e in cls if e.name not in (exclude or [])]
    except Exception:
        return []


def _bpm_enum_values(enum_cls_path: str, exclude: list | None = None) -> list:
    """Lazily import a bpm enum and return member values, excluding any in exclude."""
    try:
        import importlib
        module_path, cls_name = enum_cls_path.rsplit(".", 1)
        cls = getattr(importlib.import_module(module_path), cls_name)
        return [e.value for e in cls if e.value not in (exclude or [])]
    except Exception:
        return []


# Fixed-value enum lists keyed by (endpoint_name, param_name).
# BPM-backed entries use _bpm_enum_names/_bpm_enum_values so new enum members
# are picked up automatically. Hardcoded lists are used only where no BPM enum exists.
_ROUTE_ENUM_VALUES: dict[tuple[str, str], list] = {
    # ── Print control ──────────────────────────────────────────────────────────
    ("set_speed_level",              "level"):         _bpm_enum_names("bpm.bambutools.SpeedLevel"),
    ("print_3mf",                    "plate"):         _bpm_enum_names("bpm.bambutools.PlateType", exclude=["NONE"]),
    # ── Climate ────────────────────────────────────────────────────────────────
    ("set_light_state",              "state"):         ["on", "off"],
    # ── AI detectors ───────────────────────────────────────────────────────────
    ("set_spaghetti_detector",       "sensitivity"):   _bpm_enum_names("bpm.bambutools.DetectorSensitivity"),
    ("set_purgechutepileup_detector","sensitivity"):   _bpm_enum_names("bpm.bambutools.DetectorSensitivity"),
    ("set_nozzleclumping_detector",  "sensitivity"):   _bpm_enum_names("bpm.bambutools.DetectorSensitivity"),
    ("set_airprinting_detector",     "sensitivity"):   _bpm_enum_names("bpm.bambutools.DetectorSensitivity"),
    # ── Print options / AMS control ────────────────────────────────────────────
    ("set_print_option",             "option"):        _bpm_enum_names("bpm.bambutools.PrintOption"),
    ("send_ams_control_command",     "cmd"):           _bpm_enum_names("bpm.bambutools.AMSControlCommand"),
    ("set_ams_user_setting",         "setting"):       _bpm_enum_names("bpm.bambutools.AMSUserSetting"),
    # ── Hardware / nozzle ──────────────────────────────────────────────────────
    ("set_nozzle_details",           "nozzle_type"):   _bpm_enum_names("bpm.bambutools.NozzleType", exclude=["UNKNOWN"]),
    ("set_nozzle_details",           "nozzle_diameter"): _bpm_enum_values("bpm.bambutools.NozzleDiameter", exclude=[0.0]),
}


_PRINTER_DESC = "Printer name. Omit to use the default printer. Required. Use GET /api/default_printer to resolve the current default."

_ROUTE_PARAM_DESCRIPTIONS: dict[tuple[str, str], str] = {
    # ── Shared ─────────────────────────────────────────────────────────────────
    ("set_tool_target_temp",            "printer"):     _PRINTER_DESC,
    ("set_bed_target_temp",             "printer"):     _PRINTER_DESC,
    ("set_chamber_target_temp",         "printer"):     _PRINTER_DESC,
    ("set_aux_fan_speed_target",        "printer"):     _PRINTER_DESC,
    ("set_exhaust_fan_speed_target",    "printer"):     _PRINTER_DESC,
    ("set_fan_speed_target",            "printer"):     _PRINTER_DESC,
    ("set_light_state",                 "printer"):     _PRINTER_DESC,
    ("set_speed_level",                 "printer"):     _PRINTER_DESC,
    ("unload_filament",                 "printer"):     _PRINTER_DESC,
    ("load_filament",                   "printer"):     _PRINTER_DESC,
    ("refresh_spool_rfid",              "printer"):     _PRINTER_DESC,
    ("set_spool_details",               "printer"):     _PRINTER_DESC,
    ("resume_printing",                 "printer"):     _PRINTER_DESC,
    ("pause_printing",                  "printer"):     _PRINTER_DESC,
    ("stop_printing",                   "printer"):     _PRINTER_DESC,
    ("print_3mf",                       "printer"):     _PRINTER_DESC,
    ("skip_objects",                    "printer"):     _PRINTER_DESC,
    ("refresh_sdcard_3mf_files",        "printer"):     _PRINTER_DESC,
    ("get_sdcard_3mf_files",            "printer"):     _PRINTER_DESC,
    ("refresh_sdcard_contents",         "printer"):     _PRINTER_DESC,
    ("get_sdcard_contents",             "printer"):     _PRINTER_DESC,
    ("delete_sdcard_file",              "printer"):     _PRINTER_DESC,
    ("make_sdcard_directory",           "printer"):     _PRINTER_DESC,
    ("rename_sdcard_file",              "printer"):     _PRINTER_DESC,
    ("upload_file_to_printer",          "printer"):     _PRINTER_DESC,
    ("download_file_from_printer",      "printer"):     _PRINTER_DESC,
    ("set_buildplate_marker_detector",  "printer"):     _PRINTER_DESC,
    ("set_first_layer_inspection",      "printer"):     _PRINTER_DESC,
    ("set_spaghetti_detector",          "printer"):     _PRINTER_DESC,
    ("set_purgechutepileup_detector",   "printer"):     _PRINTER_DESC,
    ("set_nozzleclumping_detector",     "printer"):     _PRINTER_DESC,
    ("set_airprinting_detector",        "printer"):     _PRINTER_DESC,
    ("set_print_option",                "printer"):     _PRINTER_DESC,
    ("send_gcode",                      "printer"):     _PRINTER_DESC,
    ("send_ams_control_command",        "printer"):     _PRINTER_DESC,
    ("send_mqtt_command",               "printer"):     _PRINTER_DESC,
    ("set_ams_user_setting",            "printer"):     _PRINTER_DESC,
    ("set_nozzle_details",              "printer"):     _PRINTER_DESC,
    ("refresh_nozzles",                 "printer"):     _PRINTER_DESC,
    ("get_3mf_props_for_file",          "printer"):     _PRINTER_DESC,
    ("get_current_3mf_props",           "printer"):     _PRINTER_DESC,
    ("trigger_printer_refresh",         "printer"):     _PRINTER_DESC,
    ("toggle_active_tool",              "printer"):     _PRINTER_DESC,
    ("toggle_session",                  "printer"):     _PRINTER_DESC,
    ("get_printer_info",                "printer"):     _PRINTER_DESC,
    ("rename_printer",                  "printer"):     _PRINTER_DESC,
    ("clear_print_error",              "printer"):     _PRINTER_DESC,
    ("select_extrusion_calibration",   "printer"):     _PRINTER_DESC,
    ("turn_on_ams_dryer",              "printer"):     _PRINTER_DESC,
    ("turn_off_ams_dryer",             "printer"):     _PRINTER_DESC,
    ("get_detector_settings",          "printer"):     _PRINTER_DESC,
    # ── Climate ────────────────────────────────────────────────────────────────
    ("set_tool_target_temp",            "temp"):        "Target nozzle temperature in °C. Use 0 to turn off.",
    ("set_bed_target_temp",             "temp"):        "Target bed temperature in °C. Use 0 to turn off.",
    ("set_chamber_target_temp",         "temp"):        "Target chamber temperature in °C. Use 0 to turn off.",
    ("set_aux_fan_speed_target",        "percent"):     "Aux fan speed 0–100%.",
    ("set_exhaust_fan_speed_target",    "percent"):     "Exhaust fan speed 0–100%.",
    ("set_fan_speed_target",            "percent"):     "Part cooling fan speed 0–100%.",
    ("set_light_state",                 "state"):       "Light state.",
    # ── Print control ──────────────────────────────────────────────────────────
    ("set_speed_level",                 "level"):       "Print speed profile.",
    ("load_filament",                   "slot"):        "AMS slot index (0–3).",
    ("refresh_spool_rfid",              "slot_id"):     "Slot index within the AMS unit (0–3).",
    ("refresh_spool_rfid",              "ams_id"):      "AMS unit index (0-based).",
    ("print_3mf",                       "filename"):    "Full SD card path to the .3mf file (e.g. /_jobs/myprint.gcode.3mf).",
    ("print_3mf",                       "platenum"):    "Plate number within the .3mf project (1-based).",
    ("print_3mf",                       "plate"):       "Build plate surface type.",
    ("print_3mf",                       "use_ams"):     "Use AMS filament mapping from the project file.",
    ("print_3mf",                       "ams_mapping"): "JSON array overriding AMS slot assignment (e.g. [1,-1,-1,-1]). Omit to use project defaults.",
    ("print_3mf",                       "bl"):          "Run bed leveling before printing.",
    ("print_3mf",                       "flow"):        "Run flow/extrusion calibration before printing.",
    ("print_3mf",                       "tl"):          "Record a timelapse of the print.",
    ("skip_objects",                    "objects"):     "Comma-separated list of object identify_id values to skip.",
    # ── Spool / AMS ────────────────────────────────────────────────────────────
    ("set_spool_details",               "tray_id"):       "Absolute tray ID: ams_unit_index × 4 + slot (0–3). External spool = 254.",
    ("set_spool_details",               "tray_info_idx"): "Bambu filament catalog ID (e.g. GFA00). Use 'no_filament' to clear.",
    ("set_spool_details",               "tray_id_name"):  "Filament brand/product name label.",
    ("set_spool_details",               "tray_type"):     "Filament type string (e.g. PLA, PETG, ABS).",
    ("set_spool_details",               "tray_color"):    "Filament color as RRGGBB hex (e.g. FF0000).",
    ("set_spool_details",               "nozzle_temp_min"): "Minimum nozzle temperature in °C. Pass -1 to keep existing.",
    ("set_spool_details",               "nozzle_temp_max"): "Maximum nozzle temperature in °C. Pass -1 to keep existing.",
    ("send_ams_control_command",        "cmd"):          "AMS control command.",
    ("set_ams_user_setting",            "setting"):      "AMS user setting to toggle.",
    ("set_ams_user_setting",            "enabled"):      "Enable or disable the setting.",
    # ── File management ────────────────────────────────────────────────────────
    ("delete_sdcard_file",              "file"):        "Full SD card path of the file to delete.",
    ("make_sdcard_directory",           "dir"):         "Full SD card path of the directory to create.",
    ("rename_sdcard_file",              "src"):         "Current full SD card path.",
    ("rename_sdcard_file",              "dest"):        "New full SD card path.",
    ("upload_file_to_printer",          "src"):         "Local filename as uploaded via multipart POST to the host.",
    ("upload_file_to_printer",          "dest"):        "Destination path on the printer SD card.",
    ("download_file_from_printer",      "src"):         "Full SD card path of the file to download.",
    ("get_3mf_props_for_file",          "file"):        "Full SD card path to the .3mf file.",
    ("get_3mf_props_for_file",          "plate"):       "Plate number within the .3mf project (1-based). Use 0 to return all plates.",
    # ── AI detectors ───────────────────────────────────────────────────────────
    ("set_buildplate_marker_detector",  "enabled"):     "Enable or disable the detector.",
    ("set_first_layer_inspection",      "enabled"):     "Enable or disable first-layer LiDAR/camera inspection.",
    ("set_spaghetti_detector",          "enabled"):     "Enable or disable the detector.",
    ("set_spaghetti_detector",          "sensitivity"): "Detection sensitivity.",
    ("set_purgechutepileup_detector",   "enabled"):     "Enable or disable the detector.",
    ("set_purgechutepileup_detector",   "sensitivity"): "Detection sensitivity.",
    ("set_nozzleclumping_detector",     "enabled"):     "Enable or disable the detector.",
    ("set_nozzleclumping_detector",     "sensitivity"): "Detection sensitivity.",
    ("set_airprinting_detector",        "enabled"):     "Enable or disable the detector.",
    ("set_airprinting_detector",        "sensitivity"): "Detection sensitivity.",
    # ── Print options ──────────────────────────────────────────────────────────
    ("set_print_option",                "option"):      "Print option to toggle.",
    ("set_print_option",                "enabled"):     "Enable or disable the option.",
    # ── G-code ─────────────────────────────────────────────────────────────────
    ("send_gcode",                      "gcode"):       "G-code commands to send. Use | as a newline separator for multiple commands.",
    # ── Clear print error ──────────────────────────────────────────────────────
    ("clear_print_error",              "print_error"): "Integer error code to clear. Pass 0 to clear any active error.",
    ("clear_print_error",              "subtask_id"):  "Subtask ID of the failed job (from get_job_info). Pass empty string if not known.",
    # ── Extrusion calibration ──────────────────────────────────────────────────
    ("select_extrusion_calibration",   "tray_id"):     "Absolute tray ID: ams_unit_index × 4 + slot (0–3). External spool = 254.",
    ("select_extrusion_calibration",   "cali_idx"):    "Calibration profile index to activate. Use -1 for automatic best-match.",
    # ── AMS dryer ─────────────────────────────────────────────────────────────
    ("turn_on_ams_dryer",              "ams_id"):      "Internal AMS unit ID (chip_id). AMS 2 Pro starts at 0; AMS HT starts at 128.",
    ("turn_on_ams_dryer",              "target_temp"): "Target drying temperature in °C (default 55).",
    ("turn_on_ams_dryer",              "duration_hours"): "Drying duration in hours (default 4).",
    ("turn_off_ams_dryer",             "ams_id"):      "Internal AMS unit ID (chip_id).",
    # ── Raw MQTT ───────────────────────────────────────────────────────────────
    ("send_mqtt_command",               "command_json"): "Valid JSON string matching the Bambu Lab MQTT command schema.",
    # ── Nozzle hardware ────────────────────────────────────────────────────────
    ("set_nozzle_details",              "nozzle_diameter"): "Nozzle diameter in mm.",
    ("set_nozzle_details",              "nozzle_type"):     "Nozzle material type.",
    # ── System ─────────────────────────────────────────────────────────────────
    ("rename_printer",                  "new_name"):        "New display name to set on the printer firmware (visible on touchscreen and in Bambu Studio).",
}


def _extract_query_params(view_func: Callable) -> list[dict]:
    log.debug("_extract_query_params: func=%s", view_func.__name__)
    try:
        source = inspect.getsource(view_func)
    except OSError:
        return []
    matches = re.findall(
        r"(?:request\.args|_rargs\(\))\.get\(\s*['\"]([^'\"]+)['\"](?:\s*,\s*([^\)]+))?\)",
        source,
    )
    seen: set[str] = set()
    params: list[dict] = []
    for name, default_expr in matches:
        if not name or name in seen:
            continue
        seen.add(name)
        params.append({
            "name": name,
            "in": "query",
            "required": default_expr.strip() == "",
            "schema": {"type": _infer_param_type(source, name)},
        })
    log.debug("_extract_query_params: → %d params", len(params))
    return params


_ROUTE_TAGS: dict[str, str] = {
    # System
    "health_check": "System",
    "list_printers": "System",
    "default_printer": "System",
    "get_printer_info": "System",
    "trigger_printer_refresh": "System",
    "toggle_session": "System",
    "dump_log": "System",
    "truncate_log": "System",
    "server_info": "System",
    "get_openapi_spec": "System",
    "api_docs": "System",
    # Climate
    "set_tool_target_temp": "Climate",
    "set_bed_target_temp": "Climate",
    "set_chamber_target_temp": "Climate",
    "set_aux_fan_speed_target": "Climate",
    "set_exhaust_fan_speed_target": "Climate",
    "set_fan_speed_target": "Climate",
    "set_light_state": "Climate",
    "toggle_active_tool": "Climate",
    # Print Control
    "resume_printing": "Print Control",
    "pause_printing": "Print Control",
    "stop_printing": "Print Control",
    "print_3mf": "Print Control",
    "skip_objects": "Print Control",
    "set_speed_level": "Print Control",
    "send_gcode": "Print Control",
    "send_mqtt_command": "Print Control",
    "clear_print_error": "Print Control",
    # AMS & Filament
    "load_filament": "AMS & Filament",
    "unload_filament": "AMS & Filament",
    "refresh_spool_rfid": "AMS & Filament",
    "set_spool_details": "AMS & Filament",
    "send_ams_control_command": "AMS & Filament",
    "set_ams_user_setting": "AMS & Filament",
    "set_spool_k_factor": "AMS & Filament",
    "select_extrusion_calibration": "AMS & Filament",
    "turn_on_ams_dryer": "AMS & Filament",
    "turn_off_ams_dryer": "AMS & Filament",
    # Hardware
    "set_nozzle_details": "Hardware",
    "refresh_nozzles": "Hardware",
    "set_buildplate_marker_detector": "Hardware",
    "set_first_layer_inspection": "Hardware",
    "set_spaghetti_detector": "Hardware",
    "set_purgechutepileup_detector": "Hardware",
    "set_nozzleclumping_detector": "Hardware",
    "set_airprinting_detector": "Hardware",
    "set_print_option": "Hardware",
    "get_detector_settings": "Hardware",
    "rename_printer": "System",
    # Files
    "refresh_sdcard_3mf_files": "Files",
    "get_sdcard_3mf_files": "Files",
    "refresh_sdcard_contents": "Files",
    "get_sdcard_contents": "Files",
    "delete_sdcard_file": "Files",
    "make_sdcard_directory": "Files",
    "rename_sdcard_file": "Files",
    "upload_file_to_host": "Files",
    "upload_file_to_printer": "Files",
    "download_file_from_printer": "Files",
    "get_3mf_props_for_file": "Files",
    "get_current_3mf_props": "Files",
    # Camera
    "analyze_active_job": "Camera",
}

_ROUTE_EXAMPLES: dict[str, dict] = {
    "health_check": {
        "response": {"status": "success", "printer": {"gcode_state": "RUNNING", "print_percentage": 42, "nozzle_temp": 220, "bed_temp": 35}},
        "params": {"printer": "H2D"},
    },
    "list_printers": {
        "params": {},
        "response": {"printers": [{"name": "H2D", "connected": True, "session_active": True}], "total": 1},
    },
    "default_printer": {
        "params": {},
        "response": {"status": "success", "printer": "H2D", "source": "env_var", "connected_printers": ["H2D", "A1"]},
    },
    "get_printer_info": {
        "response": {"gcode_state": "RUNNING", "print_percentage": 42, "nozzle_temp": 220, "nozzle_temp_target": 220, "bed_temp": 35, "bed_temp_target": 35, "chamber_temp": 21, "speed_level": "standard"},
        "params": {"printer": "H2D"},
    },
    "rename_printer": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "new_name": "My Printer"},
    },
    "toggle_active_tool": {
        "response": {"status": "success"},
        "params": {"printer": "H2D"},
    },
    "set_tool_target_temp": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "temp": "220"},
    },
    "set_bed_target_temp": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "temp": "35"},
    },
    "set_chamber_target_temp": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "temp": "45"},
    },
    "set_aux_fan_speed_target": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "percent": "70"},
    },
    "set_exhaust_fan_speed_target": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "percent": "50"},
    },
    "set_fan_speed_target": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "percent": "100"},
    },
    "set_light_state": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "state": "on"},
    },
    "set_speed_level": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "level": "standard"},
    },
    "unload_filament": {
        "response": {"status": "success"},
        "params": {"printer": "H2D"},
    },
    "load_filament": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "slot": "0"},
    },
    "refresh_spool_rfid": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "slot_id": "2", "ams_id": "0"},
    },
    "set_spool_details": {
        "response": {"status": "success"},
        "params": {
            "printer": "H2D",
            "tray_id": "1",
            "tray_info_idx": "GFA00",
            "tray_id_name": "Bambu PLA Basic",
            "tray_type": "PLA",
            "tray_color": "FF0000",
            "nozzle_temp_min": "190",
            "nozzle_temp_max": "240",
        },
    },
    "resume_printing": {
        "response": {"status": "success"},
        "params": {"printer": "H2D"},
    },
    "pause_printing": {
        "response": {"status": "success"},
        "params": {"printer": "H2D"},
    },
    "stop_printing": {
        "response": {"status": "success"},
        "params": {"printer": "H2D"},
    },
    "clear_print_error": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "print_error": "0", "subtask_id": ""},
    },
    "print_3mf": {
        "response": {"status": "success"},
        "params": {
            "printer": "H2D",
            "filename": "/_jobs/myprint.gcode.3mf",
            "platenum": "1",
            "plate": "TEXTURED_PLATE",
            "use_ams": "true",
            "ams_mapping": "",
            "bl": "true",
            "flow": "false",
            "tl": "false",
        },
    },
    "skip_objects": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "objects": "1,3"},
    },
    "refresh_sdcard_3mf_files": {
        "response": {"status": "success"},
        "params": {"printer": "H2D"},
    },
    "get_sdcard_3mf_files": {
        "response": ["/_jobs/myprint.gcode.3mf", "/cache/calibration.gcode.3mf"],
        "params": {"printer": "H2D"},
    },
    "refresh_sdcard_contents": {
        "response": {"status": "success"},
        "params": {"printer": "H2D"},
    },
    "get_sdcard_contents": {
        "response": {"/": ["_jobs", "cache"], "/_jobs": ["myprint.gcode.3mf"]},
        "params": {"printer": "H2D"},
    },
    "delete_sdcard_file": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "file": "/_jobs/myprint.gcode.3mf"},
    },
    "make_sdcard_directory": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "dir": "/_jobs/archive"},
    },
    "rename_sdcard_file": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "src": "/_jobs/old.gcode.3mf", "dest": "/_jobs/new.gcode.3mf"},
    },
    "upload_file_to_host": {
        "response": {"status": "success", "filename": "myprint.gcode.3mf"},
        "params": {},
    },
    "upload_file_to_printer": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "src": "myprint.gcode.3mf", "dest": "/_jobs/myprint.gcode.3mf"},
    },
    "download_file_from_printer": {
        "response": {},
        "params": {"printer": "H2D", "src": "/_jobs/myprint.gcode.3mf"},
    },
    "set_buildplate_marker_detector": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "enabled": "true"},
    },
    "set_first_layer_inspection": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "enabled": "true"},
    },
    "set_spaghetti_detector": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "enabled": "true", "sensitivity": "medium"},
    },
    "set_purgechutepileup_detector": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "enabled": "true", "sensitivity": "medium"},
    },
    "set_nozzleclumping_detector": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "enabled": "true", "sensitivity": "medium"},
    },
    "set_airprinting_detector": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "enabled": "true", "sensitivity": "medium"},
    },
    "get_detector_settings": {
        "response": {
            "spaghetti_detector": {"enabled": True, "sensitivity": "medium", "supported": True},
            "buildplate_marker_detector": {"enabled": True, "supported": True},
            "airprinting_detector": {"enabled": True, "sensitivity": "medium", "supported": True},
            "purgechutepileup_detector": {"enabled": True, "sensitivity": "medium", "supported": True},
            "nozzleclumping_detector": {"enabled": True, "sensitivity": "medium", "supported": True},
            "nozzle_blob_detect": {"enabled": False, "supported": True},
            "air_print_detect": {"enabled": False, "supported": True},
        },
        "params": {"printer": "H2D"},
    },
    "set_print_option": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "option": "AUTO_RECOVERY", "enabled": "true"},
    },
    "send_gcode": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "gcode": "G28|G1 X100 Y100 F3000"},
    },
    "send_mqtt_command": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "command_json": '{"print":{"command":"pause"}}'},
    },
    "send_ams_control_command": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "cmd": "RESUME"},
    },
    "select_extrusion_calibration": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "tray_id": "0", "cali_idx": "-1"},
    },
    "turn_on_ams_dryer": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "ams_id": "0", "target_temp": "55", "duration_hours": "4"},
    },
    "turn_off_ams_dryer": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "ams_id": "0"},
    },
    "set_ams_user_setting": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "setting": "CALIBRATE_REMAIN_FLAG", "enabled": "true"},
    },
    "set_nozzle_details": {
        "response": {"status": "success"},
        "params": {"printer": "H2D", "nozzle_diameter": "0.4", "nozzle_type": "HARDENED_STEEL"},
    },
    "refresh_nozzles": {
        "response": {"status": "success"},
        "params": {"printer": "H2D"},
    },
    "get_3mf_props_for_file": {
        "response": {
            "id": "myprint",
            "plates": [1],
            "filaments": [{"type": "PLA", "color": "FF0000", "nozzle_temp_min": 190, "nozzle_temp_max": 240}],
        },
        "params": {"printer": "H2D", "file": "/_jobs/myprint.gcode.3mf", "plate": "1"},
    },
    "get_current_3mf_props": {
        "response": {"id": "myprint", "status": "success", "plates": [1]},
        "params": {"printer": "H2D"},
    },
    "trigger_printer_refresh": {
        "response": {"status": "success"},
        "params": {"printer": "H2D"},
    },
    "toggle_session": {
        "response": {"status": "success", "state": "CONNECTED"},
        "params": {"printer": "H2D"},
    },
    "dump_log": {
        "response": "2024-01-01 12:00:00 INFO bambu-mcp started\n2024-01-01 12:00:01 INFO printer H2D connected",
        "params": {},
    },
    "truncate_log": {
        "response": {"status": "success"},
        "params": {},
    },
    "server_info": {
        "response": {
            "api_port": 49153,
            "api_url": "http://localhost:49153/api",
            "pool_start": 49152,
            "pool_end": 49251,
            "pool_size": 100,
            "pool_available": 98,
            "pool_claimed": [49153, 49154],
            "stream_count": 1,
            "streams": {"H2D": {"port": 49154, "url": "http://localhost:49154/"}},
        },
        "params": {},
    },
    "analyze_active_job": {
        "response": {
            "verdict": "clean",
            "anomaly_score": 0.04,
            "hot_pct": 0.03,
            "strand_score": 0.02,
            "diff_score": 0.06,
            "reference_age_s": 312.4,
            "success_probability": 0.95,
            "decision_confidence": 0.82,
            "factor_contributions": {"material": 0.04, "platform": 0.0, "anomaly": 0.026, "thermal": 0.0, "humidity": 0.0, "stability": 0.0, "settings": 0.0, "progress": 0.29},
            "stable_verdict": "clean",
            "stage": 255,
            "stage_name": "printing",
            "stage_gated": False,
            "layer": 32,
            "quality": "preview",
            "timestamp": "2026-03-08T12:00:00",
            "job_state_composite_jpg": "data:image/jpeg;base64,...",
        },
        "params": {"printer": "H2D", "quality": "auto", "categories": "X"},
    },
    "get_openapi_spec": {
        "response": {"openapi": "3.0.3", "info": {"title": "bambu-mcp API", "version": "1.0.0"}},
        "params": {},
    },
    "api_docs": {
        "response": {},
        "params": {},
    },
}

_TAG_DESCRIPTIONS: dict[str, str] = {
    "System": "Health, session management, logging, and server information.",
    "Climate": "Temperature targets, fan speeds, chamber light, and active tool selection.",
    "Print Control": "Start, pause, resume, stop, speed, skip objects, and raw G-code.",
    "AMS & Filament": "Filament loading, unloading, spool metadata, RFID refresh, and AMS settings.",
    "Hardware": "Nozzle configuration, AI vision detectors, and print option flags.",
    "Files": "SD card file listing, upload, download, delete, rename, and 3MF project metadata.",
    "Camera": "Live camera snapshot, active job state report, and stream control.",
}


def build_openapi_document(flask_app) -> dict:
    log.debug("build_openapi_document: called")
    try:
        from importlib.metadata import version as _pkg_version
        _api_version = _pkg_version("bambu-mcp")
    except Exception:
        _api_version = "0.0.0"

    paths: dict = {}
    for rule in sorted(flask_app.url_map.iter_rules(), key=lambda r: r.rule):
        if not rule.rule.startswith("/api/"):
            continue
        if rule.endpoint == "static":
            continue
        vf = flask_app.view_functions[rule.endpoint]
        params = _extract_query_params(vf)
        ep = rule.endpoint
        ex = _ROUTE_EXAMPLES.get(ep, {})
        param_examples = ex.get("params", {})
        resp_example = ex.get("response")

        # Inject the `printer` parameter for any route that calls _get_printer().
        # The param is consumed inside _get_printer() so _extract_query_params
        # never finds it in the view function source.
        try:
            vf_source = inspect.getsource(vf)
        except OSError:
            vf_source = ""
        if "_get_printer(" in vf_source and not any(p["name"] == "printer" for p in params):
            printer_param: dict = {
                "name": "printer",
                "in": "query",
                "required": True,
                "schema": {"type": "string"},
                "description": _PRINTER_DESC,
            }
            pex = ex.get("params", {}).get("printer")
            if pex is not None:
                printer_param["schema"]["example"] = pex
            params = [printer_param] + params

        # Enrich each extracted parameter schema with a realistic example value
        # and enum list (renders as <select> in Swagger UI)
        for p in params:
            pex = param_examples.get(p["name"])
            if pex is not None:
                p["schema"]["example"] = pex
            enum_vals = _ROUTE_ENUM_VALUES.get((ep, p["name"]))
            if enum_vals:
                p["schema"]["enum"] = enum_vals
            pdesc = _ROUTE_PARAM_DESCRIPTIONS.get((ep, p["name"]))
            if pdesc:
                p["description"] = pdesc

        # Build full description from the complete docstring (all lines, stripped)
        raw_doc = vf.__doc__ or ""
        doc_lines = [line.strip() for line in raw_doc.strip().splitlines()]
        summary = doc_lines[0] if doc_lines else ep.replace("_", " ")
        description = "\n".join(doc_lines).strip()

        tag = _ROUTE_TAGS.get(ep, "System")

        resp_schema: dict = {"type": "object"}
        if resp_example is not None:
            resp_schema["example"] = resp_example
        resp_content = {"application/json": {"schema": resp_schema}}

        ops = paths.setdefault(rule.rule, {})
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            op: dict = {
                "operationId": ep,
                "summary": summary,
                "description": description,
                "tags": [tag],
                "responses": {"200": {"description": "Success", "content": resp_content}},
            }
            if params and method in ("GET", "DELETE"):
                op["parameters"] = params
            elif params and method in ("POST", "PATCH"):
                # Build OpenAPI requestBody from the extracted params
                properties: dict = {}
                required_props: list[str] = []
                for p in params:
                    schema = dict(p.get("schema", {}))
                    prop: dict = {"type": schema.get("type", "string")}
                    if "enum" in schema:
                        prop["enum"] = schema["enum"]
                    if "example" in schema:
                        prop["example"] = schema["example"]
                    if p.get("description"):
                        prop["description"] = p["description"]
                    properties[p["name"]] = prop
                    if p.get("required"):
                        required_props.append(p["name"])
                body_schema: dict = {"type": "object", "properties": properties}
                if required_props:
                    body_schema["required"] = required_props
                op["requestBody"] = {
                    "content": {
                        "application/x-www-form-urlencoded": {"schema": body_schema},
                        "application/json": {"schema": body_schema},
                    }
                }
            elif params:
                op["parameters"] = params
            ops[method.lower()] = op

    doc = {
        "openapi": "3.0.3",
        "info": {
            "title": "bambu-mcp API",
            "version": _api_version,
            "description": "Bambu printer REST API backed by bambu-mcp session_manager.",
        },
        "tags": [{"name": name, "description": desc} for name, desc in _TAG_DESCRIPTIONS.items()],
        "paths": paths,
    }
    log.debug("build_openapi_document: → %d paths", len(paths))
    return doc


def swagger_ui_html(spec_url: str) -> str:
    log.debug("swagger_ui_html: spec_url=%s", spec_url)
    return f"""<!doctype html>
<html><head>
  <meta charset="utf-8"/>
  <title>bambu-mcp API Docs</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css"/>
</head><body>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>window.ui=SwaggerUIBundle({{url:"{spec_url}",dom_id:"#swagger-ui",deepLinking:true,persistAuthorization:true}});</script>
</body></html>"""


# ── Printer resolver ───────────────────────────────────────────────────────────

def _get_printer(request_args=None):
    """Return (printer, name) for the request, or (None, None) if unavailable."""
    log.debug("_get_printer: called")
    from session_manager import session_manager
    name = (request_args or {}).get("printer", "") or DEFAULT_PRINTER
    if not name:
        names = session_manager.list_connected()
        name = names[0] if names else ""
    if not name:
        log.warning("_get_printer: no connected printers")
        return None, None
    p = session_manager.get_printer(name)
    if p is None:
        log.warning("_get_printer: printer '%s' not found", name)
    log.debug("_get_printer: → name=%s printer=%s", name, p)
    return p, name


# ── Flask app factory ──────────────────────────────────────────────────────────

def _build_app():
    log.debug("_build_app: called")
    try:
        from flask import Flask, Response, jsonify, request, send_from_directory
        from flask_cors import CORS
    except ImportError as e:
        log.error("_build_app: flask/flask_cors not available: %s", e, exc_info=True)
        return None

    app = Flask("bambu_mcp_api")
    CORS(app)
    os.makedirs(_UPLOADS, exist_ok=True)

    def _ok(**kwargs):
        return jsonify({"status": "success", **kwargs})

    def _err(msg, code=HTTPStatus.INTERNAL_SERVER_ERROR):
        return jsonify({"status": "error", "reason": msg}), code

    def _rargs():
        """Merge query-string params, form body, and JSON body into one dict.

        Works for GET, POST, PATCH, and DELETE.
        Note: For DELETE and PATCH, params typically come via query string or JSON body;
        Flask does not populate request.form for DELETE/PATCH with application/json.
        """
        merged = dict(request.values)  # request.args + request.form
        if request.is_json:
            merged.update(request.get_json(silent=True) or {})
        return merged

    # ── state ──────────────────────────────────────────────────────────────────

    @app.route("/api/openapi.json")
    def get_openapi_spec():
        """Return the OpenAPI 3.0 specification for this API."""
        log.debug("get_openapi_spec: called")
        doc = build_openapi_document(app)
        log.debug("get_openapi_spec: → %d paths", len(doc["paths"]))
        return jsonify(doc)

    @app.route("/api/docs")
    @app.route("/api/docs/")
    def api_docs():
        """Serve Swagger UI for interactive API exploration."""
        log.debug("api_docs: called")
        html = swagger_ui_html("/api/openapi.json")
        log.debug("api_docs: → %d bytes", len(html))
        return Response(html, mimetype="text/html")

    @app.route("/api/filament_catalog")
    def filament_catalog():
        """Return the full filament profile catalog as a JSON array.

        Each entry contains: tray_info_idx, name, vendor, filament_type,
        nozzle_temp_min, nozzle_temp_max, hot_plate_temp.

        No printer parameter required — this is static reference data from BPM.
        """
        log.debug("filament_catalog: called")
        from bpm.bambucommands import FILAMENT_CATALOG as _catalog
        return jsonify(_catalog)

    @app.route("/api/printer")
    def get_printer_info():
        """Return full printer state as JSON."""
        log.debug("get_printer_info: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            log.warning("get_printer_info: no printer")
            return _err("no printer connected", HTTPStatus.NOT_MODIFIED)
        if not p.recent_update:
            log.warning("get_printer_info: no recent update")
            return _err("no data yet", HTTPStatus.NOT_MODIFIED)
        result = p.toJson()
        log.debug("get_printer_info: → ok")
        return jsonify(result)

    @app.route("/api/health_check")
    def health_check():
        """Return health status and full printer state."""
        log.debug("health_check: called")
        p, _ = _get_printer(_rargs())
        if p is None or not p.recent_update:
            log.warning("health_check: not ready")
            return _err("no data", HTTPStatus.INTERNAL_SERVER_ERROR)
        result = {"status": "success", "printer": p.toJson()}
        log.debug("health_check: → ok")
        return jsonify(result)

    @app.route("/api/printers")
    def list_printers():
        """Return all configured printers and their current connection status.

        No printer parameter required — this is server-level discovery data.

        Response fields:
        - `printers`: list of dicts, each with `name`, `connected`, `session_active`
        - `total`: count of configured printers

        Use this route to discover which printer names are available before calling
        printer-specific routes. Equivalent to the MCP `get_configured_printers()` tool.
        """
        log.debug("list_printers: called")
        from tools.management import get_configured_printers as _get_configured
        result = _get_configured()
        log.debug("list_printers: → %d printers", result.get("total", 0))
        return jsonify(result)

    @app.route("/api/default_printer")
    def default_printer():
        """Return the printer that would be targeted by a request with no explicit printer parameter.

        Resolves the default printer using the same three-tier logic as all printer-specific routes:
        1. `printer` query parameter (explicit — always wins)
        2. `BAMBU_API_PRINTER` environment variable
        3. First entry in the connected printers list (non-deterministic — use explicit targeting)

        Use this route to discover which printer to pass as the `printer` parameter on write routes.
        The `printer` parameter is required on all printer-specific routes; this route provides the
        discovery mechanism.

        Response fields:
        - `printer`: resolved printer name, or null if no printers are connected
        - `source`: resolution path — "explicit", "env_var", "first_connected", or "none"
        - `connected_printers`: all currently connected printer names
        """
        log.debug("default_printer: called")
        from session_manager import session_manager
        args = _rargs()
        explicit = args.get("printer", "")
        connected = session_manager.list_connected()

        if explicit:
            source = "explicit"
            name = explicit
        elif DEFAULT_PRINTER:
            source = "env_var"
            name = DEFAULT_PRINTER
        elif connected:
            source = "first_connected"
            name = connected[0]
        else:
            source = "none"
            name = None

        log.debug("default_printer: → name=%s source=%s", name, source)
        return _ok(printer=name, source=source, connected_printers=connected)

    # ── tool / thermal ─────────────────────────────────────────────────────────

    @app.route("/api/toggle_active_tool", methods=["PATCH"])
    def toggle_active_tool():
        """Swap active extruder between 0 (right) and 1 (left).

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("toggle_active_tool: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            tool = abs(p.printer_state.active_tool.value - 1)
            log.debug("toggle_active_tool: calling set_active_tool(%s)", tool)
            p.set_active_tool(tool)
            log.debug("toggle_active_tool: → ok")
            return _ok()
        except Exception as e:
            log.error("toggle_active_tool: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/set_tool_target_temp", methods=["PATCH"])
    def set_tool_target_temp():
        """Set nozzle temperature for the active tool. ?temp=<°C>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_tool_target_temp: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            target = int(_rargs().get("temp", 0))
            ext = p.printer_state.active_tool.value
            log.debug("set_tool_target_temp: target=%s ext=%s", target, ext)
            p.set_nozzle_temp_target(target, ext)
            log.debug("set_tool_target_temp: → ok")
            return _ok()
        except Exception as e:
            log.error("set_tool_target_temp: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/set_bed_target_temp", methods=["PATCH"])
    def set_bed_target_temp():
        """Set heated bed temperature. ?temp=<°C>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_bed_target_temp: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            target = int(_rargs().get("temp", 0))
            log.debug("set_bed_target_temp: target=%s", target)
            p.set_bed_temp_target(target)
            log.debug("set_bed_target_temp: → ok")
            return _ok()
        except Exception as e:
            log.error("set_bed_target_temp: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/set_chamber_target_temp", methods=["PATCH"])
    def set_chamber_target_temp():
        """Set chamber temperature target. On H2D, sends MQTT; on A1/P1S, stores target for external chamber management. ?temp=<°C>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_chamber_target_temp: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            target = int(_rargs().get("temp", 0))
            log.debug("set_chamber_target_temp: target=%s", target)
            p.set_chamber_temp_target(target)
            log.debug("set_chamber_target_temp: → ok")
            return _ok()
        except Exception as e:
            log.error("set_chamber_target_temp: error: %s", e, exc_info=True)
            return _err(str(e))

    # ── fans ───────────────────────────────────────────────────────────────────

    @app.route("/api/set_aux_fan_speed_target", methods=["PATCH"])
    def set_aux_fan_speed_target():
        """Set aux (recirculation) fan speed. ?percent=<0-100>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_aux_fan_speed_target: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            pct = int(_rargs().get("percent", 0))
            log.debug("set_aux_fan_speed_target: pct=%s", pct)
            p.set_aux_fan_speed_target_percent(pct)
            log.debug("set_aux_fan_speed_target: → ok")
            return _ok()
        except Exception as e:
            log.error("set_aux_fan_speed_target: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/set_exhaust_fan_speed_target", methods=["PATCH"])
    def set_exhaust_fan_speed_target():
        """Set exhaust fan speed. ?percent=<0-100>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_exhaust_fan_speed_target: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            pct = int(_rargs().get("percent", 0))
            log.debug("set_exhaust_fan_speed_target: pct=%s", pct)
            p.set_exhaust_fan_speed_target_percent(pct)
            log.debug("set_exhaust_fan_speed_target: → ok")
            return _ok()
        except Exception as e:
            log.error("set_exhaust_fan_speed_target: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/set_fan_speed_target", methods=["PATCH"])
    def set_fan_speed_target():
        """Set part-cooling fan speed. ?percent=<0-100>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_fan_speed_target: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            pct = int(_rargs().get("percent", 0))
            log.debug("set_fan_speed_target: pct=%s", pct)
            p.set_part_cooling_fan_speed_target_percent(pct)
            log.debug("set_fan_speed_target: → ok")
            return _ok()
        except Exception as e:
            log.error("set_fan_speed_target: error: %s", e, exc_info=True)
            return _err(str(e))

    # ── light / speed ──────────────────────────────────────────────────────────

    @app.route("/api/set_light_state", methods=["PATCH"])
    def set_light_state():
        """Set chamber light. ?state=on|off

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_light_state: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            state = _rargs().get("state") == "on"
            log.debug("set_light_state: state=%s", state)
            p.light_state = state
            log.debug("set_light_state: → ok")
            return _ok()
        except Exception as e:
            log.error("set_light_state: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/set_speed_level", methods=["PATCH"])
    def set_speed_level():
        """Set print speed level. ?level=quiet|standard|sport|ludicrous

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_speed_level: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            from bpm.bambutools import SpeedLevel
            level = _rargs().get("level", "standard")
            log.debug("set_speed_level: level=%s", level)
            p.speed_level = SpeedLevel[level.upper()]
            log.debug("set_speed_level: → ok")
            return _ok()
        except KeyError:
            return _err(f"Invalid level '{level}'. Must be one of: quiet, standard, sport, ludicrous.")
        except Exception as e:
            log.error("set_speed_level: error: %s", e, exc_info=True)
            return _err(str(e))

    # ── filament ───────────────────────────────────────────────────────────────

    @app.route("/api/unload_filament", methods=["POST"])
    def unload_filament():
        """⚠️ Unload the currently loaded filament back to AMS.

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("unload_filament: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            log.debug("unload_filament: calling printer.unload_filament()")
            p.unload_filament()
            log.debug("unload_filament: → ok")
            return _ok()
        except Exception as e:
            log.error("unload_filament: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/load_filament", methods=["POST"])
    def load_filament():
        """⚠️ Load filament from AMS slot. ?slot=<0-3>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("load_filament: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            slot = int(_rargs().get("slot", 0))
            log.debug("load_filament: slot=%s", slot)
            p.load_filament(slot)
            log.debug("load_filament: → ok")
            return _ok()
        except Exception as e:
            log.error("load_filament: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/refresh_spool_rfid", methods=["POST"])
    def refresh_spool_rfid():
        """⚠️ Trigger RFID re-scan on an AMS slot. ?slot_id=<0-3>&ams_id=<0-n>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("refresh_spool_rfid: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            slot_id = int(_rargs().get("slot_id", 0))
            ams_id = int(_rargs().get("ams_id", 0))
            log.debug("refresh_spool_rfid: slot_id=%s ams_id=%s", slot_id, ams_id)
            p.refresh_spool_rfid(slot_id, ams_id=ams_id)
            log.debug("refresh_spool_rfid: → ok")
            return _ok()
        except Exception as e:
            log.error("refresh_spool_rfid: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/set_spool_details", methods=["PATCH"])
    def set_spool_details():
        """Update filament metadata for an AMS slot. ?tray_id=<int>&tray_info_idx=<str>&...

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_spool_details: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            tray_id = int(_rargs().get("tray_id", 0))
            tray_info_idx = _rargs().get("tray_info_idx", "")
            tray_id_name = _rargs().get("tray_id_name", "")
            tray_type = _rargs().get("tray_type", "")
            tray_color = _rargs().get("tray_color", "")
            nozzle_temp_min = int(_rargs().get("nozzle_temp_min", 0)) if "nozzle_temp_min" in _rargs() else -1
            nozzle_temp_max = int(_rargs().get("nozzle_temp_max", 0)) if "nozzle_temp_max" in _rargs() else -1
            log.debug("set_spool_details: tray_id=%s tray_info_idx=%s", tray_id, tray_info_idx)
            p.set_spool_details(tray_id, tray_info_idx, tray_id_name, tray_type, tray_color, nozzle_temp_min, nozzle_temp_max)
            log.debug("set_spool_details: → ok (sleeping 2s)")
            time.sleep(2)
            return _ok()
        except Exception as e:
            log.error("set_spool_details: error: %s", e, exc_info=True)
            return _err(str(e))

    # ── print control ──────────────────────────────────────────────────────────

    @app.route("/api/resume_printing", methods=["POST"])
    def resume_printing():
        """⚠️ Resume a paused print job.

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("resume_printing: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            log.debug("resume_printing: calling printer.resume_printing()")
            p.resume_printing()
            log.debug("resume_printing: → ok")
            return _ok()
        except Exception as e:
            log.error("resume_printing: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/pause_printing", methods=["POST"])
    def pause_printing():
        """⚠️ Pause the current print job.

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("pause_printing: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            log.debug("pause_printing: calling printer.pause_printing()")
            p.pause_printing()
            log.debug("pause_printing: → ok")
            return _ok()
        except Exception as e:
            log.error("pause_printing: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/stop_printing", methods=["POST"])
    def stop_printing():
        """⚠️ Stop (cancel) the current print job.

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("stop_printing: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            log.debug("stop_printing: calling printer.stop_printing()")
            p.stop_printing()
            log.debug("stop_printing: → ok")
            return _ok()
        except Exception as e:
            log.error("stop_printing: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/clear_print_error", methods=["POST"])
    def clear_print_error():
        """⚠️ Clear an active print_error on the printer. ?print_error=<int>&subtask_id=<str>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.

        Sends two commands matching the protocol BambuStudio uses when dismissing an
        error dialog: clean_print_error (clears the error value) and a uiop signal
        (acknowledges the dialog). Without both commands, the printer may re-raise the
        error on the next push_status.

        ?print_error=0 clears any active error without specifying a code.
        """
        log.debug("clear_print_error: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            print_error = int(_rargs().get("print_error", 0))
            subtask_id = _rargs().get("subtask_id", "")
            log.debug("clear_print_error: print_error=%s subtask_id=%s", print_error, subtask_id)
            p.clean_print_error(subtask_id=subtask_id, print_error=print_error)
            p.clean_print_error_uiop(print_error=print_error)
            log.debug("clear_print_error: → ok print_error=%08X", print_error)
            return _ok()
        except Exception as e:
            log.error("clear_print_error: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/print_3mf", methods=["POST"])
    def print_3mf():
        """⚠️ Start printing a .3mf file from SD card. ?filename=&platenum=&plate=AUTO|COOL_PLATE|ENG_PLATE|HOT_PLATE|TEXTURED_PLATE&use_ams=true|false&ams_mapping=&bl=true|false&flow=true|false&tl=true|false

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("print_3mf: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            from bpm.bambutools import PlateType
            filename = _rargs().get("filename", "")
            platenum = int(_rargs().get("platenum", 0))
            plate_str = _rargs().get("plate", "AUTO").upper()
            platetype = PlateType[plate_str]
            use_ams = _rargs().get("use_ams") == "true"
            ams_mapping = _rargs().get("ams_mapping")
            bl = _rargs().get("bl") == "true"
            flow = _rargs().get("flow") == "true"
            tl = _rargs().get("tl") == "true"
            log.debug("print_3mf: filename=%s platenum=%s plate=%s use_ams=%s", filename, platenum, platetype, use_ams)
            p.print_3mf_file(filename, platenum, platetype, use_ams, ams_mapping=ams_mapping, bedlevel=bl, flow=flow, timelapse=tl)
            log.debug("print_3mf: → ok")
            return _ok()
        except Exception as e:
            log.error("print_3mf: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/skip_objects", methods=["POST"])
    def skip_objects():
        """⚠️ Skip one or more objects. ?objects=<id1>,<id2>,...

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("skip_objects: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            objects = _rargs().get("objects", "").split(",")
            log.debug("skip_objects: objects=%s", objects)
            p.skip_objects(objects)
            log.debug("skip_objects: → ok")
            return _ok()
        except Exception as e:
            log.error("skip_objects: error: %s", e, exc_info=True)
            return _err(str(e))

    # ── SD card ────────────────────────────────────────────────────────────────

    @app.route("/api/refresh_sdcard_3mf_files", methods=["POST"])
    def refresh_sdcard_3mf_files():
        """⚠️ Trigger refresh of .3mf file listing from SD card.

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("refresh_sdcard_3mf_files: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            log.debug("refresh_sdcard_3mf_files: calling get_sdcard_3mf_files()")
            p.get_sdcard_3mf_files()
            log.debug("refresh_sdcard_3mf_files: → ok")
            return _ok()
        except Exception as e:
            log.error("refresh_sdcard_3mf_files: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/get_sdcard_3mf_files")
    def get_sdcard_3mf_files():
        """Return list of .3mf files on SD card.

        ?cached=true returns the in-memory cached copy without a live FTPS fetch.
        Use when stale data is acceptable and low latency matters.
        Default (cached=false) performs a live FTPS fetch for up-to-date results.
        """
        log.debug("get_sdcard_3mf_files: called")
        p, args = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            use_cache = str(args.get("cached", "false")).lower() == "true"
            if use_cache:
                log.debug("get_sdcard_3mf_files: returning cached_sd_card_3mf_files")
                result = p.cached_sd_card_3mf_files
            else:
                log.debug("get_sdcard_3mf_files: calling printer.get_sdcard_3mf_files()")
                result = p.get_sdcard_3mf_files()
            log.debug("get_sdcard_3mf_files: → ok")
            return jsonify(result)
        except Exception as e:
            log.error("get_sdcard_3mf_files: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/refresh_sdcard_contents", methods=["POST"])
    def refresh_sdcard_contents():
        """⚠️ Trigger full SD card contents refresh.

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("refresh_sdcard_contents: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            log.debug("refresh_sdcard_contents: calling get_sdcard_contents()")
            p.get_sdcard_contents()
            log.debug("refresh_sdcard_contents: → ok")
            return _ok()
        except Exception as e:
            log.error("refresh_sdcard_contents: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/get_sdcard_contents")
    def get_sdcard_contents():
        """Return full SD card directory listing.

        ?cached=true returns the in-memory cached copy without a live FTPS fetch.
        Use when stale data is acceptable and low latency matters.
        Default (cached=false) performs a live FTPS fetch for up-to-date results.
        """
        log.debug("get_sdcard_contents: called")
        p, args = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            use_cache = str(args.get("cached", "false")).lower() == "true"
            if use_cache:
                log.debug("get_sdcard_contents: returning cached_sd_card_contents")
                result = p.cached_sd_card_contents
            else:
                log.debug("get_sdcard_contents: calling printer.get_sdcard_contents()")
                result = p.get_sdcard_contents()
            log.debug("get_sdcard_contents: → ok")
            return jsonify(result)
        except Exception as e:
            log.error("get_sdcard_contents: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/find_3mf_by_name")
    def find_3mf_by_name():
        """Search SD card 3MF file tree by filename. ?name=<filename>"""
        log.debug("find_3mf_by_name: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            target_name = _rargs().get("name", "")
            log.debug("find_3mf_by_name: target_name=%s", target_name)
            if not target_name:
                return _err("name parameter required")
            from bpm.bambuproject import get_3mf_entry_by_name as _bpm_search
            tree = p.get_sdcard_3mf_files()
            if tree is None:
                return _err("failed to retrieve SD card contents")
            result = _bpm_search(tree, target_name)
            log.debug("find_3mf_by_name: → %s", "found" if result else "not found")
            return jsonify({"entry": result} if result else {"error": f"Not found: {target_name}"})
        except Exception as e:
            log.error("find_3mf_by_name: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/find_3mf_by_id")
    def find_3mf_by_id():
        """Search SD card 3MF file tree by full path ID. ?id=<path>"""
        log.debug("find_3mf_by_id: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            target_id = _rargs().get("id", "")
            log.debug("find_3mf_by_id: target_id=%s", target_id)
            if not target_id:
                return _err("id parameter required")
            from bpm.bambuproject import get_3mf_entry_by_id as _bpm_search
            tree = p.get_sdcard_3mf_files()
            if tree is None:
                return _err("failed to retrieve SD card contents")
            result = _bpm_search(tree, target_id)
            log.debug("find_3mf_by_id: → %s", "found" if result else "not found")
            return jsonify({"entry": result} if result else {"error": f"Not found: {target_id}"})
        except Exception as e:
            log.error("find_3mf_by_id: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/delete_sdcard_file", methods=["DELETE"])
    def delete_sdcard_file():
        """⚠️ Delete a file or folder from SD card. ?file=<path> (trailing / for folder)

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("delete_sdcard_file: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            path = _rargs().get("file", "")
            log.debug("delete_sdcard_file: path=%s", path)
            if path.endswith("/"):
                result = p.delete_sdcard_folder(path)
            else:
                result = p.delete_sdcard_file(path)
            log.debug("delete_sdcard_file: → ok")
            return jsonify(result)
        except Exception as e:
            log.error("delete_sdcard_file: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/make_sdcard_directory", methods=["POST"])
    def make_sdcard_directory():
        """⚠️ Create a directory on SD card. ?dir=<path>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("make_sdcard_directory: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            d = _rargs().get("dir", "")
            log.debug("make_sdcard_directory: dir=%s", d)
            result = p.make_sdcard_directory(d)
            log.debug("make_sdcard_directory: → ok")
            return jsonify(result)
        except Exception as e:
            log.error("make_sdcard_directory: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/rename_sdcard_file", methods=["PATCH"])
    def rename_sdcard_file():
        """Rename or move an SD card file. ?src=<path>&dest=<path>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("rename_sdcard_file: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            src = _rargs().get("src", "")
            dest = _rargs().get("dest", "")
            log.debug("rename_sdcard_file: src=%s dest=%s", src, dest)
            result = p.rename_sdcard_file(src, dest)
            log.debug("rename_sdcard_file: → ok")
            return jsonify(result)
        except Exception as e:
            log.error("rename_sdcard_file: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/upload_file_to_host", methods=["GET", "POST"])
    def upload_file_to_host():
        """Upload a file to the local uploads directory. POST multipart/form-data with field 'myFile'."""
        log.debug("upload_file_to_host: called")
        try:
            f = request.files.get("myFile")
            if f is None:
                return _err("no file in request", HTTPStatus.BAD_REQUEST)
            dest = os.path.join(_UPLOADS, f.filename)
            log.debug("upload_file_to_host: saving to %s", dest)
            f.save(dest)
            log.debug("upload_file_to_host: → ok")
            return _ok(filename=f.filename)
        except Exception as e:
            log.error("upload_file_to_host: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/upload_file_to_printer", methods=["POST"])
    def upload_file_to_printer():
        """⚠️ Upload a local file to the printer SD card. ?src=<filename_in_uploads>&dest=<remote_path>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("upload_file_to_printer: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            src = _rargs().get("src", "")
            dest = _rargs().get("dest", "")
            local = os.path.join(_UPLOADS, src)
            log.debug("upload_file_to_printer: src=%s dest=%s", local, dest)
            result = p.upload_sdcard_file(local, dest)
            log.debug("upload_file_to_printer: → ok")
            return jsonify(result)
        except Exception as e:
            log.error("upload_file_to_printer: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/download_file_from_printer", methods=["GET", "POST"])
    def download_file_from_printer():
        """⚠️ Download a file from the printer SD card and return it. ?src=<remote_path>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("download_file_from_printer: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            src = _rargs().get("src", "")
            filename = src[src.rindex("/") + 1:]
            local = os.path.join(_UPLOADS, filename)
            log.debug("download_file_from_printer: src=%s local=%s", src, local)
            p.download_sdcard_file(src, local)
            log.debug("download_file_from_printer: → serving %s", filename)
            return send_from_directory(_UPLOADS, filename)
        except Exception as e:
            log.error("download_file_from_printer: error: %s", e, exc_info=True)
            return _err(str(e))

    # ── detectors ─────────────────────────────────────────────────────────────

    @app.route("/api/set_buildplate_marker_detector", methods=["PATCH"])
    def set_buildplate_marker_detector():
        """Enable/disable buildplate ArUco marker detector. ?enabled=true|false

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_buildplate_marker_detector: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            enabled = _rargs().get("enabled") == "true"
            log.debug("set_buildplate_marker_detector: enabled=%s", enabled)
            p.set_buildplate_marker_detector(enabled)
            log.debug("set_buildplate_marker_detector: → ok")
            return _ok()
        except Exception as e:
            log.error("set_buildplate_marker_detector: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/set_first_layer_inspection", methods=["PATCH"])
    def set_first_layer_inspection():
        """Enable/disable first-layer LiDAR/camera inspection. ?enabled=true|false

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_first_layer_inspection: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            enabled = _rargs().get("enabled") == "true"
            log.debug("set_first_layer_inspection: enabled=%s", enabled)
            cmd = {
                "xcam": {
                    "command": "xcam_control_set",
                    "control": enabled,
                    "enable": enabled,
                    "module_name": "first_layer_inspector",
                    "print_halt": False,
                    "sequence_id": "0",
                }
            }
            p.send_anything(json.dumps(cmd))
            log.debug("set_first_layer_inspection: → ok")
            return _ok()
        except Exception as e:
            log.error("set_first_layer_inspection: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/set_spaghetti_detector", methods=["PATCH"])
    def set_spaghetti_detector():
        """Enable/disable spaghetti detector. ?enabled=true|false&sensitivity=low|medium|high

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_spaghetti_detector: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            enabled = _rargs().get("enabled") == "true"
            sensitivity = _rargs().get("sensitivity", "medium")
            log.debug("set_spaghetti_detector: enabled=%s sensitivity=%s", enabled, sensitivity)
            p.set_spaghetti_detector(enabled, sensitivity)
            log.debug("set_spaghetti_detector: → ok")
            return _ok()
        except Exception as e:
            log.error("set_spaghetti_detector: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/set_purgechutepileup_detector", methods=["PATCH"])
    def set_purgechutepileup_detector():
        """Enable/disable purge chute pile-up detector. ?enabled=true|false&sensitivity=low|medium|high

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_purgechutepileup_detector: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            enabled = _rargs().get("enabled") == "true"
            sensitivity = _rargs().get("sensitivity", "medium")
            log.debug("set_purgechutepileup_detector: enabled=%s sensitivity=%s", enabled, sensitivity)
            p.set_purgechutepileup_detector(enabled, sensitivity)
            log.debug("set_purgechutepileup_detector: → ok")
            return _ok()
        except Exception as e:
            log.error("set_purgechutepileup_detector: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/set_nozzleclumping_detector", methods=["PATCH"])
    def set_nozzleclumping_detector():
        """Enable/disable nozzle clumping detector. ?enabled=true|false&sensitivity=low|medium|high

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_nozzleclumping_detector: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            enabled = _rargs().get("enabled") == "true"
            sensitivity = _rargs().get("sensitivity", "medium")
            log.debug("set_nozzleclumping_detector: enabled=%s sensitivity=%s", enabled, sensitivity)
            p.set_nozzleclumping_detector(enabled, sensitivity)
            log.debug("set_nozzleclumping_detector: → ok")
            return _ok()
        except Exception as e:
            log.error("set_nozzleclumping_detector: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/set_airprinting_detector", methods=["PATCH"])
    def set_airprinting_detector():
        """Enable/disable air-printing detector. ?enabled=true|false&sensitivity=low|medium|high

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_airprinting_detector: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            enabled = _rargs().get("enabled") == "true"
            sensitivity = _rargs().get("sensitivity", "medium")
            log.debug("set_airprinting_detector: enabled=%s sensitivity=%s", enabled, sensitivity)
            p.set_airprinting_detector(enabled, sensitivity)
            log.debug("set_airprinting_detector: → ok")
            return _ok()
        except Exception as e:
            log.error("set_airprinting_detector: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/get_detector_settings")
    def get_detector_settings():
        """Return the current state of all X-Cam AI detector settings on the printer.

        Returns enabled/disabled state and sensitivity for each supported detector:
        spaghetti_detector, buildplate_marker_detector, airprinting_detector,
        purgechutepileup_detector, nozzleclumping_detector. Also returns the legacy
        home_flag detectors: nozzle_blob_detect and air_print_detect.
        Each entry includes 'supported' (bool) indicating hardware support.
        """
        log.debug("get_detector_settings: called")
        p, name = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            from session_manager import session_manager
            config = session_manager.get_config(name)
            if config is None:
                return _err(f"Printer '{name}' config unavailable")
            log.debug("get_detector_settings: building detector state for %s", name)
            result = {
                "spaghetti_detector": {
                    "enabled": config.spaghetti_detector,
                    "sensitivity": config.spaghetti_detector_sensitivity,
                    "supported": config.capabilities.has_spaghetti_detector_support,
                },
                "buildplate_marker_detector": {
                    "enabled": config.buildplate_marker_detector,
                    "supported": config.capabilities.has_buildplate_marker_detector_support,
                },
                "airprinting_detector": {
                    "enabled": config.airprinting_detector,
                    "sensitivity": config.airprinting_detector_sensitivity,
                    "supported": config.capabilities.has_airprinting_detector_support,
                },
                "purgechutepileup_detector": {
                    "enabled": config.purgechutepileup_detector,
                    "sensitivity": config.purgechutepileup_detector_sensitivity,
                    "supported": config.capabilities.has_purgechutepileup_detector_support,
                },
                "nozzleclumping_detector": {
                    "enabled": config.nozzleclumping_detector,
                    "sensitivity": config.nozzleclumping_detector_sensitivity,
                    "supported": config.capabilities.has_nozzleclumping_detector_support,
                },
                "nozzle_blob_detect": {
                    "enabled": config.nozzle_blob_detect,
                    "supported": config.capabilities.has_nozzle_blob_detect_support,
                },
                "air_print_detect": {
                    "enabled": config.air_print_detect,
                    "supported": config.capabilities.has_air_print_detect_support,
                },
            }
            log.debug("get_detector_settings: → ok %d detectors", len(result))
            return jsonify(result)
        except Exception as e:
            log.error("get_detector_settings: error: %s", e, exc_info=True)
            return _err(str(e))

    # ── print options / gcode / AMS ───────────────────────────────────────────

    @app.route("/api/set_print_option", methods=["PATCH"])
    def set_print_option():
        """Set a print option flag. ?option=AUTO_RECOVERY|SOUND_ENABLE|...&enabled=true|false

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_print_option: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            from bpm.bambutools import PrintOption
            option = PrintOption[_rargs().get("option", "").upper()]
            enabled = _rargs().get("enabled") == "true"
            log.debug("set_print_option: option=%s enabled=%s", option, enabled)
            p.set_print_option(option, enabled)
            log.debug("set_print_option: → ok")
            return _ok()
        except Exception as e:
            log.error("set_print_option: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/send_gcode", methods=["POST"])
    def send_gcode():
        """⚠️ Send raw G-code commands. ?gcode=<commands> (use | as newline separator)

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("send_gcode: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            gcode = _rargs().get("gcode", "").replace("|", "\n")
            log.debug("send_gcode: gcode=%s", repr(gcode[:80]))
            p.send_gcode(gcode)
            log.debug("send_gcode: → ok")
            return _ok()
        except Exception as e:
            log.error("send_gcode: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/send_mqtt_command", methods=["POST"])
    def send_mqtt_command():
        """⚠️ Send a raw MQTT command JSON to the printer's request topic. ?command_json=<json>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        ⚠️ LAST-RESORT TOOL. Bypasses all safety checks. Incorrect commands can damage
        prints, trigger hardware faults, or put the printer into an unrecoverable state.
        command_json must be a valid JSON string matching the Bambu Lab MQTT command schema.
        """
        log.debug("send_mqtt_command: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            import json as _json
            command_json = _rargs().get("command_json", "")
            _json.loads(command_json)  # validate JSON before sending
            log.debug("send_mqtt_command: command=%s", command_json[:80])
            p.send_anything(command_json)
            log.debug("send_mqtt_command: → ok")
            return _ok()
        except ValueError as e:
            log.error("send_mqtt_command: invalid JSON: %s", e)
            return _err(f"invalid JSON: {e}")
        except Exception as e:
            log.error("send_mqtt_command: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/send_ams_control_command", methods=["POST"])
    def send_ams_control_command():
        """⚠️ Send AMS control command. ?cmd=PAUSE|RESUME|RESET

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("send_ams_control_command: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            from bpm.bambutools import AMSControlCommand
            cmd = AMSControlCommand[_rargs().get("cmd", "").upper()]
            log.debug("send_ams_control_command: cmd=%s", cmd)
            p.send_ams_control_command(cmd)
            log.debug("send_ams_control_command: → ok")
            return _ok()
        except Exception as e:
            log.error("send_ams_control_command: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/set_ams_user_setting", methods=["PATCH"])
    def set_ams_user_setting():
        """Set AMS user setting. ?setting=CALIBRATE_REMAIN_FLAG|STARTUP_READ_OPTION|TRAY_READ_OPTION&enabled=true|false

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_ams_user_setting: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            from bpm.bambutools import AMSUserSetting
            setting = AMSUserSetting[_rargs().get("setting", "").upper()]
            enabled = _rargs().get("enabled") == "true"
            log.debug("set_ams_user_setting: setting=%s enabled=%s", setting, enabled)
            p.set_ams_user_setting(setting, enabled)
            log.debug("set_ams_user_setting: → ok")
            return _ok()
        except Exception as e:
            log.error("set_ams_user_setting: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/select_extrusion_calibration", methods=["POST"])
    def select_extrusion_calibration():
        """⚠️ Select an extrusion calibration profile for a filament slot. ?tray_id=<int>&cali_idx=<int>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.

        tray_id encoding: ams_unit_index × 4 + slot (0–3). External spool = 254.
        cali_idx = -1 to auto-select the best matching profile for the loaded filament.
        """
        log.debug("select_extrusion_calibration: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            tray_id = int(_rargs().get("tray_id", 0))
            cali_idx = int(_rargs().get("cali_idx", -1))
            log.debug("select_extrusion_calibration: tray_id=%s cali_idx=%s", tray_id, cali_idx)
            p.select_extrusion_calibration_profile(tray_id, cali_idx)
            log.debug("select_extrusion_calibration: → ok")
            return _ok()
        except Exception as e:
            log.error("select_extrusion_calibration: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/turn_on_ams_dryer", methods=["POST"])
    def turn_on_ams_dryer():
        """⚠️ Start the AMS filament dryer. ?ams_id=<int>&target_temp=<int>&duration_hours=<int>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.

        ams_id is the internal chip_id (not the user-facing unit_id): AMS 2 Pro starts at 0,
        AMS HT starts at 128. target_temp defaults to 55°C; duration_hours defaults to 4.
        Only supported on AMS 2 Pro and AMS HT models.
        """
        log.debug("turn_on_ams_dryer: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            ams_id = int(_rargs().get("ams_id", 0))
            target_temp = int(_rargs().get("target_temp", 55))
            duration_hours = int(_rargs().get("duration_hours", 4))
            log.debug("turn_on_ams_dryer: ams_id=%s target_temp=%s duration_hours=%s", ams_id, target_temp, duration_hours)
            p.turn_on_ams_dryer(target_temp=target_temp, duration=duration_hours, ams_id=ams_id)
            log.debug("turn_on_ams_dryer: → ok")
            return _ok()
        except Exception as e:
            log.error("turn_on_ams_dryer: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/turn_off_ams_dryer", methods=["POST"])
    def turn_off_ams_dryer():
        """⚠️ Stop the AMS filament dryer. ?ams_id=<int>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.

        ams_id is the internal chip_id: AMS 2 Pro starts at 0, AMS HT starts at 128.
        """
        log.debug("turn_off_ams_dryer: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            ams_id = int(_rargs().get("ams_id", 0))
            log.debug("turn_off_ams_dryer: ams_id=%s", ams_id)
            p.turn_off_ams_dryer(ams_id=ams_id)
            log.debug("turn_off_ams_dryer: → ok")
            return _ok()
        except Exception as e:
            log.error("turn_off_ams_dryer: error: %s", e, exc_info=True)
            return _err(str(e))

    # ── nozzle ─────────────────────────────────────────────────────────────────

    @app.route("/api/set_nozzle_details", methods=["PATCH"])
    def set_nozzle_details():
        """Set nozzle diameter and type. ?nozzle_diameter=0.2|0.4|0.6|0.8&nozzle_type=BRASS|HARDENED_STEEL|...

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("set_nozzle_details: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            from bpm.bambutools import NozzleDiameter, NozzleType
            nozzle_diameter = NozzleDiameter(float(_rargs().get("nozzle_diameter", 0.4)))
            nozzle_type = NozzleType[_rargs().get("nozzle_type", "BRASS")]
            log.debug("set_nozzle_details: diameter=%s type=%s", nozzle_diameter, nozzle_type)
            p.set_nozzle_details(nozzle_diameter, nozzle_type)
            log.debug("set_nozzle_details: → ok (sleeping 1s)")
            time.sleep(1)
            return _ok()
        except Exception as e:
            log.error("set_nozzle_details: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/refresh_nozzles", methods=["POST"])
    def refresh_nozzles():
        """⚠️ Trigger nozzle hardware re-read.

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("refresh_nozzles: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            log.debug("refresh_nozzles: calling printer.refresh_nozzles()")
            p.refresh_nozzles()
            log.debug("refresh_nozzles: → ok")
            return _ok()
        except Exception as e:
            log.error("refresh_nozzles: error: %s", e, exc_info=True)
            return _err(str(e))

    # ── 3MF project info ───────────────────────────────────────────────────────

    @app.route("/api/get_3mf_props_for_file")
    def get_3mf_props_for_file():
        """Return 3MF project properties for a file on SD card. ?file=<path>&plate=<int>"""
        log.debug("get_3mf_props_for_file: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            from bpm.bambuproject import get_project_info as _gpi
            file = _rargs().get("file", "")
            plate = int(_rargs().get("plate", 0))
            log.debug("get_3mf_props_for_file: file=%s plate=%s", file, plate)
            props = _gpi(file, p, plate_num=plate, use_cached_list=True)
            if not props:
                log.warning("get_3mf_props_for_file: not found file=%s", file)
                return _err("No file found", HTTPStatus.NOT_FOUND)
            log.debug("get_3mf_props_for_file: → ok")
            return jsonify(asdict(props))
        except Exception as e:
            log.error("get_3mf_props_for_file: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/get_current_3mf_props")
    def get_current_3mf_props():
        """Return 3MF project properties for the currently active print job."""
        log.debug("get_current_3mf_props: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            info = p.active_job_info
            if not info or not getattr(info, "project_info", None) or getattr(info.project_info, "id", "") == "":
                log.warning("get_current_3mf_props: no active job")
                return _err("No Job Found", HTTPStatus.NOT_FOUND)
            resp = asdict(info.project_info)
            resp["status"] = "success"
            log.debug("get_current_3mf_props: → ok")
            return jsonify(resp)
        except Exception as e:
            log.error("get_current_3mf_props: error: %s", e, exc_info=True)
            return _err(str(e))

    # ── session / system ───────────────────────────────────────────────────────

    @app.route("/api/rename_printer", methods=["PATCH"])
    def rename_printer():
        """Rename the printer on its own firmware. ?new_name=<name>

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("rename_printer: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            new_name = _rargs().get("new_name", "").strip()
            if not new_name:
                return _err("new_name is required")
            log.debug("rename_printer: new_name=%s", new_name)
            p.rename_printer(new_name)
            log.debug("rename_printer: → ok")
            return _ok()
        except Exception as e:
            log.error("rename_printer: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/trigger_printer_refresh", methods=["POST"])
    def trigger_printer_refresh():
        """⚠️ Force printer to re-broadcast its full state.

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("trigger_printer_refresh: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            log.debug("trigger_printer_refresh: calling printer.refresh()")
            p.refresh()
            log.debug("trigger_printer_refresh: → ok")
            return _ok()
        except Exception as e:
            log.error("trigger_printer_refresh: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/toggle_session", methods=["PATCH"])
    def toggle_session():
        """Pause or resume the MQTT session for the printer.

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("toggle_session: called")
        p, _ = _get_printer(_rargs())
        if p is None:
            return _err("no printer")
        try:
            from bpm.bambutools import ServiceState
            log.debug("toggle_session: service_state=%s", p.service_state)
            if p.service_state is ServiceState.PAUSED:
                p.resume_session()
            elif p.service_state is ServiceState.CONNECTED:
                p.pause_session()
            log.debug("toggle_session: → ok state=%s", p.service_state.name)
            return _ok(state=p.service_state.name)
        except Exception as e:
            log.error("toggle_session: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/dump_log")
    def dump_log():
        """Return the bambu-mcp server log."""
        log.debug("dump_log: called")
        try:
            log_path = os.path.join(os.path.dirname(__file__), "bambu-mcp.log")
            if not os.path.isfile(log_path):
                return Response("", mimetype="text/plain")
            with open(log_path, "r") as f:
                content = f.read()
            log.debug("dump_log: → %d bytes", len(content))
            return Response(content, mimetype="text/plain")
        except Exception as e:
            log.error("dump_log: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.route("/api/truncate_log", methods=["DELETE"])
    def truncate_log():
        """⚠️ Truncate the bambu-mcp server log.

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        """
        log.debug("truncate_log: called")
        try:
            log_path = os.path.join(os.path.dirname(__file__), "bambu-mcp.log")
            if os.path.isfile(log_path):
                with open(log_path, "r+") as f:
                    os.ftruncate(f.fileno(), 0)
            log.debug("truncate_log: → ok")
            return _ok()
        except Exception as e:
            log.error("truncate_log: error: %s", e, exc_info=True)
            return _err(str(e))

    # ── server info ───────────────────────────────────────────────────────────

    @app.route("/api/server_info")
    def server_info():
        """
        Return runtime port pool state for the bambu-mcp server.

        No printer parameter required — this is server-level state.

        Returns:
            api_port       — TCP port the REST API is currently bound to
            api_url        — convenience base URL: http://localhost:{api_port}/api
            pool_start     — first port in the shared ephemeral pool (default 49152)
            pool_end       — last port in the shared ephemeral pool inclusive (default 49251)
            pool_size      — total number of ports in the pool
            pool_available — number of unclaimed ports remaining
            pool_claimed   — sorted list of all currently claimed port numbers (API + MJPEG streams)
            stream_count   — number of active MJPEG camera streams
            streams        — {printer_name: {port, url}} for each active stream
        """
        log.debug("server_info: called")
        try:
            from port_pool import port_pool as _pp
            from camera.mjpeg_server import mjpeg_server as _mjs
            state = _pp.get_state()
            streams = _mjs.get_active_streams()
            pool_size = state["pool_end"] - state["pool_start"] + 1
            result = {
                "api_port":       _port,
                "api_url":        f"http://localhost:{_port}/api",
                "pool_start":     state["pool_start"],
                "pool_end":       state["pool_end"],
                "pool_size":      pool_size,
                "pool_available": pool_size - len(state["pool_claimed"]),
                "pool_claimed":   state["pool_claimed"],
                "stream_count":   len(streams),
                "streams":        streams,
            }
            log.debug("server_info: → %s", result)
            return jsonify(result)
        except Exception as e:
            log.error("server_info: error: %s", e, exc_info=True)
            return _err(str(e))

    # ── set k-factor (stubbed — not yet in session_manager) ──────────────────

    @app.route("/api/set_spool_k_factor", methods=["POST"])
    def set_spool_k_factor():
        """⚠️ Set extrusion calibration k-factor for a spool. (stub — returns success)

        ⚠️ WRITE OPERATION — requires explicit user confirmation before calling.
        ⚠️ STUB / FIRMWARE WARNING: This route is a no-op that always returns
        `{"status": "success"}` without sending any command to the printer.
        The underlying BPM method `set_spool_k_factor()` carries a docstring
        warning ("Broken in recent Bambu firmware") and recommends
        `select_extrusion_calibration_profile` instead. The `@deprecated`
        Python decorator was removed in a later BPM update, but the firmware
        limitation stands. Use the `select_extrusion_calibration` MCP tool
        to manage calibration profiles via the supported API.
        """
        log.debug("set_spool_k_factor: called (stubbed)")
        return _ok()

    @app.route("/api/analyze_active_job")
    def analyze_active_job():
        """Capture the live camera frame and produce a full active job state report.

        Returns a cohesive suite of digital assets representing every meaningful
        dimension of the active print job: project identity, live camera reality,
        anomaly detection (spaghetti/strand sub-module), print health, and a
        composite dashboard. All images use the HUD design system (dark palette,
        verdict badges, zone overlays).

        Query parameters:
          printer         — printer name (required)
          store_reference — "true" to store the current frame as the diff baseline
          quality         — "auto" | "preview" | "standard" | "full"
                            auto scales with verdict severity (clean=preview,
                            warning=standard, critical=full)
          categories      — comma-separated asset category letters to include
                            (default "X" — composite only):
                            P = project_thumbnail_png + project_layout_png
                            C = raw_png + diff_png
                            D = air_zone_png + mask_png + annotated_png +
                                heat_png + edge_png
                            H = health_panel_png
                            X = job_state_composite_jpg (default primary output)

        Response fields (always present):
          verdict               — "clean" | "warning" | "critical"
          anomaly_score         — composite anomaly score (0–1)
          hot_pct               — bright-pixel fraction in air zone
          strand_score          — directional strand-likelihood (0–1)
          diff_score            — frame-diff from reference, or null
          reference_age_s       — age of reference frame in seconds, or null
          success_probability   — Bayesian print health score (0–1; 1.0 = fully healthy)
          decision_confidence   — agent's ability to assess failure given current data (0–1)
          factor_contributions  — dict of 8 Bayesian factor scores for radar chart (0–1 each)
          stable_verdict        — consensus verdict from recent analysis window, or null
          stage                 — current printer stage code
          stage_name            — human-readable stage name
          stage_gated           — true when analysis is skipped due to non-printing stage
          layer                 — current layer number
          quality               — resolved quality tier used
          timestamp             — ISO8601 capture time

        Error responses:
          400 {"error": "no_active_job"} — gcode_state is IDLE/FINISH/FAILED
          400 {"error": "no_camera"}     — printer has no camera
          400 {"error": "not_connected"} — MQTT session not active
        """
        log.debug("analyze_active_job: called")
        import importlib
        try:
            printer_name = _rargs().get("printer", "")
            store_ref    = _rargs().get("store_reference", "false").lower() == "true"
            quality      = _rargs().get("quality", "auto")
            cats_raw     = _rargs().get("categories", "")
            categories   = [c.strip() for c in cats_raw.split(",") if c.strip()] or None
            cam = importlib.import_module("tools.camera")
            result = cam.analyze_active_job(
                printer_name,
                store_as_reference=store_ref,
                quality=quality,
                categories=categories,
            )
            if "error" in result:
                return jsonify(result), 400
            return jsonify(result)
        except Exception as e:
            log.error("analyze_active_job: error: %s", e, exc_info=True)
            return jsonify({"status": "error", "message": str(e)}), 500

    # ── error handler ─────────────────────────────────────────────────────────

    @app.route("/api/alerts", methods=["GET", "DELETE"])
    def alerts():
        """Return or clear pending state-change alerts for a printer.

        Query parameters:
          printer — printer name (required)
          all     — "true" to return alerts for all printers (GET only; ignores printer param)

        GET  returns pending alerts (clears queue by default).
        DELETE clears the queue without returning alerts.
        """
        log.debug("alerts: called method=%s", request.method)
        from notifications import notifications as _notifications
        try:
            if request.method == "GET":
                all_printers = _rargs().get("all", "false").lower() == "true"
                if all_printers:
                    return jsonify(_notifications.get_all_pending(clear=True))
                p, name = _get_printer(_rargs())
                if p is None:
                    return _err("no printer")
                return jsonify(_notifications.get_pending(name, clear=True))
            else:  # DELETE
                p, name = _get_printer(_rargs())
                if p is None:
                    return _err("no printer")
                _notifications.clear(name)
                return jsonify({"status": "ok"})
        except Exception as e:
            log.error("alerts: error: %s", e, exc_info=True)
            return _err(str(e))

    @app.errorhandler(500)
    def handle_500(e):
        import traceback
        log.error("handle_500: unhandled exception: %s", e, exc_info=True)
        return jsonify({"status": "error", "message": str(e), "stacktrace": traceback.format_exc()}), 500

    log.debug("_build_app: → app built with %d routes", len(list(app.url_map.iter_rules())))
    return app


# ── Server lifecycle ───────────────────────────────────────────────────────────

def start(port: int | None = None) -> int:
    """
    Start the bambu-mcp-api HTTP server in a background non-daemon thread.

    If the server is already running, returns the current port immediately without
    starting a second instance. On first call, builds the Flask app, allocates a
    port from the shared ephemeral pool (IANA RFC 6335 range 49152–65535), binds a
    wsgiref WSGI server to 0.0.0.0:{port}, and starts the serve_forever loop in a
    named thread ("bambu-mcp-api").

    Port selection:
      - If BAMBU_API_PORT env var is set, it is used as a preferred-port hint (tried
        first; rotates to next available pool port if taken).
      - The *port* argument overrides BAMBU_API_PORT when provided.
      - Preferred values outside the pool range are still attempted before rotation.

    Args:
        port: Preferred TCP port.  If None, BAMBU_API_PORT env var is used as hint
              (or pool rotation starts from BAMBU_PORT_POOL_START = 49152 by default).
              Ignored if the server is already running.

    Returns:
        The port the server is (or was already) listening on.

    Raises:
        RuntimeError: If Flask is unavailable or the app fails to build.
        OSError: If the port pool is exhausted (all 100 pool ports are in use).
    """
    global _flask_app, _server_thread, _werkzeug_server, _port
    log.debug("start: called port=%s", port)

    if _server_thread is not None and _server_thread.is_alive():
        log.info("start: already running on port %s", _port)
        return _port

    # Resolve preferred port: explicit arg → env var → no preference
    if port is None:
        env_port = os.environ.get("BAMBU_API_PORT")
        port = int(env_port) if env_port else None

    from port_pool import port_pool as _pp
    _port = _pp.allocate(preferred=port)
    log.debug("start: allocated port %d from pool", _port)
    _flask_app = _build_app()
    if _flask_app is None:
        log.error("start: failed to build Flask app")
        raise RuntimeError("bambu-mcp-api: Flask unavailable")

    def _run():
        global _werkzeug_server
        log.info("start: _run: serving on http://0.0.0.0:%s", _port)
        try:
            import logging as _logging
            _logging.getLogger("werkzeug").setLevel(_logging.WARNING)
            from wsgiref.simple_server import make_server as _make_server, WSGIServer, WSGIRequestHandler

            _app = _flask_app  # capture reference at thread-start time

            def _safe_app(environ, start_response):
                try:
                    return _app(environ, start_response)
                except Exception as exc:
                    log.error("wsgi: unhandled app exception: %s", exc, exc_info=True)
                    try:
                        start_response("500 Internal Server Error",
                                       [("Content-Type", "text/plain"), ("Content-Length", "21")])
                    except Exception:
                        pass
                    return [b"Internal Server Error"]

            class _QuietHandler(WSGIRequestHandler):
                def log_message(self, fmt, *args):
                    log.debug("http: " + fmt, *args)
                def log_error(self, fmt, *args):
                    log.warning("http error: " + fmt, *args)
                def get_stderr(self):
                    import io
                    class _LogWriter(io.RawIOBase):
                        def write(self, b):
                            log.warning("wsgiref stderr: %s", b.decode("utf-8", "replace").rstrip())
                            return len(b)
                    return _LogWriter()

            _werkzeug_server = _make_server("0.0.0.0", _port, _safe_app,
                                            server_class=WSGIServer,
                                            handler_class=_QuietHandler)
            log.info("start: _run: wsgiref server created, entering serve_forever")
            _werkzeug_server.serve_forever()
            log.info("start: _run: serve_forever returned (server stopped normally)")
        except Exception as exc:
            log.error("start: _run: FATAL: %s", exc, exc_info=True)

    _server_thread = threading.Thread(target=_run, name="bambu-mcp-api", daemon=False)
    _server_thread.start()
    log.info("bambu-mcp-api started on http://localhost:%s  (docs: http://localhost:%s/api/docs)", _port, _port)
    return _port


def stop() -> None:
    """
    Shut down the bambu-mcp-api HTTP server and release the server thread.

    Calls shutdown() on the wsgiref server, which causes serve_forever() to return
    and the server thread to exit. Releases the allocated port back to the shared
    ephemeral pool so other listeners can reuse it.  Safe to call when not running
    — no-op in that case.  After stop(), is_running() returns False and start() can
    be called again.
    """
    global _server_thread, _werkzeug_server, _port
    log.debug("stop: called")
    if _werkzeug_server is not None:
        try:
            _werkzeug_server.shutdown()
            log.info("stop: werkzeug server shut down")
        except Exception as e:
            log.warning("stop: shutdown error: %s", e, exc_info=True)
        _werkzeug_server = None
    if _port:
        try:
            from port_pool import port_pool as _pp
            _pp.release(_port)
            log.debug("stop: released port %d to pool", _port)
        except Exception as e:
            log.warning("stop: port release error: %s", e, exc_info=True)
        _port = 0
    _server_thread = None
    log.info("stop: bambu-mcp-api stopped")


def is_running() -> bool:
    """Return True if the server thread exists and is currently alive."""
    log.debug("is_running: called → %s", _server_thread is not None and _server_thread.is_alive())
    return _server_thread is not None and _server_thread.is_alive()


def get_url() -> str:
    """Return the base URL of the HTTP API server (e.g. 'http://localhost:49152')."""
    log.debug("get_url: called → port=%s", _port)
    return f"http://localhost:{_port}"


def get_port() -> int:
    """Return the TCP port the HTTP API server is currently bound to (0 if not running)."""
    log.debug("get_port: called → %d", _port)
    return _port
