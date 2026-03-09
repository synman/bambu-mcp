"""
api_reference_state.py — BambuPrinter properties, BambuConfig, PrinterCapabilities, BambuState.

Sub-topic of api_reference. Access via get_knowledge_topic('api_reference/state').
For BambuSpool, ProjectInfo, ActiveJobInfo, and utility functions, see:
  get_knowledge_topic('api_reference/dataclasses')
"""

from __future__ import annotations

API_REFERENCE_STATE_TEXT: str = """
# BambuPrinter API — Properties, BambuConfig & BambuState

---

## set_print_option(option: PrintOption, enabled: bool) -> None
Enables/disables a PrintOption. Updates corresponding BambuConfig attribute.
Publishes PRINT_OPTION_COMMAND. AUTO_RECOVERY also sets cmd["print"]["option"] = 1/0.

## toJson() -> dict
Returns JSON-serializable dict of private instance attributes.
Handles dataclasses, objects with __dict__. Skips MQTT clients, threads.

---

## Properties (Read)

| Property | Type | Description |
|---|---|---|
| config | BambuConfig | Connection/config settings |
| service_state | ServiceState | Current MQTT connection state |
| printer_state | BambuState | Current telemetry state |
| active_job_info | ActiveJobInfo | Details of current/last active job |
| bed_temp_target_time | int | Timestamp of last bed temp target change |
| tool_temp_target_time | int | Timestamp of last nozzle temp target change |
| chamber_temp_target_time | int | Timestamp of last chamber temp target change |
| fan_speed_target_time | int | Timestamp of last fan speed target change |
| cached_sd_card_contents | dict or None | All files on SD card (cached) |
| cached_sd_card_3mf_files | dict or None | Only .3mf files on SD card (cached) |

## Properties with Setters

| Property | Setter behavior |
|---|---|
| light_state | bool → publishes CHAMBER_LIGHT_TOGGLE for all light nodes |
| speed_level | str → publishes SPEED_PROFILE_TEMPLATE with param=value |
| service_state | Assignment + calls _notify_update() |

## Deprecated Properties

| Property | Replacement |
|---|---|
| nozzle_diameter | printer_state.active_nozzle.diameter_mm |
| nozzle_type | printer_state.active_nozzle.material |

---

## BambuConfig (dataclass)

Located at: bambu-printer-manager/src/bpm/bambuconfig.py

### Required Parameters

| Parameter | Type | Description |
|---|---|---|
| hostname | str | IP address or DNS name |
| access_code | str | 8-character LAN-only access code |
| serial_number | str | Hardware serial (determines printer_model) |

### Optional Parameters

| Parameter | Default | Source / Notes |
|---|---|---|
| mqtt_port | 8883 | SSL MQTT broker port |
| mqtt_username | "bblp" | MQTT auth username |
| watchdog_timeout | 30 | Seconds before connection flagged stale |
| capabilities | PrinterCapabilities() | Auto-discovered from telemetry |
| bpm_cache_path | ~/.bpm | Cache directory for metadata |
| printer_model | PrinterModel.UNKNOWN | Auto-set from serial in __post_init__ |
| firmware_version | "" | Main firmware version string |
| ams_firmware_version | "" | AMS controller firmware version |
| auto_recovery | False | home_flag bit 4 | Resume print automatically after power loss or hardware fault. |
| filament_tangle_detect | False | home_flag bit 20 | Pause if AMS sensors detect a filament tangle. AMS-only; no effect during external spool prints. Guarded by has_filament_tangle_detect_support. |
| sound_enable | False | home_flag bit 17 | Enable audible beep notifications. Guarded by has_sound_enable_support. |
| auto_switch_filament | False | home_flag bit 10 | Auto-switch to another AMS slot when active spool runs out, if same type+color available. AMS-hosted spools only. |
| nozzle_blob_detect | False | home_flag bit 24 | Legacy firmware-level blob detection flag. Prefer nozzleclumping_detector (xcam) on supported printers — it offers sensitivity control. Guarded by has_nozzle_blob_detect_support. |
| air_print_detect | False | home_flag bit 28 | Legacy firmware-level air-printing detection flag. Prefer airprinting_detector (xcam) on supported printers — it offers sensitivity control. Guarded by has_air_print_detect_support. |
| spaghetti_detector | False | xcam.cfg bit 7 | X-Cam AI: detects loose spaghetti strands from a delaminated/detached print. Guarded by has_spaghetti_detector_support. |
| spaghetti_detector_sensitivity | "medium" | low/medium/high | Sensitivity for spaghetti_detector. Low = fewer false positives; high = catches subtle failures earlier. |
| purgechutepileup_detector | False | xcam.cfg bit 10 | X-Cam AI: detects purge waste pile-up in chute. Primarily relevant during multi-color prints. Guarded by has_purgechutepileup_detector_support. |
| nozzleclumping_detector | False | xcam.cfg bit 13 | X-Cam AI: newer/preferred path for nozzle blob detection; supersedes nozzle_blob_detect on supported printers. Guarded by has_nozzleclumping_detector_support. |
| nozzleclumping_detector_sensitivity | "medium" | low/medium/high | Sensitivity for nozzleclumping_detector. |
| airprinting_detector | False | xcam.cfg bit 16 | X-Cam AI: newer/preferred path for air-printing detection; supersedes air_print_detect on supported printers. Guarded by has_airprinting_detector_support. |
| airprinting_detector_sensitivity | "medium" | low/medium/high | Sensitivity for airprinting_detector. |
| buildplate_marker_detector | False | xcam.buildplate_marker_detector | X-Cam AI: verifies build plate type via ArUco markers before print starts. No sensitivity control. Guarded by has_buildplate_marker_detector_support. |

**⚠️ Live response field mapping — home_flag is NOT decoded by name:**
In the raw response from `get_printer_state()`, `home_flag` arrives as the field **`fun`** (a hex
string, e.g. `"4027FF18FFF9CB3"`). The PrintOption boolean fields listed above (auto_recovery,
filament_tangle_detect, sound_enable, auto_switch_filament, nozzle_blob_detect, air_print_detect)
are **NOT pre-decoded** into named keys — they must be derived in-session:

```python
home_flag = int(state["fun"], 16)
auto_switch_filament = bool(home_flag & (1 << 10))   # bit 10
auto_recovery        = bool(home_flag & (1 << 4))    # bit 4
```

See `protocol/mqtt` knowledge sub-topic for the full home_flag bit table.

__post_init__: sets printer_model via getPrinterModelBySerial(serial_number).

---

## PrinterCapabilities (dataclass)

Nested in BambuConfig.capabilities. All fields default False.
Auto-discovered from telemetry on connection.

| Field | Telemetry source | Description |
|---|---|---|
| has_ams | "ams" key in push_status | At least one AMS unit is connected. |
| has_lidar | xcam.first_layer_inspector | Printer has LiDAR for first-layer inspection (X1/H2D series). Required for set_first_layer_inspection() to have any effect. |
| has_camera | Always True (hardcoded) | Printer has an onboard camera. |
| has_dual_extruder | extruder.info array length > 1 | Printer has two independent extruders (H2D only). |
| has_chamber_temp | device.ctc block present | Printer has a chamber temperature sensor. |
| has_chamber_door_sensor | fun bit 12 | Printer has a chamber door/lid open sensor. |
| has_auto_recovery_support | Always True | auto_recovery PrintOption is supported. No home_flag support bit exists; all printers support step-loss detection. Value at home_flag bit 4. |
| has_auto_switch_filament_support | True if has_ams | auto_switch_filament PrintOption is supported. No home_flag support bit exists; requires AMS (AMS-hosted spools only). Value at home_flag bit 10. |
| has_sound_enable_support | home_flag bit 18 | sound_enable PrintOption is supported. |
| has_filament_tangle_detect_support | home_flag bit 19 | filament_tangle_detect PrintOption is supported. |
| has_nozzle_blob_detect_support | home_flag bit 25 | Legacy nozzle_blob_detect PrintOption is supported. |
| has_air_print_detect_support | home_flag bit 29 | Legacy air_print_detect PrintOption is supported. |
| has_spaghetti_detector_support | fun bit 42 OR xcam.spaghetti_detector | xcam spaghetti_detector is supported. |
| has_purgechutepileup_detector_support | fun bit 43 OR xcam.pileup_detector | xcam purgechutepileup_detector is supported. |
| has_nozzleclumping_detector_support | fun bit 44 OR xcam.clump_detector | xcam nozzleclumping_detector is supported (preferred over nozzle_blob_detect). |
| has_airprinting_detector_support | fun bit 45 OR xcam.airprint_detector | xcam airprinting_detector is supported (preferred over air_print_detect). |
| has_buildplate_marker_detector_support | xcam.buildplate_marker_detector present | xcam buildplate_marker_detector is supported. |

---

## BambuState (dataclass, frozen via replace())

Located at: bambu-printer-manager/src/bpm/bambustate.py
Populated via `BambuState.fromJson(data, printer)`.

Key fields:

| Field | Type | Telemetry source |
|---|---|---|
| gcode_state | str | print.gcode_state (IDLE/PREPARE/RUNNING/PAUSE/FINISH/FAILED) |
| active_ams_id | int | Computed from active_tray_id |
| active_tray_id | int | extruder.active_tray_id or ams.tray_now |
| active_tray_state | TrayState | Computed from extruder state/status |
| active_tool | ActiveTool | extruder.state bits 4-7 |
| is_external_spool_active | bool | active_tray_id in [254, 255] |
| active_nozzle_temp | float | extruder.temp (actual) |
| active_nozzle_temp_target | int | extruder.temp (target) |
| active_nozzle | NozzleCharacteristics | from extruder nozzle block |
| ams_units | list[AMSUnitState] | ams.ams[] + info.module[] |
| extruders | list[ExtruderState] | device.extruder.info[] |
| spools | list[BambuSpool] | ams tray[] + vt_tray + vir_slot |
| print_error | int | print.print_error |
| hms_errors | list[dict] | print.hms |
| climate | BambuClimate | Multiple telemetry sources |

BambuClimate key fields:

| Field | Telemetry source |
|---|---|
| bed_temp | print.bed_temper |
| bed_temp_target | print.bed_target_temper |
| chamber_temp | device.ctc (H2D) or print.chamber_temper |
| chamber_temp_target | device.ctc.info.temp (packed 32-bit) |
| part_cooling_fan_speed_percent | print.cooling_fan_speed OR zone_part_fan |
| aux_fan_speed_percent | print.big_fan1_speed OR zone_aux |
| exhaust_fan_speed_percent | print.big_fan2_speed OR zone_exhaust |
| is_chamber_door_open | stat bit 23 (if has_chamber_door_sensor) |
| air_conditioning_mode | device.airduct.modeCur |

Fan speed scaling: raw 0-15 → percent: `round((val/15.0)*100)`.

---

## Related sub-topic

BambuSpool, ProjectInfo, ActiveJobInfo dataclasses and utility functions:
→ `get_knowledge_topic('api_reference/dataclasses')`
"""
