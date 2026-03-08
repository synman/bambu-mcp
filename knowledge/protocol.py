"""
protocol.py — Bambu Lab protocol summary for bambu-mcp agents.

Top-level topic: get_knowledge_topic('protocol')

Covers MQTT communication, HMS error encoding, 3MF file structure, SSDP discovery,
and camera protocols. For detailed content, call the sub-topics listed below.

Sources: bambu-printer-manager/src/bpm/bambuprinter.py, bambustate.py,
         bambu-printer-app/.github/copilot-instructions.md,
         bambu-printer-manager/.github/copilot-instructions.md
"""

from __future__ import annotations

PROTOCOL_TEXT: str = """
# Bambu Lab Protocol Reference — Summary

---

## Sub-Topics

Call the relevant sub-topic for detailed reference content.

### Glossary and terminology
All core concepts: Bambu Lab, FDM, filament, nozzle, build plate, AMS, spool, HMS,
G-code, gcode_state, Stage, MQTT, push_status, Bitfield, SSDP, FTPS, 3MF, Plate,
Timelapse, xcam, Access Code, LAN Mode, RTSPS, TCP+TLS camera protocol, MJPEG.
→ `get_knowledge_topic('protocol/concepts')`

### MQTT topics, message types, bitfields, xcam fields
MQTT topic structure, push_status/push_info/push_full message types, command acks,
ANNOUNCE_VERSION/PUSH, home_flag bitfield (all bit positions), xcam.cfg bitfield,
xcam explicit keys, fun capability bitfield, stat door/lid sensor bitfield.
→ `get_knowledge_topic('protocol/mqtt')`

### HMS errors and firmware upgrade state
HMS error structure (attr + code encoding, module bytes, severity), print_error decoding,
HMS_0300-400C transient error, two-command clear protocol, firmware upgrade state fields.
→ `get_knowledge_topic('protocol/hms')`

### 3MF structure, SSDP, AMS info, FTPS, extruder block
SSDP discovery (UDP port 2021, field parsing), AMS info hex field (bit layout),
tray_exist_bits, 3MF ZIP structure (plate_N.json, thumbnails, bbox_objects, ams_mapping
encoding), bed dimensions, trigger_printer_refresh, FTPS file operations, H2D dual-extruder
device block (extruder.info, device.nozzle, device.ctc, device.airduct).
→ `get_knowledge_topic('protocol/3mf')`

---

## Quick Reference

| Topic | When to use |
|---|---|
| `protocol/concepts` | Unfamiliar with a Bambu-specific term or concept |
| `protocol/mqtt` | Implementing MQTT message handling, bitfield decoding, xcam state |
| `protocol/hms` | Interpreting errors, clearing print_error, firmware upgrades |
| `protocol/3mf` | Parsing 3MF files, SSDP discovery, AMS info, FTPS operations |

---

## Key constants

- MQTT port: **8883** (SSL) | FTPS port: **990** (implicit SSL)
- Camera: RTSPS port **322** (X1/H2D) | TCP+TLS port **6000** (A1/P1)
- SSDP: UDP port **2021** (Bambu-specific, not standard 1900)
- MQTT username: **"bblp"**, password = access_code (8-char)
- Command topic: `device/{serial}/request`
- Telemetry topic: `device/{serial}/report`
"""
