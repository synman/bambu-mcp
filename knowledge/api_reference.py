"""
api_reference.py — Full BambuPrinter method inventory from actual source code.

Sources read:
  ~/bambu-printer-manager/src/bpm/bambuprinter.py
  ~/bambu-printer-manager/src/bpm/bambuconfig.py
  ~/bambu-printer-manager/src/bpm/bambustate.py
  ~/bambu-printer-manager/src/bpm/bambudiscovery.py
  ~/bambu-printer-manager/src/bpm/bambuproject.py
  ~/bambu-printer-manager/src/bpm/bambuspool.py
  ~/bambu-printer-manager/src/bpm/bambutools.py
"""

API_REFERENCE_TEXT: str = """
# BambuPrinter API Reference

All method signatures sourced directly from bambuprinter.py. Do not invent
parameter names, types, or defaults — always verify from source before coding.

---

## BambuPrinter Class

Central class for all printer interaction. Located at:
  bambu-printer-manager/src/bpm/bambuprinter.py

### Constructor

```python
BambuPrinter(config: BambuConfig | None = None)
```
Sets up internal storage and bootstraps logging. If config=None, a default
BambuConfig("", "", "") is created.

---

### Session Management Methods

#### start_session() -> None
Initiates an SSL MQTT connection to the printer. Starts the watchdog thread.
Subscribes to `device/{serial}/report` on connect. Must be called before any
commands or data collection. Raises if hostname/access_code/serial_number missing
or if session already active.

#### pause_session() -> None
Unsubscribes from the /report topic, disabling telemetry. Sets ServiceState.PAUSED.

#### resume_session() -> None
Re-subscribes to /report topic. Sets ServiceState.CONNECTED.
Sets ServiceState.QUIT if client not connected or not in PAUSED state.

#### quit() -> None
Disconnects MQTT client, sets ServiceState.QUIT, notifies update callback,
joins all threads (mqtt_client_thread, watchdog_thread).

#### refresh() -> None
Publishes ANNOUNCE_VERSION and ANNOUNCE_PUSH to trigger full state refresh.
Only acts if ServiceState.CONNECTED.

---

### File Management Methods (FTPS)

#### ftp_connection() -> contextmanager[IoTFTPSClient]
Context manager. Opens FTPS connection (port 990, implicit SSL) to printer SD card.
Credentials: mqtt_username + access_code. Closes connection on exit.

#### get_sdcard_contents() -> dict | None
Returns dict of ALL files on printer SD card. Populates `_sdcard_contents` and
`_sdcard_3mf_files`. Uses FTPS via ftp_connection(). Returns None on failure.

#### get_sdcard_3mf_files() -> dict | None
Returns dict of only .3mf files. Calls get_sdcard_contents() internally.

#### delete_sdcard_file(file: str) -> dict
Deletes file at full path on SD card. Invalidates cached plate metadata for that
file. Updates `_sdcard_contents` and `_sdcard_3mf_files` in-memory. Returns
updated `_sdcard_contents`.

#### delete_sdcard_folder(path: str) -> dict
Recursively deletes folder and all contents. Invalidates all cached plate metadata
under that prefix. Returns updated `_sdcard_contents`.

#### download_sdcard_file(src: str, dest: str) -> None
Downloads file from printer (src = SD card path) to host (dest = local path).

#### upload_sdcard_file(src: str, dest: str) -> dict
Uploads local file (src) to printer (dest). If src ends with .3mf, calls
get_project_info(dest, self, local_file=src). Returns updated SD card contents.

#### rename_sdcard_file(src: str, dest: str) -> dict
Renames file on SD card via FTPS move. Returns updated SD card contents.

#### make_sdcard_directory(dir: str) -> dict
Creates directory on SD card. Returns updated SD card contents.

#### sdcard_file_exists(path: str) -> bool
Checks if file exists at path on SD card.

---

### Print Control Methods

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

#### send_gcode(gcode: str) -> None
Submits GCode commands. Multiple commands separated by \\n.
Format: `f"M104 S{value}\\n"`. Publishes SEND_GCODE_TEMPLATE.
⚠️ REQUIRES explicit user permission — Printer Write Protection applies.

#### send_anything(anything: str) -> None
Publishes arbitrary valid JSON string to `device/{serial}/request`.
Parses then re-serialises before publishing (must be valid JSON).
⚠️ REQUIRES explicit user permission — Printer Write Protection applies.

---

### Temperature Methods

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

### Fan Speed Methods

#### set_part_cooling_fan_speed_target_percent(value: int) -> None
Sets part cooling fan via GCode M106 P1. Scale: percent → 0-255 (value * 2.55).
Updates `_fan_speed_target_time`.

#### set_exhaust_fan_speed_target_percent(value: int) -> None
Sets exhaust fan via GCode M106 P3. Scale: percent → 0-255.

#### set_aux_fan_speed_target_percent(value: int) -> None  [DEPRECATED]
Sets aux fan via GCode M106 P2. Use select_extrusion_calibration_profile instead.

---

### AMS Methods

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

#### turn_on_ams_dryer(target_temp: int, duration: int, target_humidity: int = 0, cooling_temp: int = 45, rotate_tray: bool = False, ams_id: int = 0) -> None
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

### Spool Methods

#### set_spool_details(tray_id: int, tray_info_idx: str, tray_id_name: str | None = "", tray_type: str | None = "", tray_color: str | None = "", nozzle_temp_min: int | None = -1, nozzle_temp_max: int | None = -1, ams_id: int | None = 0) -> None
Sets spool/tray filament type, color, temp range. Publishes AMS_FILAMENT_SETTING.
- tray_id: absolute tray ID (ams_id * 4 + slot_id, or 254 for external)
- tray_info_idx: filament index (e.g. "GFA00"). Pass "no_filament" to clear tray.
- tray_color: CSS name or RRGGBB/RRGGBBAA hex string
- ams_id parameter is unused (derived automatically from tray_id)
- WARNING: Sends ALL fields in a single command. Empty string values ("") are
  interpreted by the printer as "clear this field". Always pass all relevant fields
  (tray_info_idx, tray_type, tray_color, nozzle_temp_min, nozzle_temp_max) together
  to avoid wiping existing slot metadata.

#### set_spool_k_factor(tray_id: int, k_value: float, n_coef: float | None = 1.399999976158142, nozzle_temp: int | None = -1, bed_temp: int | None = -1, max_volumetric_speed: int | None = -1) -> None
Sets linear advance k factor. Broken in recent Bambu firmware.
Use select_extrusion_calibration_profile instead.

#### refresh_spool_rfid(slot_id: int, ams_id: int = 0) -> None
(see AMS Methods above)

---

### Calibration Methods

#### select_extrusion_calibration_profile(tray_id: int, cali_idx: int = -1) -> None
Sets k factor profile for specified tray. Publishes EXTRUSION_CALI_SEL.
cali_idx: -1 = default profile.

---

### Hardware Control Methods

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

### X-Cam / AI Vision Detector Methods

#### set_buildplate_marker_detector(enabled: bool) -> None
Enables/disables buildplate ArUco marker scanning. Publishes XCAM_CONTROL_SET
with module_name="buildplate_marker_detector".

#### set_spaghetti_detector(enabled: bool, sensitivity: DetectorSensitivity = DetectorSensitivity.MEDIUM) -> None
Enables/disables spaghetti/failed-print detector. module_name="spaghetti_detector".

#### set_purgechutepileup_detector(enabled: bool, sensitivity: DetectorSensitivity = DetectorSensitivity.MEDIUM) -> None
Enables/disables purge-chute pile-up detector. module_name="pileup_detector".

#### set_nozzleclumping_detector(enabled: bool, sensitivity: DetectorSensitivity = DetectorSensitivity.MEDIUM) -> None
Enables/disables nozzle clumping/blob detector. module_name="clump_detector".

#### set_airprinting_detector(enabled: bool, sensitivity: DetectorSensitivity = DetectorSensitivity.MEDIUM) -> None
Enables/disables air-printing/no-extrusion detector. module_name="airprint_detector".

All xcam methods publish XCAM_CONTROL_SET with:
  control=enabled, enable=enabled, print_halt=True, halt_print_sensitivity=sensitivity.value

#### set_print_option(option: PrintOption, enabled: bool) -> None
Enables/disables a PrintOption (auto_recovery, filament_tangle_detect, etc.).
Updates corresponding BambuConfig attribute. Publishes PRINT_OPTION_COMMAND.
AUTO_RECOVERY also sets cmd["print"]["option"] = 1 if enabled else 0.

---

### Utility Methods

#### toJson() -> dict
Returns JSON-serializable dict of private instance attributes.
Handles dataclasses, objects with __dict__. Skips MQTT clients, threads.

---

### Properties (Read)

| Property                  | Type         | Description                                     |
|---------------------------|--------------|-------------------------------------------------|
| config                    | BambuConfig  | Settings used to connect/configure printer      |
| service_state             | ServiceState | Current MQTT connection state (setter triggers notify_update) |
| client                    | mqtt.Client  | Paho MQTT client instance                       |
| on_update                 | callable     | Callback executed on every state update         |
| recent_update             | bool         | Whether state was recently updated              |
| bed_temp_target_time      | int          | Timestamp of last bed temp target change        |
| tool_temp_target_time     | int          | Timestamp of last nozzle temp target change     |
| chamber_temp_target_time  | int          | Timestamp of last chamber temp target change    |
| fan_speed_target_time     | int          | Timestamp of last fan speed target change       |
| printer_state             | BambuState   | Current telemetry state                         |
| active_job_info           | ActiveJobInfo| Details of current/last active job             |
| internalException         | Exception    | Last captured communication error              |
| cached_sd_card_contents   | dict | None  | All files on SD card (cached)                   |
| cached_sd_card_3mf_files  | dict | None  | Only .3mf files on SD card (cached)             |

### Properties with Setters

| Property    | Setter behavior                                               |
|-------------|---------------------------------------------------------------|
| light_state | bool → publishes CHAMBER_LIGHT_TOGGLE for chamber_light, chamber_light2, column_light |
| speed_level | str → publishes SPEED_PROFILE_TEMPLATE with param=value      |
| config      | Direct assignment                                             |
| service_state | Assignment + calls _notify_update()                         |

### Deprecated Properties

| Property       | Replacement                                       |
|----------------|---------------------------------------------------|
| skipped_objects| No replacement yet (v1.0.0)                      |
| nozzle_diameter| printer_state.active_nozzle.diameter_mm          |
| nozzle_type    | printer_state.active_nozzle.material             |

---

## BambuConfig (dataclass)

Located at: bambu-printer-manager/src/bpm/bambuconfig.py

### Required Parameters (constructor)

| Parameter     | Type | Description                                     |
|---------------|------|-------------------------------------------------|
| hostname      | str  | IP address or DNS name of printer               |
| access_code   | str  | 8-character LAN-only access code                |
| serial_number | str  | Hardware serial (determines printer_model)      |

### Optional Parameters (with defaults)

| Parameter         | Type                | Default                  | Description                     |
|-------------------|---------------------|--------------------------|---------------------------------|
| mqtt_port         | int                 | 8883                     | SSL MQTT broker port            |
| mqtt_client_id    | str                 | "studio_client_id:0c1f"  | MQTT handshake ID               |
| mqtt_username     | str                 | "bblp"                   | MQTT auth username              |
| watchdog_timeout  | int                 | 30                       | Seconds before connection flagged stale |
| external_chamber  | bool                | False                    | Ignore internal CTC for manual injection |
| capabilities      | PrinterCapabilities | PrinterCapabilities()    | Hardware feature set            |
| bpm_cache_path    | Path | None          | None → ~/.bpm                    | Cache directory                 |
| printer_model     | PrinterModel        | PrinterModel.UNKNOWN     | Auto-set from serial in __post_init__ |
| firmware_version  | str                 | ""                       | Main firmware version string    |
| ams_firmware_version | str              | ""                       | AMS controller firmware version |
| auto_recovery     | bool                | False                    | Sourced from home_flag bit 4    |
| filament_tangle_detect | bool           | False                    | Sourced from home_flag bit 20   |
| sound_enable      | bool                | False                    | Sourced from home_flag bit 17   |
| auto_switch_filament | bool             | False                    | Sourced from home_flag bit 10   |
| nozzle_blob_detect | bool               | False                    | Sourced from home_flag bit 24   |
| air_print_detect  | bool                | False                    | Sourced from home_flag bit 28   |
| spaghetti_detector | bool               | False                    | Sourced from xcam.cfg bit 7     |
| spaghetti_detector_sensitivity | str    | "medium"                 | low/medium/high                 |
| purgechutepileup_detector | bool       | False                    | Sourced from xcam.cfg bit 10    |
| nozzleclumping_detector | bool         | False                    | Sourced from xcam.cfg bit 13    |
| airprinting_detector | bool            | False                    | Sourced from xcam.cfg bit 16    |
| buildplate_marker_detector | bool     | False                    | Sourced from xcam.buildplate_marker_detector |
| verbose           | bool                | False                    | Enables DEBUG MQTT message logging |

### __post_init__
Sets `printer_model = getPrinterModelBySerial(serial_number)`.
Creates default bpm_cache_path (~/.bpm) and metadata subdirectory.

### Methods
`set_new_bpm_cache_path(path: Path)` — Changes cache directory at runtime.

---

## PrinterCapabilities (dataclass)

Nested in BambuConfig.capabilities. All fields default False.

Key capabilities (auto-discovered from telemetry):

| Field                                | Telemetry source                              |
|--------------------------------------|-----------------------------------------------|
| has_ams                              | "ams" key in ams_root or p                    |
| has_lidar                            | xcam.first_layer_inspector                    |
| has_camera                           | Always True (hardcoded)                       |
| has_dual_extruder                    | extruder.info array length > 1               |
| has_air_filtration                   | device.airduct block present                  |
| has_chamber_temp                     | device.ctc block present                      |
| has_chamber_door_sensor              | fun bit 12                                    |
| has_sound_enable_support             | home_flag bit 18                              |
| has_auto_recovery_support            | explicit support telemetry keys               |
| has_auto_switch_filament_support     | explicit support telemetry keys               |
| has_filament_tangle_detect_support   | home_flag bit 19                              |
| has_nozzle_blob_detect_support       | home_flag bit 25                              |
| has_air_print_detect_support         | home_flag bit 29                              |
| has_buildplate_marker_detector_support| xcam.buildplate_marker_detector present      |
| has_spaghetti_detector_support       | fun bit 42 OR xcam.spaghetti_detector present |
| has_purgechutepileup_detector_support| fun bit 43 OR xcam.pileup_detector present    |
| has_nozzleclumping_detector_support  | fun bit 44 OR xcam.clump_detector present     |
| has_airprinting_detector_support     | fun bit 45 OR xcam.airprint_detector present  |

---

## BambuState (dataclass, frozen via replace())

Located at: bambu-printer-manager/src/bpm/bambustate.py

Populated via `BambuState.fromJson(data, printer)` — parses root MQTT payloads.

Key fields:

| Field                   | Type                 | Telemetry source              |
|-------------------------|----------------------|-------------------------------|
| gcode_state             | str                  | print.gcode_state ("IDLE", "RUNNING", "PAUSED", "FINISH", "FAILED", "PREPARE") |
| active_ams_id           | int                  | Computed from active_tray_id  |
| active_tray_id          | int                  | extruder.active_tray_id or ams.tray_now |
| active_tray_state       | TrayState            | Computed from extruder state/status |
| active_tray_state_name  | str                  | active_tray_state.name        |
| target_tray_id          | int                  | extruder.target_tray_id       |
| active_tool             | ActiveTool           | extruder.state bits 4-7       |
| is_external_spool_active| bool                 | active_tray_id in [254, 255]  |
| active_nozzle_temp      | float                | extruder.temp (actual)        |
| active_nozzle_temp_target| int                 | extruder.temp (target)        |
| active_nozzle           | NozzleCharacteristics| from extruder nozzle block    |
| ams_status_raw          | int                  | print.ams_status              |
| ams_status_text         | str                  | parseAMSStatus(ams_status_raw)|
| ams_exist_bits          | int                  | ams.ams_exist_bits            |
| ams_connected_count     | int                  | popcount(ams_exist_bits)      |
| ams_units               | list[AMSUnitState]   | ams.ams[] + info.module[]     |
| extruders               | list[ExtruderState]  | device.extruder.info[]        |
| spools                  | list[BambuSpool]     | ams.ams[].tray[] + vt_tray + vir_slot |
| print_error             | int                  | print.print_error             |
| hms_errors              | list[dict]           | print.hms                     |
| wifi_signal_strength    | str                  | print.wifi_signal             |
| climate                 | BambuClimate         | Multiple telemetry sources    |

BambuClimate key fields:

| Field                        | Telemetry source                       |
|------------------------------|----------------------------------------|
| bed_temp                     | print.bed_temper                       |
| bed_temp_target              | print.bed_target_temper                |
| chamber_temp                 | device.ctc (H2D) or print.chamber_temper |
| chamber_temp_target          | device.ctc.info.temp (packed)          |
| air_conditioning_mode        | device.airduct.modeCur                 |
| part_cooling_fan_speed_percent| print.cooling_fan_speed OR zone_part_fan |
| aux_fan_speed_percent        | print.big_fan1_speed OR zone_aux       |
| exhaust_fan_speed_percent    | print.big_fan2_speed OR zone_exhaust   |
| heatbreak_fan_speed_percent  | print.heatbreak_fan_speed              |
| is_chamber_door_open         | stat bit 23 (if has_chamber_door_sensor) |
| is_chamber_lid_open          | stat bit 24 (if has_chamber_door_sensor) |
| zone_top_vent_open           | zone_exhaust_percent > 0 AND NOT zone_intake_open |

Fan speed scaling: raw 0-15 → percent (scaleFanSpeed): `round((val/15.0)*100)`.

---

## BambuDiscovery / DiscoveredPrinter

Located at: bambu-printer-manager/src/bpm/bambudiscovery.py

### BambuDiscovery
```python
BambuDiscovery(
    on_printer_discovered=None,   # callable(DiscoveredPrinter) — new printer found
    on_discovery_ended=None,      # callable(dict) — dict of {usn: DiscoveredPrinter}
    discovery_timeout: int = 15   # seconds before auto-stopping
)
```
Methods: `start()`, `stop()`
Properties: `discovered_printers` (dict keyed by USN), `running` (bool)
Binds UDP socket to port 2021. Deduplicates by USN.

### DiscoveredPrinter (dataclass)
Fields: usn, host, server, location (IP), nt, nts, cache_control,
dev_model, decoded_model (PrinterModel), dev_name, dev_connect, dev_bind,
dev_seclink, dev_inf, dev_version, dev_cap.

Class method: `DiscoveredPrinter.fromData(data: str)` — parses raw SSDP UDP string.

---

## BambuSpool (dataclass)

Located at: bambu-printer-manager/src/bpm/bambuspool.py

```python
BambuSpool(
    id: int,                # 0-23 for AMS slots, 254-255 for external spools
    name: str = "",         # Filament name (from RFID or printer display)
    type: str = "",         # Filament type (PLA, PETG, ABS, etc.)
    sub_brands: str = "",   # Bambu Lab specialization (Matte, Pro, Tough, etc.)
    color: str = "",        # CSS color name or hex code
    tray_info_idx: str = "",# Bambu Studio filament index (e.g. "GFA00")
    k: float = 0.0,         # Linear advance k factor
    bed_temp: int = 0,      # Target bed temp
    nozzle_temp_min: int = 0,
    nozzle_temp_max: int = 0,
    drying_temp: int = 0,
    drying_time: int = 0,
    remaining_percent: int = 0,  # -1 if unknown
    state: int = 0,
    total_length: int = 0,  # Total filament length in mm
    tray_weight: int = 0,   # Spool weight in grams
    slot_id: int = -1,      # Slot within AMS (0-3) or external tray ID (254-255)
    ams_id: int = -1,       # AMS unit ID (-1 = no AMS)
)
```

---

## ProjectInfo (dataclass)

Located at: bambu-printer-manager/src/bpm/bambuproject.py

```python
ProjectInfo(
    id: str = "",           # Storage location (SD card path)
    name: str = "",         # Filename portion
    size: int = 0,
    timestamp: int = 0,
    md5: str = "",
    plate_num: int = 1,
    metadata: dict = {},    # See below
    plates: list[int] = []  # Available plate numbers
)
```

metadata keys:
- `thumbnail`: `data:image/png;base64,...` from plate_N.png
- `topimg`: `data:image/png;base64,...` from top_N.png
- `map`: full plate_N.json including filament_ids, filament_colors, bbox_objects
  (each enriched with `"id"` = identify_id from slice_info.config)
- `filament`: list of `{"id": int, "type": str, "color": "#RRGGBB"}` (1-indexed)
- `ams_mapping`: list of stringified absolute tray IDs for print_3mf_file()

Function: `get_project_info(file_id, printer, md5=None, plate_num=1, local_file=None) -> ProjectInfo`
Function: `get_3mf_entry_by_name(remote_files, name) -> dict | None`

---

## ActiveJobInfo (dataclass)

Located at: bambu-printer-manager/src/bpm/bambuproject.py

Accessible via `BambuPrinter.active_job_info`.

| Field                      | Type          | Source                                   |
|----------------------------|---------------|------------------------------------------|
| project_info               | ProjectInfo   | Parsed from project_file command or SD card |
| project_file_command       | dict          | Raw project_file command message         |
| stage_id                   | int           | print.stg_cur                            |
| stage_name                 | str           | parseStage(stage_id)                     |
| current_layer              | int           | print.layer_num                          |
| total_layers               | int           | print.total_layer_num                    |
| print_percentage           | int           | print.mc_percent                         |
| elapsed_minutes            | int           | Computed from monotonic_start_time       |
| remaining_minutes          | int           | print.mc_remaining_time                  |
| monotonic_start_time       | float         | time.monotonic() on RUNNING start        |
| subtask_name               | str           | print.subtask_name                       |
| gcode_file                 | str           | print.gcode_file                         |

---

## Utility Functions (bambutools.py)

| Function                        | Signature                                              | Returns          |
|---------------------------------|--------------------------------------------------------|------------------|
| getPrinterModelBySerial         | (serial: str)                                          | PrinterModel     |
| getPrinterSeriesByModel         | (model: PrinterModel)                                  | PrinterSeries    |
| getAMSModelBySerial             | (serial: str)                                          | AMSModel         |
| getAMSSeriesByModel             | (model: AMSModel)                                      | AMSSeries        |
| parseStage                      | (stage_int: int)                                       | str              |
| parseAMSInfo                    | (info_hex: str)                                        | dict             |
| parseAMSStatus                  | (status_int: int)                                      | str              |
| parseExtruderInfo               | (info_int: int)                                        | ExtruderInfoState|
| parseExtruderStatus             | (stat_int: int)                                        | ExtruderStatus   |
| parseRFIDStatus                 | (status)                                               | str              |
| decodeError                     | (error: int)                                           | dict             |
| decodeHMS                       | (hms_list: list)                                       | list[dict]       |
| getAMSHeatingState              | (ams_info: int)                                        | AMSHeatingState  |
| scaleFanSpeed                   | (raw_val: Any) [0-15 → 0-100%]                         | int              |
| unpackTemperature               | (raw_temp: int) [32-bit packed]                        | tuple[float,float]|
| parse_nozzle_identifier         | (nozzle_id: str) [e.g. "HS00-0.4"]                    | tuple[NozzleFlowType, NozzleType, str] |
| parse_nozzle_type               | (value: str | None)                                    | NozzleType       |
| nozzle_type_to_telemetry        | (value: NozzleType)                                    | str              |
| build_nozzle_identifier         | (flow_type, nozzle_type, diameter)                     | str              |
| sortFileTreeAlphabetically      | (source: dict)                                         | dict             |
| get_file_md5                    | (file_path: str | Path)                                | str              |
| jsonSerializer                  | (obj: Any) [for json.dumps default=]                   | Any              |
| parseExtruderTrayState          | (extruder: int, hotend, slot)                          | int              |
"""
