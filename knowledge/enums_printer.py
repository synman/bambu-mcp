"""
enums_printer.py — PrinterModel, PrinterSeries, ActiveTool, ServiceState, AirConditioningMode.

Sub-topic of enums. Access via get_knowledge_topic('enums/printer').
"""

from __future__ import annotations

ENUMS_PRINTER_TEXT: str = """
# Bambu Lab Enums — Printer

All enums sourced from bpm.bambutools.

---

## PrinterModel (Enum)

Identifies the specific printer model. Auto-detected from serial number prefix at
connection time. Determines camera protocol, bed dimensions, AMS compatibility, etc.

| Member | Value | Description |
|---|---|---|
| UNKNOWN | "unknown" | Unrecognized serial prefix |
| X1C | "x1c" | X1 Carbon |
| X1 | "x1" | X1 (non-Carbon) |
| X1E | "x1e" | X1E (Enterprise) |
| P1P | "p1p" | P1P |
| P1S | "p1s" | P1S |
| A1_MINI | "a1_mini" | A1 Mini |
| A1 | "a1" | A1 |
| P2S | "p2s" | P2S |
| H2S | "h2s" | H2S |
| H2D | "h2d" | H2D (Dual Extruder) |

Serial prefix → model mapping (getPrinterModelBySerial):

| Prefix | Model | | Prefix | Model |
|---|---|---|---|---|
| 00M | X1C | | 030 | A1_MINI |
| 00W | X1 | | 039 | A1 |
| 03W | X1E | | 22E | P2S |
| 01S | P1P | | 093 | H2S |
| 01P | P1S | | 094 | H2D |

---

## PrinterSeries (Enum)

Groups printer models into hardware generations for feature-level decisions.
Prefer checking specific capabilities via get_capabilities() over branching on series.

| Member | Value | Models |
|---|---|---|
| UNKNOWN | 0 | |
| X1 | 1 | X1C, X1, X1E |
| P1 | 2 | P1P, P1S |
| A1 | 3 | A1_MINI, A1 |
| P2 | 4 | P2S |
| H2 | 5 | H2S, H2D |

getPrinterSeriesByModel(model) → tries PrinterSeries[model.name[:2]].

---

## ActiveTool (IntEnum)

Which extruder is currently active on H2D dual-extruder printers. RIGHT_EXTRUDER
(id=0) is fed by AMS 2 Pro. LEFT_EXTRUDER (id=1) is fed by AMS HT.
Single-extruder printers always use RIGHT_EXTRUDER.
Sourced from `device.extruder.state` bits 4-7 in push_status.

| Member | Value | Description |
|---|---|---|
| SINGLE_EXTRUDER | -1 | Standard single-toolhead (X1/P1/A1) |
| RIGHT_EXTRUDER | 0 | Primary/right toolhead in H2D |
| LEFT_EXTRUDER | 1 | Secondary/left toolhead in H2D |
| NOT_ACTIVE | 15 | Multi-extruder system in transitional state |

---

## ServiceState (Enum)

Tracks the MQTT connection state. Read via get_session_status() or
get_printer_connection_status().

| Member | Value | Description |
|---|---|---|
| NO_STATE | 0 | Not yet initialized |
| CONNECTED | 1 | MQTT connected and subscribed to /report |
| DISCONNECTED | 2 | Lost connection (watchdog will reconnect) |
| PAUSED | 3 | Unsubscribed from /report (no telemetry) |
| QUIT | 4 | Session terminated; instance is dead |

---

## AirConditioningMode (IntEnum)

H2D chamber air conditioning mode. Sourced from `device.airduct.modeCur`.

| Member | Value | Description |
|---|---|---|
| NOT_SUPPORTED | -1 | Printer not equipped with this feature |
| COOL_MODE | 0 | Not heating; top vent may be open |
| HEAT_MODE | 1 | Actively heating chamber with recirculation fan |
"""
