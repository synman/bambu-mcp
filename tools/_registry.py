"""Registration and MCP annotations for every bambu-mcp tool.

A tool is registered when it is a function defined in one of TOOL_MODULES that carries a
docstring, except the four names url_factory owns, which are registered from there only.
Every registered tool MUST have an ANNOTATIONS entry: a missing or orphaned entry raises at
startup, so a new tool cannot ship without a title and a read-only classification.

Entry: name -> (title, read_only, destructive, idempotent, open_world). read_only is False
for every tool that takes user_permission, and for the two unguarded stream tools that start
and stop the local MJPEG server (start_stream, stop_stream). The tools that only show data are
read-only even though they write a temp file or open a viewer or browser tab (view_stream,
open_charts, open_job_state, open_plate_layout, open_plate_viewer), as is analyze_active_job,
whose store_as_reference option is an opt-in mode; they are not idempotent because each repeat
opens another window. get_pending_alerts is read-only because its purpose is to read, with the
queue drain documented. Titles carry a "Bambu: " prefix so they stay unique across the MCP servers a client registers together.
"""

from __future__ import annotations

from mcp.types import ToolAnnotations

import tools.camera
import tools.charts
import tools.climate
import tools.commands
import tools.detectors
import tools.discovery
import tools.filament
import tools.files
import tools.management
import tools.notifications
import tools.nozzle
import tools.print_control
import tools.state
import tools.system
import tools.url_factory

TOOL_MODULES = [
    tools.state,
    tools.print_control,
    tools.climate,
    tools.filament,
    tools.nozzle,
    tools.detectors,
    tools.management,
    tools.files,
    tools.system,
    tools.discovery,
    tools.commands,
    tools.camera,
    tools.notifications,
    tools.url_factory,
    tools.charts,
]

# Registered from tools.url_factory only; the same names in their original modules are skipped.
URL_FACTORY_NAMES: frozenset[str] = frozenset({
    "get_snapshot",
    "get_monitoring_data",
    "get_monitoring_history",
    "get_monitoring_series",
})

ANNOTATIONS: dict[str, tuple[str, bool, bool, bool, bool]] = {
    "add_printer": ("Bambu: Add Printer", False, True, False, True),
    "analyze_active_job": ("Bambu: Analyze Active Print Job", True, False, False, True),
    "calibrate_ams_remaining": ("Bambu: Rescan AMS Spool RFID", False, False, True, True),
    "clear_print_error": ("Bambu: Clear Print Error", False, False, True, True),
    "create_folder": ("Bambu: Create SD Card Folder", False, False, True, True),
    "delete_file": ("Bambu: Delete SD Card File", False, True, True, True),
    "disconnect_printer": ("Bambu: Disconnect Printer", False, False, True, True),
    "discover_printers": ("Bambu: Discover LAN Printers", True, False, True, True),
    "download_file": ("Bambu: Download SD Card File", False, True, True, True),
    "dump_log": ("Bambu: Read Server Log", True, False, True, False),
    "force_state_refresh": ("Bambu: Force Printer State Refresh", False, False, True, True),
    "get_3mf_entry_by_id": ("Bambu: Find 3MF Entry By Path", True, False, True, True),
    "get_3mf_entry_by_name": ("Bambu: Find 3MF Entry By Name", True, False, True, True),
    "get_all_project_info": ("Bambu: Get All Plate Info", True, False, True, True),
    "get_ams_status": ("Bambu: Get AMS Status", True, False, True, False),
    "get_ams_units": ("Bambu: Get AMS Units", True, False, True, False),
    "get_capabilities": ("Bambu: Get Printer Capabilities", True, False, True, False),
    "get_chamber_light": ("Bambu: Get Chamber Light State", True, False, True, False),
    "get_climate": ("Bambu: Get Climate Status", True, False, True, False),
    "get_configured_printers": ("Bambu: List Configured Printers", True, False, True, False),
    "get_current_job_project_info": ("Bambu: Get Current Job Project Info", True, False, True, True),
    "get_detector_settings": ("Bambu: Get Detector Settings", True, False, True, False),
    "get_external_spool": ("Bambu: Get External Spool", True, False, True, False),
    "get_fan_speeds": ("Bambu: Get Fan Speeds", True, False, True, False),
    "get_file_info": ("Bambu: Get SD Card File Info", True, False, True, True),
    "get_firmware_version": ("Bambu: Get Firmware Version", True, False, True, False),
    "get_hms_errors": ("Bambu: Get HMS Errors", True, False, True, False),
    "get_job_info": ("Bambu: Get Print Job Info", True, False, True, False),
    "get_monitoring_data": ("Bambu: Get Monitoring Data URL", True, False, True, False),
    "get_monitoring_history": ("Bambu: Get Monitoring History URL", True, False, True, False),
    "get_monitoring_series": ("Bambu: Get Monitoring Series URL", True, False, True, False),
    "get_nozzle_info": ("Bambu: Get Nozzle Info", True, False, True, False),
    "get_pending_alerts": ("Bambu: Get Pending Alerts", True, False, False, False),
    "get_plate_thumbnail": ("Bambu: Get Plate Thumbnail", True, False, True, True),
    "get_plate_topview": ("Bambu: Get Plate Top View", True, False, True, True),
    "get_print_progress": ("Bambu: Get Print Progress", True, False, True, False),
    "get_printer_connection_status": ("Bambu: Get Printer Connection Status", True, False, True, False),
    "get_printer_info": ("Bambu: Get Printer Info", True, False, True, False),
    "get_printer_state": ("Bambu: Get Printer State", True, False, True, False),
    "get_project_info": ("Bambu: Get Project Plate Info", True, False, True, True),
    "get_server_info": ("Bambu: Get Server Port Info", True, False, True, False),
    "get_session_status": ("Bambu: Get MQTT Session Status", True, False, True, False),
    "get_snapshot": ("Bambu: Get Camera Snapshot URL", True, False, True, False),
    "get_spool_info": ("Bambu: Get Spool Info", True, False, True, False),
    "get_stream_url": ("Bambu: Get Camera Stream Info", True, False, True, False),
    "get_temperatures": ("Bambu: Get Temperatures", True, False, True, False),
    "get_wifi_signal": ("Bambu: Get Wi-Fi Signal", True, False, True, False),
    "list_sdcard_files": ("Bambu: List SD Card Files", True, False, True, True),
    "load_filament": ("Bambu: Load Filament", False, False, False, True),
    "open_charts": ("Bambu: Open Telemetry Dashboard", True, False, False, False),
    "open_job_state": ("Bambu: Open Job State Images", True, False, False, False),
    "open_plate_layout": ("Bambu: Open Plate Layout Image", True, False, False, True),
    "open_plate_viewer": ("Bambu: Open Plate Viewer", True, False, False, True),
    "pause_mqtt_session": ("Bambu: Pause MQTT Session", False, False, True, True),
    "pause_print": ("Bambu: Pause Print", False, False, True, True),
    "preview_ams_mapping": ("Bambu: Preview AMS Mapping", True, False, True, True),
    "print_file": ("Bambu: Start Print From SD Card", False, True, False, True),
    "refresh_nozzles": ("Bambu: Refresh Nozzle Info", False, False, True, True),
    "refresh_sdcard": ("Bambu: Refresh SD Card Listing", True, False, True, True),
    "remove_printer": ("Bambu: Remove Printer", False, True, True, True),
    "rename_printer": ("Bambu: Rename Printer Device", False, False, True, True),
    "rename_sdcard_file": ("Bambu: Rename SD Card File", False, False, True, True),
    "render_charts_html": ("Bambu: Render Dashboard HTML", True, False, True, False),
    "render_charts_panels": ("Bambu: Render Dashboard Panels", True, False, True, False),
    "resume_mqtt_session": ("Bambu: Resume MQTT Session", False, False, True, True),
    "resume_print": ("Bambu: Resume Print", False, False, True, True),
    "select_extrusion_calibration": ("Bambu: Select Extrusion Calibration", False, False, True, True),
    "send_ams_control_command": ("Bambu: Send AMS Control Command", False, False, False, True),
    "send_gcode": ("Bambu: Send G-code", False, True, False, True),
    "send_mqtt_command": ("Bambu: Send Raw MQTT Command", False, True, False, True),
    "set_air_printing_detection": ("Bambu: Set Air Printing Detection", False, False, True, True),
    "set_ams_filament_setting": ("Bambu: Set AMS Filament Setting", False, False, True, True),
    "set_ams_user_setting": ("Bambu: Set AMS User Setting", False, False, True, True),
    "set_bed_temp": ("Bambu: Set Bed Temperature", False, False, True, True),
    "set_buildplate_marker_detection": ("Bambu: Set Buildplate Marker Detection", False, False, True, True),
    "set_chamber_light": ("Bambu: Set Chamber Light", False, False, True, True),
    "set_chamber_temp": ("Bambu: Set Chamber Temperature", False, False, True, True),
    "set_fan_speed": ("Bambu: Set Fan Speed", False, False, True, True),
    "set_first_layer_inspection": ("Bambu: Set First Layer Inspection", False, False, True, True),
    "set_nozzle_clumping_detection": ("Bambu: Set Nozzle Clumping Detection", False, False, True, True),
    "set_nozzle_config": ("Bambu: Set Nozzle Config", False, False, True, True),
    "set_nozzle_temp": ("Bambu: Set Nozzle Temperature", False, False, True, True),
    "set_print_option": ("Bambu: Set Print Option", False, False, True, True),
    "set_print_options": ("Bambu: Set Print Options", False, False, True, True),
    "set_print_speed": ("Bambu: Set Print Speed", False, False, True, True),
    "set_purge_chute_detection": ("Bambu: Set Purge Chute Detection", False, False, True, True),
    "set_spaghetti_detection": ("Bambu: Set Spaghetti Detection", False, False, True, True),
    "skip_objects": ("Bambu: Skip Print Objects", False, True, True, True),
    "start_ams_dryer": ("Bambu: Start AMS Dryer", False, False, False, True),
    "start_printer": ("Bambu: Start Printer Session", False, False, False, True),
    "start_stream": ("Bambu: Start Camera Stream", False, False, True, True),
    "stop_ams_dryer": ("Bambu: Stop AMS Dryer", False, False, True, True),
    "stop_print": ("Bambu: Stop Print", False, True, True, True),
    "stop_stream": ("Bambu: Stop Camera Stream", False, False, True, True),
    "swap_tool": ("Bambu: Swap Active Extruder", False, False, False, True),
    "trigger_printer_refresh": ("Bambu: Trigger Printer Refresh", False, False, True, True),
    "truncate_log": ("Bambu: Truncate Server Log", False, True, True, False),
    "unload_filament": ("Bambu: Unload Filament", False, False, True, True),
    "update_printer_credentials": ("Bambu: Update Printer Credentials", False, True, False, True),
    "upload_file": ("Bambu: Upload File To SD Card", False, True, True, True),
    "view_stream": ("Bambu: View Camera Stream", True, False, False, True),
}


def registered_tools() -> list:
    """Return the tool functions bambu-mcp registers, in registration order."""
    found = []
    for mod in TOOL_MODULES:
        for name in dir(mod):
            if name.startswith("_"):
                continue
            if name in URL_FACTORY_NAMES and mod.__name__ != "tools.url_factory":
                continue
            fn = getattr(mod, name)
            if callable(fn) and getattr(fn, "__module__", None) == mod.__name__ and getattr(fn, "__doc__", None):
                found.append(fn)
    return found


def register_tools(mcp) -> int:
    """Register every tool on mcp with its title and annotations; return the count."""
    fns = registered_tools()
    names = {fn.__name__ for fn in fns}
    missing = sorted(names - ANNOTATIONS.keys())
    orphaned = sorted(ANNOTATIONS.keys() - names)
    if missing or orphaned:
        raise RuntimeError(
            f"tool annotations out of step with registered tools: missing={missing} orphaned={orphaned}"
        )
    for fn in fns:
        title, read_only, destructive, idempotent, open_world = ANNOTATIONS[fn.__name__]
        mcp.add_tool(
            fn,
            title=title,
            annotations=ToolAnnotations(
                title=title,
                readOnlyHint=read_only,
                destructiveHint=destructive,
                idempotentHint=idempotent,
                openWorldHint=open_world,
            ),
        )
    return len(fns)
