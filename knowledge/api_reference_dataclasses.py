"""
api_reference_dataclasses.py — BambuSpool, ProjectInfo, ActiveJobInfo, utility functions.

Sub-topic of api_reference. Access via get_knowledge_topic('api_reference/dataclasses').
"""

from __future__ import annotations

API_REFERENCE_DATACLASSES_TEXT: str = """
# BambuPrinter API — BambuSpool, ProjectInfo, ActiveJobInfo & Utilities

---

## BambuSpool (dataclass)

Located at: bambu-printer-manager/src/bpm/bambuspool.py

```python
BambuSpool(
    id: int,                # 0-23 for AMS slots, 254-255 for external spools
    name: str = "",         # Bambu Lab vendor-specific brand label (e.g. "Bambu PLA Basic", "B00-K0").
                            # Optional and unreliable as an identifier — not present on third-party spools.
                            # The true identity of a spool is color + tray_info_idx (base profile).
    type: str = "",         # Filament type (PLA, PETG, ABS, etc.)
    sub_brands: str = "",   # Bambu Lab specialization (Matte, Pro, Tough, etc.)
    color: str = "",        # CSS color name or hex code — primary identity component
    tray_info_idx: str = "",# Bambu filament catalog code (e.g. "GFA00") — primary identity component.
                            # Encodes base profile: temp range, drying params, flow characteristics.
    k: float = 0.0,         # Linear advance k factor
    bed_temp: int = 0,      # Target bed temp
    nozzle_temp_min: int = 0,
    nozzle_temp_max: int = 0,
    drying_temp: int = 0,
    drying_time: int = 0,
    remaining_percent: int = 0,  # -1 if unknown
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

Functions:
- `get_project_info(file_id, printer, md5=None, plate_num=1, local_file=None) -> ProjectInfo`
- `get_3mf_entry_by_name(remote_files, name) -> dict | None`

---

## ActiveJobInfo (dataclass)

Located at: bambu-printer-manager/src/bpm/bambuproject.py
Accessible via `BambuPrinter.active_job_info`.

| Field | Type | Source |
|---|---|---|
| project_info | ProjectInfo | Parsed from SD card |
| project_file_command | dict | Raw project_file command message |
| stage_id | int | print.stg_cur |
| stage_name | str | parseStage(stage_id) |
| current_layer | int | print.layer_num |
| total_layers | int | print.total_layer_num |
| print_percentage | int | print.mc_percent |
| elapsed_minutes | int | Computed from monotonic_start_time |
| remaining_minutes | int | print.mc_remaining_time |
| subtask_name | str | print.subtask_name |
| gcode_file | str | print.gcode_file |

---

## BambuDiscovery / DiscoveredPrinter

Located at: bambu-printer-manager/src/bpm/bambudiscovery.py

### BambuDiscovery
```python
BambuDiscovery(
    on_printer_discovered=None,   # callable(DiscoveredPrinter)
    on_discovery_ended=None,      # callable(dict)
    discovery_timeout: int = 15
)
```
Methods: `start()`, `stop()`
Properties: `discovered_printers` (dict keyed by USN), `running` (bool)
Binds UDP socket to port 2021. Deduplicates by USN.

### DiscoveredPrinter (dataclass)
Class method: `DiscoveredPrinter.fromData(data: str)` — parses raw SSDP UDP string.
Key fields: usn, host, location (IP), dev_model, decoded_model (PrinterModel),
dev_name, dev_connect, dev_bind, dev_version, dev_seclink, dev_cap.

---

## Utility Functions (bambutools.py)

| Function | Signature | Returns |
|---|---|---|
| getPrinterModelBySerial | (serial: str) | PrinterModel |
| getPrinterSeriesByModel | (model: PrinterModel) | PrinterSeries |
| getAMSModelBySerial | (serial: str) | AMSModel |
| getAMSSeriesByModel | (model: AMSModel) | AMSSeries |
| parseStage | (stage_int: int) | str |
| parseAMSInfo | (info_hex: str) | dict |
| parseAMSStatus | (status_int: int) | str |
| parseExtruderInfo | (info_int: int) | ExtruderInfoState |
| parseExtruderStatus | (stat_int: int) | ExtruderStatus |
| parseRFIDStatus | (status) | str |
| decodeError | (error: int) | dict |
| decodeHMS | (hms_list: list) | list[dict] |
| getAMSHeatingState | (ams_info: int) | AMSHeatingState |
| scaleFanSpeed | (raw_val) [0-15 → 0-100%] | int |
| unpackTemperature | (raw_temp: int) [32-bit packed] | tuple[float,float] |
| parse_nozzle_identifier | (nozzle_id: str) [e.g. "HS00-0.4"] | tuple[NozzleFlowType, NozzleType, str] |
| parse_nozzle_type | (value: str or None) | NozzleType |
| nozzle_type_to_telemetry | (value: NozzleType) | str |
| build_nozzle_identifier | (flow_type, nozzle_type, diameter) | str |
| sortFileTreeAlphabetically | (source: dict) | dict |
| get_file_md5 | (file_path: str or Path) | str |
| jsonSerializer | (obj: Any) [for json.dumps default=] | Any |
| parseExtruderTrayState | (extruder: int, hotend, slot) | int |

For `JobStateReport` (camera analysis result) and background monitor result dict fields,
see `get_knowledge_topic('api_reference/camera')`.
"""
