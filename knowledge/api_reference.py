"""
api_reference.py — BambuPrinter API reference summary for bambu-mcp agents.

Top-level topic: get_knowledge_topic('api_reference')

This is the summary of the BambuPrinter Python API — the library backing bambu-mcp.
For detailed method documentation, call the sub-topics listed below.

Sources: bambu-printer-manager/src/bpm/bambuprinter.py, bambuconfig.py,
         bambustate.py, bambudiscovery.py, bambuproject.py, bambuspool.py,
         bambutools.py
"""

from __future__ import annotations

API_REFERENCE_TEXT: str = """
# BambuPrinter API Reference — Summary

All printer operations in bambu-mcp route through BambuPrinter instances managed by
session_manager. Access via `session_manager.get_printer(name) -> BambuPrinter`.

---

## Sub-Topics

Call the relevant sub-topic for detailed method signatures and parameters.

### Session management, raw commands
BambuPrinter class intro, constructor, start/pause/resume/quit/refresh session,
send_gcode, send_anything.
→ `get_knowledge_topic('api_reference/session')`

### FTPS file management
ftp_connection, get_sdcard_contents, get/delete/download/upload/rename/mkdir SD card.
→ `get_knowledge_topic('api_reference/files')`

### Print control, temperatures, fans
pause/resume/stop_printing, print_3mf_file, skip_objects, set_nozzle/bed/chamber temps,
set part cooling / exhaust / aux fan speeds.
→ `get_knowledge_topic('api_reference/print')`

### AMS, spools, calibration, hardware, xcam detectors
load/unload filament, AMS dryer, set_spool_details, extrusion calibration, set_nozzle_details,
set_active_tool, rename_printer, all xcam AI vision detectors.
→ `get_knowledge_topic('api_reference/ams')`

### Properties, BambuConfig, PrinterCapabilities, BambuState
set_print_option, toJson, all printer properties, BambuConfig optional parameters and
their home_flag/xcam sources, PrinterCapabilities auto-discovery, BambuState field table,
BambuClimate, fan speed scaling.
→ `get_knowledge_topic('api_reference/state')`

### BambuSpool, ProjectInfo, ActiveJobInfo, utility functions
BambuSpool dataclass, ProjectInfo/metadata keys, ActiveJobInfo fields,
BambuDiscovery/DiscoveredPrinter, all bambutools.py utility functions.
→ `get_knowledge_topic('api_reference/dataclasses')`

---

## Quick Method Index

| Category | Key methods |
|---|---|
| Session | start_session, pause_session, resume_session, quit, refresh |
| Files | get_sdcard_contents, upload_sdcard_file, download_sdcard_file, delete_sdcard_file |
| Print | print_3mf_file, pause_printing, resume_printing, stop_printing, skip_objects |
| Temperature | set_nozzle_temp_target, set_bed_temp_target, set_chamber_temp_target |
| Fans | set_part_cooling_fan_speed_target_percent, set_exhaust_fan_speed_target_percent |
| AMS | load_filament, unload_filament, turn_on_ams_dryer, set_spool_details |
| Hardware | set_nozzle_details, set_active_tool, rename_printer, refresh_nozzles |
| State | printer_state (BambuState), config (BambuConfig), active_job_info (ActiveJobInfo) |
| Options | set_print_option, light_state (setter), speed_level (setter) |
"""
