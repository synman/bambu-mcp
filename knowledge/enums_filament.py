"""
enums_filament.py — NozzleDiameter, NozzleType, NozzleFlowType, PlateType, PrintOption, Stage.

Sub-topic of enums. Access via get_knowledge_topic('enums/filament').
"""

from __future__ import annotations

ENUMS_FILAMENT_TEXT: str = """
# Bambu Lab Enums — Filament, Nozzle, Plate & Print Options

All enums sourced from bpm.bambutools.

---

## NozzleDiameter (Enum)

Nozzle opening diameter in millimeters. Used by set_nozzle_details().

| Member | Value |
|---|---|
| UNKNOWN | 0.0 |
| POINT_TWO_MM | 0.2 |
| POINT_FOUR_MM | 0.4 |
| POINT_SIX_MM | 0.6 |
| POINT_EIGHT_MM | 0.8 |

---

## NozzleType (Enum)

Nozzle material. Softer materials (BRASS) wear out with abrasive filaments (carbon fiber,
glass fiber, glow-in-the-dark). Use hardened steel/tungsten carbide/E3D for abrasives.
Read via get_nozzle_info(); write via set_nozzle_config().

| Member | Value | Telemetry string |
|---|---|---|
| UNKNOWN | 0 | "unknown" |
| STAINLESS_STEEL | 1 | "stainless_steel" |
| HARDENED_STEEL | 2 | "hardened_steel" |
| TUNGSTEN_CARBIDE | 3 | "tungsten_carbide" |
| BRASS | 4 | "brass" |
| E3D | 5 | "E3D" |

---

## NozzleFlowType (Enum)

Canonical nozzle flow families. Encoded as second character in identifiers like
`HS00-0.4` (H=prefix, S=flow type, 00=material).

| Member | Value | Description |
|---|---|---|
| UNKNOWN | "?" | |
| STANDARD | "S" | Standard flow |
| HIGH_FLOW | "H" | High flow |
| TPU_HIGH_FLOW | "U" | TPU high flow |

---

## PlateType (Enum)

Removable build surface type. Used by print_3mf_file() bed parameter.
Sent as `bed_type = plate.name.lower()`.

| Member | Value | Description |
|---|---|---|
| AUTO | 0 | Printer decides based on slicer metadata |
| COOL_PLATE | 1 | Cool plate / PEI |
| ENG_PLATE | 2 | Engineering plate |
| HOT_PLATE | 3 | Smooth PEI high-temp plate |
| TEXTURED_PLATE | 4 | Textured PEI plate |
| NONE | 999 | No plate specified |

---

## PrintOption (Enum)

Configurable print behavior flags. Read via BambuConfig attributes; toggle via
set_print_option(). Steady-state sourced from `home_flag` bitfield.

| Member | Value | home_flag bit | BambuConfig field | Description |
|---|---|---|---|---|
| AUTO_RECOVERY | 0 | bit 4 | config.auto_recovery | Resume print automatically after a power loss or hardware fault. |
| FILAMENT_TANGLE_DETECT | 1 | bit 20 | config.filament_tangle_detect | Pause print if AMS sensors detect a filament tangle. |
| SOUND_ENABLE | 2 | bit 17 | config.sound_enable | Enable audible beep notifications for print events. |
| AUTO_SWITCH_FILAMENT | 3 | bit 10 | config.auto_switch_filament | When active spool runs out, automatically switch to another AMS slot loaded with the same filament type AND color. AMS-hosted spools only — external spool holder spools are not eligible. |
| NOZZLE_BLOB_DETECT | 4 | bit 24 | config.nozzle_blob_detect | Pause print if a filament blob accumulates on the nozzle tip. |
| AIR_PRINT_DETECT | 5 | bit 28 | config.air_print_detect | Pause print if nozzle is detected extruding into open air (indicates a clog or grinding condition). |

Note: AUTO_RECOVERY also sets `cmd["print"]["option"] = 1 if enabled else 0`.

Note on legacy vs xcam detector pairs:
- NOZZLE_BLOB_DETECT (home_flag, older path) and nozzleclumping_detector (xcam, newer path)
  both detect nozzle blob conditions. On printers where has_nozzleclumping_detector_support=True,
  prefer the xcam version via set_nozzle_clumping_detection() — it offers sensitivity control.
- AIR_PRINT_DETECT (home_flag, older path) and airprinting_detector (xcam, newer path) both
  detect air-printing conditions. On printers where has_airprinting_detector_support=True,
  prefer the xcam version via set_air_printing_detection() — it offers sensitivity control.
  Both pairs can be active simultaneously on supported printers.

---

## DetectorSensitivity (Enum)

Sensitivity level for X-Cam AI vision detectors.
LOW = fewer false positives; HIGH = catches more subtle issues.
String value sent directly in `halt_print_sensitivity` MQTT field.

| Member | Value |
|---|---|
| LOW | "low" |
| MEDIUM | "medium" |
| HIGH | "high" |

---

## Stage Mappings (parseStage — bambutools.py)

The `stg_cur` integer in `push_status` maps to human-readable print stage names.

Key stage IDs:

| ID | Stage | ID | Stage |
|---|---|---|---|
| -1 | (empty) | 22 | Filament unloading |
| 0 | (empty) | 24 | Filament loading |
| 1 | Auto bed leveling | 29 | Cooling chamber |
| 4 | Changing filament | 30 | Custom Gcode pause |
| 7 | Heating hotend | 36 | Absolute accuracy pre-check |
| 16 | Paused by user | 100 | Printing |
| 17 | Front cover falling | 255 | Completed |

Full mapping: stage IDs 0-58, 70-77, 100, 255 (see parseStage() in bambutools.py).
"""
