"""
protocol.py — Bambu Lab MQTT/FTPS protocol knowledge for the bambu-mcp agent.

Sourced from:
  ~/bambu-printer-manager/src/bpm/bambuprinter.py
  ~/bambu-printer-manager/src/bpm/bambustate.py
  ~/bambu-printer-app/.github/copilot-instructions.md
  ~/bambu-printer-manager/.github/copilot-instructions.md
"""

PROTOCOL_TEXT: str = """
# Bambu Lab Printer Protocol Reference

---

## Concepts and Terminology

This section defines all core concepts used throughout this reference. Assume no prior
knowledge of Bambu Lab, 3D printing, MQTT, or streaming protocols.

### Bambu Lab
Chinese manufacturer of FDM 3D printers known for high-speed enclosed desktop machines
aimed at hobbyists and professionals alike. Their product line includes the X1C, X1E,
H2D, P1S, P1P, A1, and A1 Mini. bambu-mcp communicates with them over a local network
using MQTT and FTPS — no cloud account required.

### FDM (Fused Deposition Modeling)
The 3D printing process where plastic filament is melted through a heated nozzle and
deposited in layers to build a 3D object from the bottom up. The printer builds the
object one horizontal cross-section at a time, with each layer fusing to the one below
it. FDM is the technology used by all Bambu Lab printers.

### Filament
The plastic feedstock consumed during printing, supplied on a spool. Common materials
include PLA (easy to print, biodegradable), PETG (strong and flexible), ABS
(heat-resistant), TPU (flexible rubber-like), PA/Nylon (engineering-grade), and PC
(polycarbonate). Each material requires specific nozzle and bed temperatures, and some
materials require an enclosure or specific nozzle types to print successfully.

### Nozzle
The metal tip at the end of the hotend that melts and extrudes filament onto the build
plate. Nozzles are characterized by their opening diameter (0.2 mm for fine detail,
0.4 mm standard, 0.6/0.8 mm for fast printing) and their material (brass for
general-purpose use; hardened steel, tungsten carbide, or E3D for abrasive filaments
containing carbon fiber, glass fiber, or glow-in-the-dark additives). The MCP can read
the current nozzle configuration via get_nozzle_info() and update it via
set_nozzle_config().

### Build Plate / Bed
The heated flat surface on which parts are printed. Bed temperature affects whether the
first layer adheres correctly — too cold and the part pops off; too hot and it warps or
sticks too firmly. Bambu printers use removable magnetic surface plates: textured PEI
(works with most materials), smooth PEI (PLA/PETG), engineering plate (ABS/ASA), and
cool plate (PLA). An incorrect bed temperature is one of the most common causes of
failed prints.

### AMS (Automatic Material System)
Bambu's optional multi-filament feeder unit that automates spool switching and enables
multi-color or multi-material printing. The AMS 2 Pro holds 4 spools and feeds the
standard extruder; the AMS HT holds 1 high-temperature spool (for PA, PC, ABS) and
feeds the second extruder on H2D printers. A printer can have 0, 1, or 2 AMS units.
An external spool holder (tray_id=254) can hold a fifth filament without an AMS unit.

### Spool / Tray
A reel of filament loaded into an AMS slot (slots 0–3) or the external holder
(slot 254). Each spool has a filament type (e.g. "PLA"), color (hex RGB), and an
estimated remaining percentage based on weight or RFID tag data. The MCP can read
spool information via get_spool_info() and trigger RFID re-scans via
calibrate_ams_remaining().

### HMS (Health Management System)
Bambu's on-printer error and diagnostic system. When something goes wrong — filament
tangle, nozzle clog, temperature malfunction, or hardware fault — the printer generates
an HMS error code. Each error is encoded as a pair of integers (attr, code) that
together identify the hardware module, severity (fatal/error/warning/info), and specific
fault condition. The MCP exposes active HMS errors via get_hms_errors().

### G-code / GCode
The machine instruction language used by virtually all 3D printers. A print job is a
sequence of G-code commands specifying: move to a position, set a temperature, extrude
a given amount of filament, set fan speed, and so on. Bambu printers execute G-code
from .3mf files stored on the SD card. The AI does not need to write G-code — print
jobs are prepared ("sliced") by BambuStudio or OrcaSlicer before being uploaded to the
printer.

### gcode_state
The current execution state of the print job engine, reported in every push_status
telemetry message. Known values:
- IDLE     — no active job; printer is ready for a new print
- PREPARE  — pre-print setup in progress (homing axes, auto-leveling, preheating
              nozzle and bed)
- RUNNING  — actively printing; layers are being deposited
- PAUSE    — job is paused (either by the user or triggered automatically by a
              sensor or error)
- FINISH   — the job completed successfully
- FAILED   — the job ended with an error (check HMS errors for the cause)
- SLICING  — the printer is processing/slicing a file on-device (rare)
- INIT     — initializing after power-on or reset

### Stage
A sub-state within gcode_state that describes the exact printer activity in finer
detail. While gcode_state provides coarse status, stage pinpoints which specific
operation is in progress. Key values: 0=idle/finished, 1=auto-leveling bed,
2=preheating bed, 8=heating nozzle, 14=homing toolhead, 15=cleaning nozzle,
17=paused by user request, 19=calibrating extrusion flow, 255=printing normally.
The MCP exposes this via get_print_progress().

### MQTT (Message Queuing Telemetry Transport)
A lightweight publish/subscribe messaging protocol widely used in IoT devices. The
printer continuously publishes its state to a "report" topic and the MCP subscribes
to receive those updates; the MCP publishes commands to a "request" topic and the
printer executes them. Bambu printers run an MQTT broker on port 8883 (SSL-encrypted)
and authenticate with username "bblp" and the printer's LAN access code as the
password.

### push_status
The primary continuous telemetry message that the printer publishes to its MQTT report
topic whenever anything changes. It contains the entire current printer state:
temperatures, fan speeds, print progress, AMS filament state, error codes, xcam
detector states, and dozens of other fields. The MCP processes push_status messages
automatically — tools like get_temperatures() and get_print_progress() simply read
from this cached, continuously-updated data.

### Bitfield
An integer where individual bits each encode a separate boolean or small-integer value.
For example, home_flag bit 4 encodes whether auto_recovery is enabled or disabled. To
read a flag at bit position N: `(integer >> N) & 0x1`. Several Bambu telemetry fields
(home_flag, fun, stat, xcam.cfg) use bitfields to pack many independent values into a
single number.

### SSDP (Simple Service Discovery Protocol)
A network discovery protocol that uses UDP multicast to announce service presence.
Bambu printers broadcast SSDP packets on UDP port 2021 to announce their presence on
the local network. The MCP's discover_printers() tool listens for these broadcasts to
find printers automatically — no manual IP address entry required. Note: SSDP reveals
the printer's IP and serial number but NOT the access code, which must be read from
the printer's touchscreen.

### FTPS (FTP Secure / FTP over SSL)
A file transfer protocol using FTP commands wrapped in an SSL/TLS encrypted connection.
Bambu printers expose an FTPS server on port 990 for SD card file operations: uploading
.3mf print jobs, downloading files, creating directories, and deleting files. The MCP
uses FTPS for all file management tools (list_sdcard_files, upload_file, download_file,
delete_file, create_folder).

### 3MF File
The print job container format produced by BambuStudio and OrcaSlicer (the slicing
software used to prepare print jobs for Bambu printers). A .3mf file is a ZIP archive
containing G-code for each print plate, per-plate preview thumbnails, filament and AMS
mapping data, object bounding boxes, and all print settings. You upload a .3mf to the
printer's SD card via upload_file(), then start it with print_file().

### Plate
One independently printable "page" within a .3mf file. A single .3mf can contain
multiple plates — for example, a project with 14 different parts might arrange them
across multiple plates. Each plate has its own set of objects, filament color
requirements, thumbnail image, top-down layout image, and G-code. Plates are numbered
starting at 1. The MCP can extract per-plate information via get_project_info().

### Timelapse
A time-compressed video automatically recorded during a print, assembled from camera
frames captured after each layer completes. Bambu printers with cameras can record
timelapses and store them on the SD card for later download. Timelapse recording can
be enabled or disabled when starting a print via print_file(timelapse=True/False).

### xcam
Bambu's AI vision system built around the printer's built-in camera. It monitors the
print in real time and can automatically detect and respond to failure conditions.
Detectors include: spaghetti detection (layers separating and tangling), purge chute
pile-up, nozzle clumping, air printing (nozzle extruding into air = clog), and build
plate marker detection. Each detector can be independently enabled/disabled and
configured with a sensitivity level (low/medium/high). The MCP exposes xcam detector
control via the detector tools.

### RTSPS (Real-Time Streaming Protocol Secure)
RTSP (Real-Time Streaming Protocol) is the industry-standard protocol for delivering
live video streams, similar to how HTTP delivers web pages. RTSPS is RTSP over TLS
encryption (the "S" suffix means Secure). Bambu X1-series and H2D printers expose
their camera as an RTSPS stream on port 322; the URL format is
rtsps://bblp:{access_code}@{ip}:322/streaming/live/1. TLS certificate verification
must be disabled because Bambu printers use a self-signed certificate. The MCP decodes
RTSPS streams using PyAV (libav bundled as a pip wheel — no system ffmpeg required).

### TCP+TLS Binary Camera Protocol (A1 / P1 series — port 6000)
A1, A1 Mini, P1P, and P1S printers expose their camera via a proprietary Bambu binary
protocol over a TLS-encrypted TCP connection on port 6000. TLS certificate verification
must be disabled (self-signed Bambu certificate). After the TLS handshake, the client
sends a 64-byte auth packet then receives a continuous stream of framed JPEG images.

Auth packet layout (64 bytes, little-endian):
  Offset  Size  Value
   0       4B   LE uint32: payload size = 0x40 (64)
   4       4B   LE uint32: message type = 0x3000
   8       4B   LE uint32: flags = 0
  12       4B   LE uint32: reserved = 0
  16      32B   username = "bblp", ASCII, null-padded to 32 bytes
  48      32B   password = access_code, ASCII, null-padded to 32 bytes

Frame header layout (16 bytes, precedes each JPEG, little-endian):
  Offset  Size  Value
   0       4B   LE uint32: JPEG payload size in bytes
   4       4B   LE uint32: itrack = 0
   8       4B   LE uint32: flags = 1
  12       4B   LE uint32: reserved = 0

JPEG frames start with b'\\xff\\xd8' and end with b'\\xff\\xd9'. Data is chunked (≤4096
bytes per recv call). The frame is decoded by reading exactly (payload_size) bytes
after the header. There is no separate negotiation or session setup beyond the auth
packet — frames begin arriving immediately.

### MJPEG (Motion JPEG)
A video format where each frame is a complete, individually-encoded JPEG image
delivered sequentially over HTTP as a multipart/x-mixed-replace response. Any modern
web browser natively displays a MJPEG stream as live video when the URL is opened
directly in a browser tab. The MCP serves MJPEG from a local HTTP server
(http://localhost:{port}/) to provide a browser-viewable stream regardless of the
underlying printer protocol (RTSPS or TCP+TLS).

### Access Code
The 8-character LAN password for a printer, displayed on the touchscreen under
Settings → Network → Access Code. It is used to authenticate MQTT connections, FTPS
file transfers, and camera streams — it is the only credential required for full
LAN-mode printer control. The access code does not change unless manually reset by the
user. The MCP stores it via add_printer() and must never display it in responses.

### LAN Mode
Operating Bambu printers entirely over the local network, without requiring a Bambu
Cloud account or internet connection. bambu-mcp always operates in LAN mode. LAN mode
requires that the printer and the computer running the MCP be on the same local
network, and that LAN mode be enabled on the printer (Settings → Network →
LAN Mode Locking).

---

## MQTT Topics

All communication uses SSL-encrypted MQTT on port 8883.

| Direction      | Topic pattern                        | Purpose                              |
|----------------|--------------------------------------|--------------------------------------|
| Commands (out) | `device/{serial}/request`            | Send commands to printer             |
| Telemetry (in) | `device/{serial}/report`             | Receive all telemetry from printer   |

Subscription: `client.subscribe(f"device/{serial}/report")` after connect.
Authentication: username = `bblp`, password = printer access code (8-char string).
Client ID: `studio_client_id:0c1f` (default; configurable via BambuConfig).

The "push" message types (push_status, push_info, push_full) are message types
WITHIN the report topic — NOT separate subscription topics.

---

## Message Types (inbound on report topic)

### `push_status`
The primary continuous telemetry stream. Contains the `print` key at root.
Fields include: `gcode_state`, `bed_temper`, `bed_target_temper`,
`nozzle_temper`, `nozzle_target_temper`, `home_flag`, `mc_percent`,
`mc_remaining_time`, `layer_num`, `total_layer_num`, `stg_cur`, `spd_lvl`,
`subtask_name`, `gcode_file`, `lights_report`, `print_error`, `hms`,
`ams`, `xcam`, `wifi_signal`, `fun`, `stat`.

### `push_info` / info module messages
Contains the `info` key with `module` array. Used for firmware version reporting.
Each module entry: `{"name": "ota", "sn": "<serial>", "sw_ver": "<version>",
"product_name": "..."}`. AMS firmware version sourced from modules where
`product_name.lower()` contains `"ams"`.

### `push_full`
A full state snapshot; parsed identically to `push_status`.

### Command Acknowledgment (ack)
When a command is accepted, the printer echoes the command back with
`"result": "success"` in the `print` block. These are transient acks —
they confirm command acceptance but are NOT the steady-state source of truth
for flags/states. Steady-state truth comes from `push_status` bitfields.

Example ack structure:
```json
{"print": {"command": "ams_filament_setting", "result": "success", ...}}
```

### xcam result messages
`{"xcam": {"result": "SUCCESS", ...}}` — result of an XCAM_CONTROL_SET command.

### system messages
`{"system": {...}}` — logged but no handler currently.

### update messages
`{"update": {"name": "<name>", "reason": "success", "result": "success"}}` —
response to printer rename. Logged as "unknown message type" — no handler exists.

---

## ANNOUNCE_VERSION / ANNOUNCE_PUSH (watchdog / refresh)

On connect and periodically (watchdog interval, default 30s), publish both:
- `ANNOUNCE_VERSION` → triggers the printer to send firmware/module info
- `ANNOUNCE_PUSH` → triggers full push_status telemetry refresh

The watchdog thread publishes these when no message received within
`watchdog_timeout` seconds. `refresh()` and `trigger_printer_refresh` (container
API endpoint) both publish these commands explicitly.

---

## home_flag Bitfield (from push_status)

The `home_flag` integer in `push_status` is the steady-state source for these
`BambuConfig` fields and `PrinterCapabilities`:

| Bit | Field                                    | Type         |
|-----|------------------------------------------|--------------|
|  4  | `config.auto_recovery`                   | bool (state) |
|  7  | `config.calibrate_remain_flag`           | bool (state) |
| 10  | `config.auto_switch_filament`            | bool (state) |
| 17  | `config.sound_enable`                    | bool (state) |
| 18  | `has_sound_enable_support`               | bool (cap)   |
| 19  | `has_filament_tangle_detect_support`     | bool (cap)   |
| 20  | `config.filament_tangle_detect`          | bool (state) |
| 24  | `config.nozzle_blob_detect`              | bool (state) |
| 25  | `has_nozzle_blob_detect_support`         | bool (cap)   |
| 28  | `config.air_print_detect`                | bool (state) |
| 29  | `has_air_print_detect_support`           | bool (cap)   |

Reading pattern (source in bambuprinter.py `_on_message`):
```python
flag = int(status["home_flag"])
config.auto_recovery = (flag >> 4) & 0x1 != 0
config.auto_switch_filament = (flag >> 10) & 0x1 != 0
config.calibrate_remain_flag = (flag >> 7) & 0x1 != 0
config.capabilities.has_sound_enable_support = (flag >> 18) & 0x1 != 0
...
```

Telemetry Mapping Parity: All print_option flags sourced from `home_flag` by
default. New sibling flags should follow the same home_flag pattern unless
direct evidence proves a different source.

---

## xcam Fields and Detection Features

The `xcam` block in `push_status` drives X-Cam AI vision detector state.

### xcam.cfg bitfield (modern firmware — H2D and newer)
The `xcam.cfg` integer encodes all detector states and sensitivities:

| Bits  | Field                        | Notes                            |
|-------|------------------------------|----------------------------------|
|  7    | spaghetti_detector (enable)  | bool                             |
| 8-9   | spaghetti sensitivity        | 0=low, 1=medium, 2=high          |
| 10    | purgechutepileup_detector    | bool                             |
| 11-12 | pileup sensitivity           | 0=low, 1=medium, 2=high          |
| 13    | nozzleclumping_detector      | bool                             |
| 14-15 | clump sensitivity            | 0=low, 1=medium, 2=high          |
| 16    | airprinting_detector         | bool                             |
| 17-18 | airprint sensitivity         | 0=low, 1=medium, 2=high          |

### xcam explicit keys (legacy firmware — X1/P1/A1 series)
When `xcam.cfg` is absent, individual keys are used:
- `xcam.spaghetti_detector` → bool
- `xcam.pileup_detector` → bool
- `xcam.clump_detector` → bool
- `xcam.airprint_detector` → bool
- `xcam.buildplate_marker_detector` → bool
- `xcam.print_halt` → bool (sensitivity hint: True = medium)
- `xcam.first_layer_inspector` → bool (used for `has_lidar` capability)

### fun bitfield (capability flags)
The `fun` hex string from `push_status` encodes printer capability flags:

| Bit | Capability                              |
|-----|-----------------------------------------|
| 12  | `has_chamber_door_sensor`               |
| 42  | `has_spaghetti_detector_support`        |
| 43  | `has_purgechutepileup_detector_support` |
| 44  | `has_nozzleclumping_detector_support`   |
| 45  | `has_airprinting_detector_support`      |

### stat bitfield (door/lid sensor state)
When `has_chamber_door_sensor` is set, `stat` hex from `push_status` contains:

| Bit | Field                  |
|-----|------------------------|
| 23  | `is_chamber_door_open` |
| 24  | `is_chamber_lid_open`  |

---

## HMS Error Structure

### HMS list (hms array in push_status)
Each HMS entry has `attr` and `code` integer fields:
```json
{"attr": 0x03010000, "code": 0x00010001}
```
Combined ecode = `f"{attr:08X}{code:08X}"` — 16 hex chars.
wiki_slug = XXXX-XXXX-XXXX-XXXX format.

URL pattern: `https://e.bambulab.com/?e={ecode}`

Module encoding (bits 24-31 of attr):
| Byte | Module     |
|------|------------|
| 0x03 | Mainboard  |
| 0x05 | AMS        |
| 0x07 | Toolhead   |
| 0x0B | Webcam     |
| 0x10 | HMS        |
| 0x12 | AMS        |

Severity (bits 16-23 of attr):
| Value | Severity  | is_critical |
|-------|-----------|-------------|
| 0x00  | Fatal     | True        |
| 0x01  | Error     | True        |
| 0x02  | Warning   | False       |
| other | Info      | False       |

### print_error (single integer in push_status)
Decoded via `decodeError()` in bambutools.py. Format: 8-char hex, same module
and severity encoding as HMS. URL: `https://e.bambulab.com/?e={raw_hex}`.

---

## Firmware Upgrade State Fields (push_status)

Fields in `upgrade_state` within `push_status`:
- `upgrade_state.status`: `"FLASH_START"`, `"UPGRADE_SUCCESS"`, etc.
- `upgrade_state.progress`: 0–100 integer
- `upgrade_state.module`: `"ap"` = main Linux image
- `upgrade_state.message`: human-readable status
- `dis_state`: `2` = actively upgrading; `3` = complete/failed

---

## SSDP Discovery (UDP port 2021)

Printers broadcast SSDP UDP packets on port 2021 (not standard 1900).
Bind with `socket.bind(("", 2021))`.

Key fields parsed from raw UDP SSDP data:
| SSDP Header Key           | DiscoveredPrinter field | Description                      |
|---------------------------|-------------------------|----------------------------------|
| `USN`                     | `usn`                   | Serial number (deduplication key)|
| `LOCATION`                | `location`              | Printer IP address               |
| `devname.bambu.com`       | `dev_name`              | Printer display name             |
| `devversion.bambu.com`    | `dev_version`           | Firmware version string          |
| `devbind.bambu.com`       | `dev_bind`              | Binding status (e.g. "free")     |
| `devconnect.bambu.com`    | `dev_connect`           | Connection type (e.g. "lan")     |
| `devmodel.bambu.com`      | `dev_model`             | Hardware model string            |
| `devseclink.bambu.com`    | `dev_seclink`           | Security link status             |
| `devcap.bambu.com`        | `dev_cap`               | Device capabilities flag         |
| `NT`                      | `nt`                    | Notification type                |
| `NTS`                     | `nts`                   | Notification subtype             |

`DiscoveredPrinter.fromData(data_str)` parses raw UDP payload strings.
`BambuDiscovery` deduplicates by USN. For change monitoring, bind the socket
directly and compare fields across broadcasts.

---

## AMS Info Parsing (ams_info hex field)

The `ams_info` hex string in the `ams` block is a 32-bit integer with bit fields:

| Bits  | Field                    | Notes                                      |
|-------|--------------------------|--------------------------------------------|
|  0-3  | AMS type                 | 1=AMS_1, 2=AMS_LITE, 3=AMS_2_PRO, 4=AMS_HT |
|  4-7  | Dry status               | AMSHeatingState enum                       |
|  8-11 | Extruder ID              | H2D toolhead assignment                    |
| 18-19 | Dry fan 1 status         | AMSDryFanStatus (OFF=0, ON=1)              |
| 20-21 | Dry fan 2 status         | AMSDryFanStatus (OFF=0, ON=1)              |
| 22-25 | Dry sub-status           | AMSDrySubStatus enum                       |

Parse with: `parseAMSInfo(ams_u["info"])` from bambutools.py.
Sourced from BambuStudio's `DevFilaSystemParser::ParseAmsInfo`.

### tray_exist_bits
Hex-encoded bitmask of which slots have filament:
- Standard AMS (id 0-3): bit shift = `4 * id`, slots 0-3 (4 bits per unit)
- AMS HT (id 128+): bit shift = `16 + 4 * (id - 128)`, 1 slot per unit

---

## 3MF Structure

A `.3mf` file is a ZIP archive. Key internal entries:

| ZIP Entry                         | Purpose                                        |
|-----------------------------------|------------------------------------------------|
| `Metadata/slice_info.config`      | XML: objects, filament IDs, colors, identify_ids, filament_maps |
| `Metadata/project_settings.config`| INI: filament types and colors (fallback)      |
| `Metadata/plate_N.json`           | JSON: bbox_objects, filament_ids, filament_colors per plate |
| `Metadata/plate_N.png`            | PNG: slicer preview thumbnail                  |
| `Metadata/top_N.png`              | PNG: top-down view thumbnail                   |
| `Metadata/plate_N.gcode`          | G-code header: filament type/color (fallback)  |

### ams_mapping encoding (BambuStudio/OrcaSlicer DevMapping.cpp)
JSON integer array — one absolute tray ID per filament slot:

| Value   | Meaning                                           |
|---------|---------------------------------------------------|
| 0–103   | Standard 4-slot AMS: `ams_id * 4 + slot_id`      |
| 128–135 | Single-slot AMS HT / N3S: `ams_id` (starts at 128) |
| 254     | External spool                                    |
| -1      | Unmapped / no AMS                                 |

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

---

### Bed dimensions by printer model

Used for mapping slicer bbox coordinates (mm) to top-down image pixel coordinates.

| Model(s)                         | Width (X) mm | Height (Y) mm |
|----------------------------------|-------------|---------------|
| H2D, H2S                         | 350         | 320           |
| X1C, X1, X1E, P1S, P1P, P2S, A1 | 256         | 256           |
| A1 Mini                          | 180         | 180           |

Coordinate mapping formula (slicer mm → image pixels):
- Slicer origin: bottom-left (0,0). Image origin: top-left.
- scale = min(img_w / bed_w, img_h / bed_h)   # uniform scale, no distortion
- x_off = (img_w - bed_w * scale) / 2          # centre horizontally
- y_off = (img_h - bed_h * scale) / 2          # centre vertically
- pixel_x = x_off + x_mm * scale
- pixel_y = img_h - y_off - y_mm * scale        # Y flip

---

## trigger_printer_refresh

Container API endpoint: `GET /api/trigger_printer_refresh`

Forces the container to re-query the printer by publishing ANNOUNCE_VERSION
and ANNOUNCE_PUSH to the printer's MQTT request topic. Must be called after
a printer reboot (firmware upgrade, etc.) before reading `firmware_version` —
the cached value is stale until refreshed.

---

## FTPS File Operations

Port 990, implicit SSL, credentials same as MQTT (username=`bblp`, password=access_code).
Used via `IoTFTPSClient` / `BambuPrinter.ftp_connection()` context manager.
Operations: `upload_file`, `download_file`, `delete_file`, `move_file`,
`list_files_ex`, `mkdir`, `delete_folder`, `fexists`.

For A1/P1 series: file URL format = `file:///sdcard{path}`
For X1/H2D series: file URL format = `ftp://{path}`

---

## Extruder Device Block (H2D dual-extruder)

Telemetry path in push_status: `print.device.extruder`

`extruder.info[]` — array of extruder entries, each:
- `id`: extruder physical ID (0=right, 1=left on H2D)
- `temp`: packed 32-bit temperature (unpack with `unpackTemperature()`)
- `info`: bitmask for filament presence (bit 3 = nozzle present, bit 1 = loaded,
  bit 2 = buffer loaded)
- `stat`: operational state bitmask
- `snow` / `hnow`: active tray slot / hotend slot IDs
- `star` / `htar`: target tray / hotend IDs

`extruder.state`: active tool selection — bits 4-7 = raw tool index.
  `ActiveTool(raw_t_idx)` resolves to RIGHT_EXTRUDER (0), LEFT_EXTRUDER (1), or NOT_ACTIVE (15).

`device.nozzle.info[]` — per-nozzle characteristics:
- `id`: nozzle ID (matched to extruder via hnow & 0xFF)
- `type`: nozzle material string (e.g. "hardened_steel", "HS01-0.4")
- `diameter`: float in mm

`device.ctc` — Chamber Thermal Controller block (CTC):
- `info.temp`: packed 32-bit actual+target chamber temperature

`device.airduct` — Airduct/HVAC block:
- `modeCur`: 0=cool, 1=heat, -1=not supported
- `subMode`: sub-mode integer
- `parts[]`: zone fan states — id 16=part fan, 32=aux fan, 48=exhaust, 96=intake
"""
