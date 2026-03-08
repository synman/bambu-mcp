"""
protocol_concepts.py — Bambu Lab protocol glossary and terminology.

Sub-topic of protocol. Access via get_knowledge_topic('protocol/concepts').
"""

from __future__ import annotations

PROTOCOL_CONCEPTS_TEXT: str = """
# Bambu Lab Protocol — Concepts and Terminology

---

### Bambu Lab
Chinese manufacturer of FDM 3D printers (X1C, X1E, H2D, P1S, P1P, A1, A1 Mini, P2S).
bambu-mcp communicates over local network using MQTT and FTPS — no cloud required.

### FDM (Fused Deposition Modeling)
3D printing process: plastic filament melted through a heated nozzle, deposited in
layers to build a 3D object from the bottom up. Used by all Bambu Lab printers.

### Filament
Plastic feedstock on a spool. Common materials: PLA (easy, biodegradable), PETG
(strong/flexible), ABS (heat-resistant), TPU (flexible), PA/Nylon, PC. Each material
requires specific nozzle and bed temperatures.

### Nozzle
Metal tip that melts and extrudes filament. Characterized by:
- Diameter: 0.2 mm (fine), 0.4 mm (standard), 0.6/0.8 mm (fast)
- Material: brass (general), hardened steel/tungsten carbide/E3D (abrasive filaments)
Read via get_nozzle_info(); update via set_nozzle_config().

### Build Plate / Bed
Heated flat surface for printing. Types: textured PEI (most materials), smooth PEI
(PLA/PETG), engineering plate (ABS/ASA), cool plate (PLA). Incorrect bed temp is a
common cause of failed prints.

### AMS (Automatic Material System)
Multi-filament feeder unit. AMS 2 Pro: 4 spools, standard extruder. AMS HT: 1
high-temp spool (PA/PC/ABS), second extruder on H2D. External spool holder = tray_id=254.
A printer can have 0, 1, or 2 AMS units.

### Spool / Tray
Filament reel loaded into AMS slots 0-3 or external holder (slot 254). Has type, color,
remaining % (from weight or RFID). Read via get_spool_info(); RFID re-scan via
calibrate_ams_remaining().

### HMS (Health Management System)
Bambu's on-printer error system. Error codes encoded as (attr, code) integer pairs
identifying hardware module, severity, and fault. Exposed via get_hms_errors().

### G-code / GCode
Machine instruction language for 3D printers. Print jobs are sequences of G-code from
.3mf files on the SD card. Bambu-mcp does not write G-code — print jobs are sliced by
BambuStudio or OrcaSlicer beforehand.

### gcode_state
Print job execution state in every push_status message. Values:
- IDLE — no active job; printer ready for new print
- PREPARE — pre-print setup (homing, auto-leveling, preheating)
- RUNNING — actively printing
- PAUSE — job paused (user or sensor/error triggered)
- FINISH — job completed successfully
- FAILED — job ended with error (check HMS errors)
- SLICING — printer processing/slicing a file on-device (rare)
- INIT — initializing after power-on or reset

### Stage
Sub-state within gcode_state. Key values: 0=idle, 1=auto-leveling, 2=preheating bed,
8=heating nozzle, 14=homing toolhead, 15=cleaning nozzle, 17=paused by user,
19=calibrating extrusion flow, 255=printing normally. See parseStage() for full mapping.

### MQTT (Message Queuing Telemetry Transport)
Lightweight publish/subscribe IoT protocol. Printer publishes state to "report" topic;
MCP subscribes. MCP publishes commands to "request" topic. Port 8883 (SSL-encrypted).
Authentication: username="bblp", password=access_code.

### push_status
Primary continuous telemetry message published whenever printer state changes.
Contains: temperatures, fan speeds, print progress, AMS filament state, error codes,
xcam detector states, and dozens of other fields. MCP caches this continuously.

### Bitfield
Integer where individual bits each encode a boolean. Pattern: `(integer >> N) & 0x1`.
Key bitfield fields: home_flag, fun (capability flags), stat (door sensor), xcam.cfg.

### SSDP (Simple Service Discovery Protocol)
UDP multicast discovery. Bambu printers broadcast SSDP packets on UDP port 2021 to
announce their presence. discover_printers() listens for these broadcasts. SSDP reveals
IP and serial but NOT the access code.

### FTPS (FTP Secure / FTP over SSL)
File transfer over TLS on port 990. Used for SD card operations: upload/download .3mf
files, list/delete files, create directories.

### 3MF File
Print job container format (ZIP archive) from BambuStudio/OrcaSlicer. Contains: G-code
for each plate, thumbnails, filament/AMS mapping, object bounding boxes, print settings.
Upload via upload_file(); start via print_file().

### Plate
One independently printable section within a .3mf file. Multiple plates per .3mf.
Each plate has its own objects, filament requirements, thumbnail, top-down layout image,
and G-code. Plates numbered starting at 1. See get_project_info().

### Timelapse
Time-compressed video recorded during print (one frame per layer). Stored on SD card.
Enable via print_file(timelapse=True).

### xcam
Bambu's AI vision system using the printer's camera to detect failures in real time.
Detectors: spaghetti, purge chute pile-up, nozzle clumping, air printing, build plate
marker. Each detector independently enabled/disabled with sensitivity (low/medium/high).

### RTSPS (Real-Time Streaming Protocol Secure)
RTSP over TLS. X1-series and H2D printers expose camera as RTSPS stream on port 322.
URL format: `rtsps://bblp:{access_code}@{ip}:322/streaming/live/1`. Self-signed cert;
disable TLS verification. Decoded via PyAV.

### TCP+TLS Binary Camera Protocol (A1 / P1 series — port 6000)
Proprietary Bambu binary protocol over TLS-encrypted TCP on port 6000.

Auth packet (64 bytes, little-endian):
  Offset 0:  uint32 payload_size=0x40
  Offset 4:  uint32 msg_type=0x3000
  Offset 8:  uint32 flags=0
  Offset 12: uint32 reserved=0
  Offset 16: 32B username "bblp" null-padded
  Offset 48: 32B password = access_code null-padded

Frame header (16 bytes, precedes each JPEG):
  Offset 0: uint32 JPEG payload size
  Offset 4: uint32 itrack=0
  Offset 8: uint32 flags=1
  Offset 12: uint32 reserved=0

JPEG frames: start b'\\xff\\xd8', end b'\\xff\\xd9'. Data chunked ≤4096 bytes per recv.

### MJPEG (Motion JPEG)
Video format where each frame is an individually-encoded JPEG delivered over HTTP as
multipart/x-mixed-replace. MCP serves MJPEG from a local HTTP server for browser
viewing regardless of underlying printer protocol.

### Access Code
8-character LAN password. Shown on printer touchscreen: Settings→Network→Access Code.
Used for MQTT, FTPS, and camera streams. Must never be logged or displayed in responses.

### LAN Mode
Operating Bambu printers entirely over the local network without Bambu Cloud.
bambu-mcp always operates in LAN mode. Printer and host must be on the same network.
Enable on printer: Settings→Network→LAN Mode Locking.
"""
