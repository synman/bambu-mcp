"""
api_reference_ams.py — BambuPrinter AMS, spool, calibration, hardware, and detector methods.

Sub-topic of api_reference. Access via get_knowledge_topic('api_reference/ams').
"""

from __future__ import annotations

API_REFERENCE_AMS_TEXT: str = """
# BambuPrinter API — AMS, Spool, Calibration, Hardware & Detectors

All signatures sourced from bambuprinter.py.

---

## AMS Methods

#### load_filament(slot_id: int, ams_id: int = 0) -> None
Loads filament from AMS slot. Publishes AMS_CHANGE_FILAMENT.
slot_id: 0-3 = AMS slots, 254 = external spool.

#### unload_filament(ams_id: int = 0) -> None
Unloads current filament. Publishes AMS_CHANGE_FILAMENT.

#### send_ams_control_command(ams_control_cmd: AMSControlCommand) -> None
Sends PAUSE/RESUME/RESET to AMS. RESUME also calls resume_printing() automatically.

#### set_ams_user_setting(setting: AMSUserSetting, enabled: bool, ams_id: int = 0) -> None
Enables/disables one AMSUserSetting. All three settings sent together in one command.
Updates corresponding BambuConfig attribute.

#### refresh_spool_rfid(slot_id: int, ams_id: int = 0) -> None
Requests RFID re-read for specified AMS slot. Publishes AMS_GET_RFID.

#### turn_on_ams_dryer(target_temp: int, duration: int, target_humidity: int = 0, cooling_temp: int = 45, rotate_tray: bool = False, ams_id: int = 0, filament_type: str = "") -> None
Turns on AMS dryer with specified parameters. Publishes AMS_FILAMENT_DRYING.
Updates `ams_unit.temp_target = target_temp`. Raises if ams_id not found.

#### turn_off_ams_dryer(ams_id: int = 0) -> None
Turns off AMS dryer. Publishes AMS_FILAMENT_DRYING with mode=0.
Resets `ams_unit.temp_target = 0`. Raises if ams_id not found.

#### get_current_bind_list(state: BambuState) -> list[dict]
Builds `manual_ams_bind` list for H2D dual-extruder firmware.
Hardware register inversion: RIGHT_EXTRUDER(0)→hw 1, LEFT_EXTRUDER(1)→hw 0.
When only 1 AMS connected, appends sentinel placeholder (Unit ID 1).
Returns list of {"ams_f_bind": int, "ams_s_bind": int, "extruder": int}.

---

## Spool Methods

#### set_spool_details(tray_id: int, tray_info_idx: str, tray_id_name: str | None = "", tray_type: str | None = "", tray_color: str | None = "", nozzle_temp_min: int | None = -1, nozzle_temp_max: int | None = -1, ams_id: int | None = 0) -> None
Sets spool/tray filament type, color, temp range. Publishes AMS_FILAMENT_SETTING.
- tray_id: absolute tray ID (ams_id * 4 + slot_id, or 254 for external)
- tray_info_idx: filament catalog code (e.g. "GFA00"). Pass "no_filament" to clear tray.
  This is a primary identity field — encodes base profile (temps, drying, flow).
- tray_id_name: Bambu Lab vendor-specific brand label (e.g. "Bambu PLA Basic"). Optional
  and absent on third-party spools. NOT a reliable spool identifier. The true identity of
  a spool is tray_color + tray_info_idx (base profile), not this name field.
- tray_color: CSS name or RRGGBB/RRGGBBAA hex string — primary identity field
- ams_id parameter is unused (derived automatically from tray_id)
- WARNING: Sends ALL fields in a single command. Empty string values ("") are
  interpreted by the printer as "clear this field". Always pass all relevant fields
  (tray_info_idx, tray_type, tray_color, nozzle_temp_min, nozzle_temp_max) together.

#### set_spool_k_factor(tray_id: int, k_value: float, n_coef: float | None = 1.399999976158142, nozzle_temp: int | None = -1, bed_temp: int | None = -1, max_volumetric_speed: int | None = -1) -> None
Sets linear advance k factor. Broken in recent Bambu firmware.
Use select_extrusion_calibration_profile instead.

---

## Calibration Methods

#### select_extrusion_calibration_profile(tray_id: int, cali_idx: int = -1) -> None
Sets k factor profile for specified tray. Publishes EXTRUSION_CALI_SEL.
cali_idx: -1 = default profile.

---

## Hardware Control Methods

#### set_nozzle_details(nozzle_diameter: NozzleDiameter, nozzle_type: NozzleType) -> None
Informs printer of installed nozzle. Publishes SET_ACCESSORIES.
nozzle_type sent as telemetry string (via nozzle_type_to_telemetry()).

#### set_active_tool(id: int) -> None
Switches active extruder on multi-tool machines (H2D).
id: 0=right extruder, 1=left extruder. Publishes SET_ACTIVE_TOOL.

#### refresh_nozzles() -> None
Requests printer to push back current nozzle state (multi-extruder models).
Publishes REFRESH_NOZZLE.

#### rename_printer(new_name: str) -> None
Renames printer. Publishes RENAME_PRINTER with update.name = new_name.
Printer responds with update ack (no dedicated handler in bpm).

---

## X-Cam / AI Vision Detector Methods

All xcam methods publish XCAM_CONTROL_SET with:
  control=enabled, enable=enabled, print_halt=True, halt_print_sensitivity=sensitivity.value

#### set_buildplate_marker_detector(enabled: bool) -> None
Enables/disables buildplate ArUco marker scanning. module_name="buildplate_marker_detector".

#### set_spaghetti_detector(enabled: bool, sensitivity: DetectorSensitivity = DetectorSensitivity.MEDIUM) -> None
Enables/disables spaghetti/failed-print detector. module_name="spaghetti_detector".

#### set_purgechutepileup_detector(enabled: bool, sensitivity: DetectorSensitivity = DetectorSensitivity.MEDIUM) -> None
Enables/disables purge-chute pile-up detector. module_name="pileup_detector".

#### set_nozzleclumping_detector(enabled: bool, sensitivity: DetectorSensitivity = DetectorSensitivity.MEDIUM) -> None
Enables/disables nozzle clumping/blob detector. module_name="clump_detector".

#### set_airprinting_detector(enabled: bool, sensitivity: DetectorSensitivity = DetectorSensitivity.MEDIUM) -> None
Enables/disables air-printing/no-extrusion detector. module_name="airprint_detector".

---

## AMSUnitState (dataclass)

Located at: bambu-printer-manager/src/bpm/bambustate.py
Populated from `ams.ams[]` combined with `info.module[]`. Accessible via `BambuState.ams_units`.

| Field | Type | Description |
|---|---|---|
| unit_id | int | 0-based user-facing index (0=first AMS, 1=second). NOT the same as chip_id. |
| model | AMSModel | AMS hardware model (AMS_2_PRO, AMS_HT, AMS_LITE, etc.) |
| humidity_index | int | 1=WET (alert) to 5=DRY (good). Higher = drier. 0=unavailable. Only 1–2 indicate a moisture problem. |
| temp | float | Current AMS temperature (°C) |
| temp_target | int | Dryer target temperature (°C); 0 when not drying |
| heater_state | bool | True when the dryer heater is active |
| is_drying | bool | True when dryer is running |
| tray_exist | list[bool] | Slot presence flags for all 4 slots (index 0–3) |
| assigned_to_extruder | ActiveTool enum | Which extruder this AMS unit feeds. H2D: AMS 2 Pro (unit_id 0) → RIGHT extruder (0), AMS HT (unit_id 1) → LEFT extruder (1). Set from ams_info parsing when has_dual_extruder. Critical for H2D AMS routing decisions. Single-extruder printers: always RIGHT_EXTRUDER (0). |
"""
