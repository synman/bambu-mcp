"""
api_reference_print.py — BambuPrinter print control, temperature, and fan methods.

Sub-topic of api_reference. Access via get_knowledge_topic('api_reference/print').
"""

from __future__ import annotations

API_REFERENCE_PRINT_TEXT: str = """
# BambuPrinter API — Print Control, Temperature & Fans

All signatures sourced from bambuprinter.py.

---

## Print Control Methods

#### pause_printing() -> None
Publishes PAUSE_PRINT command to `device/{serial}/request`.

#### resume_printing() -> None
Publishes RESUME_PRINT command to `device/{serial}/request`.

#### stop_printing() -> None
Publishes STOP_PRINT command to `device/{serial}/request`.

#### print_3mf_file(name: str, plate: int, bed: PlateType, use_ams: bool, ams_mapping: str | None = "", bedlevel: bool | None = True, flow: bool | None = True, timelapse: bool | None = False) -> None
Submits request to print a .3mf file already on the SD card.
- name: Full SD card path including leading / (e.g. "/jobs/my_project.3mf")
- plate: 1-indexed plate number from slicer
- bed: PlateType enum (sent as bed_type = plate.name.lower())
- use_ams: True to use AMS routing
- ams_mapping: JSON array string of absolute tray IDs (from ProjectInfo.metadata["ams_mapping"])
  Auto-generates ams_mapping2 ({"ams_id": int, "slot_id": int} dicts).
- URL format: A1/P1 → "file:///sdcard{path}", others → "ftp://{path}"
Publishes PRINT_3MF_FILE to `device/{serial}/request`.

#### skip_objects(objects: list[int | str]) -> None
Cancels listed objects during current print. IDs are identify_id values from
slice_info.config, available in ProjectInfo.metadata["map"]["bbox_objects"][N]["id"].
Values coerced to int before sending. Publishes SKIP_OBJECTS.

---

## Temperature Methods

#### set_nozzle_temp_target(value: int, tool_num: int = -1) -> None
Sets nozzle temperature target via GCode M104.
Format: `f"M104 S{value}{'' if tool_num == -1 else ' T' + str(tool_num)}\\n"`
Updates `_tool_temp_target_time`.

#### set_bed_temp_target(value: int) -> None
Sets bed temperature via GCode M140. Format: `f"M140 S{value}\\n"`.
Updates `_bed_temp_target_time`.

#### set_chamber_temp(value: float) -> None
Injects external chamber temperature (for printers without internal CTC).
Sets `_printer_state.climate.chamber_temp` directly (no MQTT publish).

#### set_chamber_temp_target(value: int, temper_check: bool = True) -> None
Sets chamber temperature target. If has_chamber_temp capability:
  - Publishes SET_CHAMBER_TEMP_TARGET with ctt_val=value, temper_check=temper_check
  - Publishes SET_CHAMBER_AC_MODE with modeId=0 (value<40) or modeId=1 (value>=40)
Always sets `_printer_state.climate.chamber_temp_target = value`.
Updates `_chamber_temp_target_time`.

---

## Fan Speed Methods

#### set_part_cooling_fan_speed_target_percent(value: int) -> None
Sets part cooling fan via GCode M106 P1. Scale: percent → 0-255 (value * 2.55).
Updates `_fan_speed_target_time`.

#### set_exhaust_fan_speed_target_percent(value: int) -> None
Sets exhaust fan via GCode M106 P3. Scale: percent → 0-255.

#### set_aux_fan_speed_target_percent(value: int) -> None  [DEPRECATED]
Sets aux fan via GCode M106 P2. Use select_extrusion_calibration_profile instead.
"""
