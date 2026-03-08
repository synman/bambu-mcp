"""
protocol_3mf.py — SSDP discovery, AMS info parsing, 3MF structure, FTPS, extruder block.

Sub-topic of protocol. Access via get_knowledge_topic('protocol/3mf').
"""

from __future__ import annotations

PROTOCOL_3MF_TEXT: str = """
# Bambu Lab Protocol — SSDP, AMS Info, 3MF Structure, FTPS & Extruder Block

---

## SSDP Discovery (UDP port 2021)

Printers broadcast SSDP UDP packets on port 2021 (not standard 1900).
Bind with `socket.bind(("", 2021))`.

Key fields parsed from raw UDP SSDP data:

| SSDP Header Key | DiscoveredPrinter field | Description |
|---|---|---|
| `USN` | `usn` | Serial number (deduplication key) |
| `LOCATION` | `location` | Printer IP address |
| `devname.bambu.com` | `dev_name` | Printer display name |
| `devversion.bambu.com` | `dev_version` | Firmware version string |
| `devbind.bambu.com` | `dev_bind` | Binding status (e.g. "free") |
| `devconnect.bambu.com` | `dev_connect` | Connection type (e.g. "lan") |
| `devmodel.bambu.com` | `dev_model` | Hardware model string |
| `devseclink.bambu.com` | `dev_seclink` | Security link status |
| `devcap.bambu.com` | `dev_cap` | Device capabilities flag |

`DiscoveredPrinter.fromData(data_str)` parses raw UDP payload strings.
`BambuDiscovery` deduplicates by USN.

---

## AMS Info Parsing (ams_info hex field)

The `ams_info` hex string in the `ams` block is a 32-bit integer with bit fields:

| Bits | Field | Notes |
|---|---|---|
| 0-3 | AMS type | 1=AMS_1, 2=AMS_LITE, 3=AMS_2_PRO, 4=AMS_HT |
| 4-7 | Dry status | AMSHeatingState enum |
| 8-11 | Extruder ID | H2D toolhead assignment |
| 18-19 | Dry fan 1 status | AMSDryFanStatus (OFF=0, ON=1) |
| 20-21 | Dry fan 2 status | AMSDryFanStatus (OFF=0, ON=1) |
| 22-25 | Dry sub-status | AMSDrySubStatus enum |

Parse with: `parseAMSInfo(ams_u["info"])` from bambutools.py.

### tray_exist_bits
Hex-encoded bitmask of which slots have filament:
- Standard AMS (id 0-3): bit shift = `4 * id`, slots 0-3 (4 bits per unit)
- AMS HT (id 128+): bit shift = `16 + 4 * (id - 128)`, 1 slot per unit

---

## 3MF Structure

A `.3mf` file is a ZIP archive. Key internal entries:

| ZIP Entry | Purpose |
|---|---|
| `Metadata/slice_info.config` | XML: objects, filament IDs, colors, identify_ids, filament_maps |
| `Metadata/project_settings.config` | INI: filament types and colors (fallback) |
| `Metadata/plate_N.json` | JSON: bbox_objects, filament_ids, filament_colors per plate |
| `Metadata/plate_N.png` | PNG: slicer preview thumbnail |
| `Metadata/top_N.png` | PNG: top-down view thumbnail |
| `Metadata/plate_N.gcode` | G-code header: filament type/color (fallback) |

### ams_mapping encoding (BambuStudio/OrcaSlicer DevMapping.cpp)

JSON integer array — one absolute tray ID per filament slot:

| Value | Meaning |
|---|---|
| 0-103 | Standard 4-slot AMS: `ams_id * 4 + slot_id` |
| 128-135 | Single-slot AMS HT / N3S: `ams_id` (starts at 128) |
| 254 | External spool |
| -1 | Unmapped / no AMS |

`ams_mapping2` = per-filament `{"ams_id": int, "slot_id": int}` dicts,
auto-generated from `ams_mapping` for firmware compatibility.

### filament_colors
`plate_N.json` contains `filament_colors` as list of `#RRGGBB` strings.

### bbox_objects
`plate_N.json` contains `bbox_objects` list. Each enriched by `get_project_info`
with `"id"` = integer `identify_id` from `slice_info.config`. These IDs are
passed to `BambuPrinter.skip_objects()` to cancel individual objects.

### plate thumbnails
`plate_N.png` → `data:image/png;base64,...` URI in `metadata["thumbnail"]`.
`top_N.png` → `data:image/png;base64,...` URI in `metadata["topimg"]`.

### Bed dimensions by printer model

| Model(s) | Width (X) mm | Height (Y) mm |
|---|---|---|
| H2D, H2S | 350 | 320 |
| X1C, X1, X1E, P1S, P1P, P2S, A1 | 256 | 256 |
| A1 Mini | 180 | 180 |

Coordinate mapping (slicer mm → image pixels):
- Slicer origin: bottom-left (0,0). Image origin: top-left.
- scale = min(img_w / bed_w, img_h / bed_h)
- x_off = (img_w - bed_w * scale) / 2
- y_off = (img_h - bed_h * scale) / 2
- pixel_x = x_off + x_mm * scale
- pixel_y = img_h - y_off - y_mm * scale  ← Y flip

---

## trigger_printer_refresh

Forces re-query by publishing ANNOUNCE_VERSION and ANNOUNCE_PUSH to the printer's
MQTT request topic. Must be called after a printer reboot (firmware upgrade, etc.)
before reading `firmware_version` — the cached value is stale until refreshed.

---

## FTPS File Operations

Port 990, implicit SSL. Credentials: username=`bblp`, password=access_code.
Used via `IoTFTPSClient` / `BambuPrinter.ftp_connection()` context manager.
Operations: `upload_file`, `download_file`, `delete_file`, `move_file`,
`list_files_ex`, `mkdir`, `delete_folder`, `fexists`.

URL formats:
- A1/P1 series: `file:///sdcard{path}`
- X1/H2D series: `ftp://{path}`

---

## Extruder Device Block (H2D dual-extruder)

Telemetry path: `print.device.extruder`

`extruder.info[]` — array of extruder entries:
- `id`: extruder physical ID (0=right, 1=left on H2D)
- `temp`: packed 32-bit temperature (unpack with `unpackTemperature()`)
- `info`: bitmask (bit 3=nozzle present, bit 1=loaded, bit 2=buffer loaded)
- `stat`: operational state bitmask
- `snow`/`hnow`: active tray slot / hotend slot IDs
- `star`/`htar`: target tray / hotend IDs

`extruder.state`: active tool — bits 4-7 = raw tool index.
`ActiveTool(raw_t_idx)` → RIGHT_EXTRUDER(0), LEFT_EXTRUDER(1), or NOT_ACTIVE(15).

`device.nozzle.info[]` — per-nozzle characteristics:
- `id`: nozzle ID (matched to extruder via hnow & 0xFF)
- `type`: nozzle material string (e.g. "hardened_steel", "HS01-0.4")
- `diameter`: float in mm

`device.ctc` — Chamber Thermal Controller:
- `info.temp`: packed 32-bit actual+target chamber temperature

`device.airduct` — Airduct/HVAC block:
- `modeCur`: 0=cool, 1=heat, -1=not supported
- `parts[]`: zone fan states — id 16=part fan, 32=aux, 48=exhaust, 96=intake
"""
