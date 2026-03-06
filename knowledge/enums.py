"""
enums.py — Enum documentation and re-exports for the bambu-mcp agent.

Exports:
  ENUMS_TEXT: str            — documentation text for all enums
  + all enum classes re-exported from bpm for use by tools
"""

import sys
from pathlib import Path

# Make bpm importable from the bambu-printer-manager source tree
_BPM_SRC = str(Path.home() / "bambu-printer-manager" / "src")
if _BPM_SRC not in sys.path:
    sys.path.insert(0, _BPM_SRC)

from bpm.bambutools import (  # noqa: E402
    ActiveTool,
    AirConditioningMode,
    AMSControlCommand,
    AMSDryFanStatus,
    AMSDrySubStatus,
    AMSHeatingState,
    AMSModel,
    AMSSeries,
    AMSUserSetting,
    DetectorSensitivity,
    ExtruderInfoState,
    ExtruderStatus,
    NozzleDiameter,
    NozzleFlowType,
    NozzleMaterialCode,
    NozzleType,
    PlateType,
    PrinterModel,
    PrinterSeries,
    PrintOption,
    ServiceState,
    TrayState,
)

__all__ = [
    "ENUMS_TEXT",
    "ActiveTool",
    "AirConditioningMode",
    "AMSControlCommand",
    "AMSDryFanStatus",
    "AMSDrySubStatus",
    "AMSHeatingState",
    "AMSModel",
    "AMSSeries",
    "AMSUserSetting",
    "DetectorSensitivity",
    "ExtruderInfoState",
    "ExtruderStatus",
    "NozzleDiameter",
    "NozzleFlowType",
    "NozzleMaterialCode",
    "NozzleType",
    "PlateType",
    "PrinterModel",
    "PrinterSeries",
    "PrintOption",
    "ServiceState",
    "TrayState",
]

ENUMS_TEXT: str = """
# Bambu Lab Enum Reference

All enums sourced from bpm.bambutools (bambu-printer-manager/src/bpm/bambutools.py).
Always use enum names rather than raw integers in code (e.g. PrinterModel.H2D not "h2d").

---

Identifies the specific Bambu Lab printer model. Detected automatically from the
printer's serial number prefix at connection time — you do not need to set this
manually. The model determines which features are available: dual extruder support,
AMS type compatibility, camera protocol, bed dimensions, and more.

## PrinterModel (Enum)

Maps to the string value used in telemetry and model identification.

| Member    | Value       | Description                          |
|-----------|-------------|--------------------------------------|
| UNKNOWN   | "unknown"   | Unrecognized serial prefix           |
| X1C       | "x1c"       | X1 Carbon                            |
| X1        | "x1"        | X1 (non-Carbon)                      |
| X1E       | "x1e"       | X1E (Enterprise)                     |
| P1P       | "p1p"       | P1P                                  |
| P1S       | "p1s"       | P1S                                  |
| A1_MINI   | "a1_mini"   | A1 Mini                              |
| A1        | "a1"        | A1                                   |
| P2S       | "p2s"       | P2S                                  |
| H2S       | "h2s"       | H2S                                  |
| H2D       | "h2d"       | H2D (Dual Extruder)                  |

Serial prefix → model mapping (getPrinterModelBySerial):
| Prefix | Model   |   | Prefix | Model  |
|--------|---------|---|--------|--------|
| 00M    | X1C     |   | 030    | A1_MINI|
| 00W    | X1      |   | 039    | A1     |
| 03W    | X1E     |   | 22E    | P2S    |
| 01S    | P1P     |   | 093    | H2S    |
| 01P    | P1S     |   | 094    | H2D    |

---

Groups printer models into hardware generations for feature-level decisions.
X1_SERIES (X1C/X1/X1E), H2_SERIES (H2D/H2S), P_SERIES (P1S/P1P/P2S),
A_SERIES (A1/A1_MINI). Prefer checking specific capabilities via get_capabilities()
over branching on series.

## PrinterSeries (Enum)

| Member  | Value | Models           |
|---------|-------|------------------|
| UNKNOWN | 0     |                  |
| X1      | 1     | X1C, X1, X1E     |
| P1      | 2     | P1P, P1S         |
| A1      | 3     | A1_MINI, A1      |
| P2      | 4     | P2S              |
| H2      | 5     | H2S, H2D         |

getPrinterSeriesByModel(model) → tries PrinterSeries[model.name[:2]].

---

Which extruder is currently active on H2D dual-extruder printers. RIGHT_EXTRUDER
(id=0) is the primary extruder fed by AMS 2 Pro. LEFT_EXTRUDER (id=1) is fed by
AMS HT. NOT_ACTIVE = no extruder selected. Single-extruder printers always use
RIGHT_EXTRUDER.

## ActiveTool (IntEnum)

The currently active extruder index.

| Member           | Value | Description                                          |
|------------------|-------|------------------------------------------------------|
| SINGLE_EXTRUDER  | -1    | Standard single-toolhead (X1/P1/A1)                  |
| RIGHT_EXTRUDER   |  0    | Primary/right toolhead in H2D dual-extruder systems   |
| LEFT_EXTRUDER    |  1    | Secondary/left toolhead in H2D dual-extruder systems  |
| NOT_ACTIVE       | 15    | Multi-extruder system in a transitional state          |

Sourced from `device.extruder.state` bits 4-7 in push_status.

---

## AirConditioningMode (IntEnum)

| Member        | Value | Description                                               |
|---------------|-------|-----------------------------------------------------------|
| NOT_SUPPORTED | -1    | Printer not equipped with this feature                    |
| COOL_MODE     |  0    | Not heating; top vent may be open if exhaust fan running  |
| HEAT_MODE     |  1    | Actively heating chamber with recirculation fan active    |

Sourced from `device.airduct.modeCur` (0=cool, 1=heat, else not supported).

---

Which AMS hardware unit is attached to the printer. AMS_2_PRO is the standard
4-spool unit for most filaments. AMS_HT is a single-spool high-temperature unit
for engineering filaments (PA, PC, ABS) on H2D printers. Read from get_ams_units().

## AMSModel (IntEnum)

| Member    | Value | BambuStudio ID | Description          |
|-----------|-------|----------------|----------------------|
| UNKNOWN   | 0     | —              |                      |
| AMS_1     | 1     | —              | Original AMS         |
| AMS_LITE  | 2     | —              | AMS Lite             |
| AMS_2_PRO | 3     | N3F            | AMS 2 Pro            |
| AMS_HT    | 4     | N3S            | AMS HT (single-slot) |

Serial prefix → AMSModel (getAMSModelBySerial):
| Prefix | Model     |
|--------|-----------|
| 19C    | AMS_2_PRO |
| 19F    | AMS_HT    |
| 006    | AMS_1     |
| 03C    | AMS_LITE  |

---

Groups AMS units by generation/capability tier.

## AMSSeries (Enum)

| Member  | Value | Models              |
|---------|-------|---------------------|
| UNKNOWN | 0     |                     |
| GEN_1   | 1     | AMS_1, AMS_LITE     |
| GEN_2   | 2     | AMS_2_PRO, AMS_HT   |

---

## AMSControlCommand (Enum)

Sent via `BambuPrinter.send_ams_control_command()`.

| Member | Value | Effect                               |
|--------|-------|--------------------------------------|
| PAUSE  | 0     | Pause AMS operation                  |
| RESUME | 1     | Resume AMS + auto-calls resume_print |
| RESET  | 2     | Reset AMS to initial state           |

---

## AMSUserSetting (Enum)

Sent via `BambuPrinter.set_ams_user_setting()`. All three are sent together
in a single command; only the targeted setting is changed.

| Member                | Value | BambuConfig field          |
|-----------------------|-------|----------------------------|
| CALIBRATE_REMAIN_FLAG | 0     | config.calibrate_remain_flag |
| STARTUP_READ_OPTION   | 1     | config.startup_read_option   |
| TRAY_READ_OPTION      | 2     | config.tray_read_option      |

---

## AMSHeatingState (IntEnum)

AMS drying/heater states extracted from bits 4-7 of ams_info.
Only AMS 2 Pro (N3F) and AMS HT (N3S) support active drying states.
Mapped from BambuStudio's DryStatus enum.

| Member              | Value | Description                    |
|---------------------|-------|--------------------------------|
| OFF                 | 0     | No drying active               |
| CHECKING            | 1     | Checking drying status         |
| DRYING              | 2     | Active drying phase            |
| COOLING             | 3     | Cooling after drying           |
| STOPPING            | 4     | Stopping drying process        |
| ERROR               | 5     | Error state                    |
| CANNOT_STOP_HEAT_OOC| 6     | Heat control out of control    |
| PRODUCT_TEST        | 7     | Product testing mode           |

---

## AMSDrySubStatus (IntEnum)

AMS drying sub-status from bits 22-25 of ams_info.
Mapped from BambuStudio's DrySubStatus enum.

| Member      | Value | Description                   |
|-------------|-------|-------------------------------|
| OFF         | 0     | No active drying phase        |
| HEATING     | 1     | Heating phase                 |
| DEHUMIDIFY  | 2     | Dehumidification phase        |

---

## AMSDryFanStatus (IntEnum)

AMS drying fan status. Two fans: fan1=bits 18-19, fan2=bits 20-21 of ams_info.

| Member | Value | Description    |
|--------|-------|----------------|
| OFF    | 0     | Fan is off     |
| ON     | 1     | Fan is running |

---

The nozzle opening diameter in millimeters. Smaller diameters (0.2mm) give finer
detail; larger diameters (0.6mm, 0.8mm) print faster with less detail. The standard
Bambu nozzle is 0.4mm. Read from get_nozzle_info(), updated via set_nozzle_config().

## NozzleDiameter (Enum)

Used by `BambuPrinter.set_nozzle_details()`.

| Member          | Value |
|-----------------|-------|
| UNKNOWN         | 0.0   |
| POINT_TWO_MM    | 0.2   |
| POINT_FOUR_MM   | 0.4   |
| POINT_SIX_MM    | 0.6   |
| POINT_EIGHT_MM  | 0.8   |

---

The material the nozzle is made of, which determines its durability with different
filaments. Softer materials (BRASS) wear out quickly with abrasive filaments containing
carbon fiber, glass fiber, or glow-in-the-dark additives. HARDENED_STEEL,
TUNGSTEN_CARBIDE, and E3D nozzles handle abrasive materials. Read from
get_nozzle_info(), written via set_nozzle_config().

## NozzleType (Enum)

Cross-model nozzle material type from telemetry.

| Member           | Value | Telemetry string   |
|------------------|-------|--------------------|
| UNKNOWN          | 0     | "unknown"          |
| STAINLESS_STEEL  | 1     | "stainless_steel"  |
| HARDENED_STEEL   | 2     | "hardened_steel"   |
| TUNGSTEN_CARBIDE | 3     | "tungsten_carbide" |
| BRASS            | 4     | "brass"            |
| E3D              | 5     | "E3D"              |

---

Whether the nozzle is standard, high-flow (wider melt chamber for faster printing),
or TPU high-flow. High-flow nozzles can print faster but require compatible filaments
and settings.

## NozzleFlowType (Enum)

Canonical nozzle flow families. Encoded as second character in identifiers
like `HS00-0.4` (H=prefix, S=flow type, 00=material).

| Member        | Value | Description     |
|---------------|-------|-----------------|
| UNKNOWN       | "?"   |                 |
| STANDARD      | "S"   | Standard flow   |
| HIGH_FLOW     | "H"   | High flow       |
| TPU_HIGH_FLOW | "U"   | TPU high flow   |

---

The type of removable build surface installed on the printer bed. Each surface
material is optimized for different filaments and requires different bed temperatures.
auto = let the printer choose based on the filament. Bed plate type is part of print
job setup in print_file().

## PlateType (Enum)

Used by `BambuPrinter.print_3mf_file()` bed parameter.

| Member         | Value | Description                            |
|----------------|-------|----------------------------------------|
| AUTO           | 0     | Printer decides based on slicer metadata|
| COOL_PLATE     | 1     | Cool plate / PEI                       |
| ENG_PLATE      | 2     | Engineering plate                      |
| HOT_PLATE      | 3     | Smooth PEI high-temp plate             |
| TEXTURED_PLATE | 4     | Textured PEI plate                     |
| NONE           | 999   | No plate specified                     |

Sent as `bed_type` in print command as `plate.name.lower()`.

---

Configurable print behavior flags that persist across reboots. Each can be read via
get_print_options() and toggled via set_print_option(). Examples: auto_recovery
(resume print after power loss), sound_enable (printer audio notifications),
filament_tangle_detect (pause on AMS tangle).

## PrintOption (Enum)

Sent via `BambuPrinter.set_print_option()`. Steady-state sourced from `home_flag`.

| Member                 | Value | home_flag bit | BambuConfig field            |
|------------------------|-------|---------------|------------------------------|
| AUTO_RECOVERY          | 0     | bit 4         | config.auto_recovery         |
| FILAMENT_TANGLE_DETECT | 1     | bit 20        | config.filament_tangle_detect|
| SOUND_ENABLE           | 2     | bit 17        | config.sound_enable          |
| AUTO_SWITCH_FILAMENT   | 3     | bit 10        | config.auto_switch_filament  |
| NOZZLE_BLOB_DETECT     | 4     | bit 24        | config.nozzle_blob_detect    |
| AIR_PRINT_DETECT       | 5     | bit 28        | config.air_print_detect      |

Note: AUTO_RECOVERY also sets `cmd["print"]["option"] = 1 if enabled else 0`.

---

The state of the MCP's MQTT connection to a printer. CONNECTED = active session with
telemetry streaming in; PAUSED = session suspended by pause_mqtt_session(); QUIT =
disconnected or shut down. Read via get_session_status() or
get_printer_connection_status().

## ServiceState (Enum)

Tracks the underlying state of the MQTT connection to the printer.

| Member       | Value | Description                              |
|--------------|-------|------------------------------------------|
| NO_STATE     | 0     | Not yet initialized                      |
| CONNECTED    | 1     | MQTT connected and subscribed to /report |
| DISCONNECTED | 2     | Lost connection (watchdog will reconnect)|
| PAUSED       | 3     | Unsubscribed from /report (no telemetry) |
| QUIT         | 4     | Session terminated; instance is dead     |

---

The loading state of an AMS spool slot. LOADED = filament is loaded and ready;
UNLOADED = slot has a spool but filament is retracted; LOADING / UNLOADING =
transition in progress. Read from get_spool_info() and get_ams_units().

## TrayState (IntEnum)

Operational status of the filament tray.

| Member    | Value | Description                      |
|-----------|-------|----------------------------------|
| UNLOADED  | 0     | No filament in extruder          |
| LOADED    | 1     | Filament loaded and active       |
| LOADING   | 2     | Filament loading in progress (stage 24) |
| UNLOADING | 3     | Filament unloading in progress (stage 22) |

---

How aggressively an xcam AI detector triggers. LOW = only triggers on obvious failures
(fewer false positives, may miss subtle issues). HIGH = triggers on subtle anomalies
(catches more problems but may pause prints unnecessarily). Sensitivity applies
per-detector and is set via the detector tools.

## DetectorSensitivity (Enum)

Sensitivity level for X-Cam AI vision detectors. String value sent directly
in `halt_print_sensitivity` MQTT field.

| Member | Value    |
|--------|----------|
| LOW    | "low"    |
| MEDIUM | "medium" |
| HIGH   | "high"   |

---

## ExtruderInfoState (IntEnum)

Decoded from `extruder.info` bitmask (H2D). Bit 3 = nozzle present,
bit 1 = loaded, bit 2 = buffer loaded.

| Member        | Value | Condition                          |
|---------------|-------|------------------------------------|
| NO_NOZZLE     | 0     | Bit 3 not set                      |
| EMPTY         | 1     | Nozzle present, not loaded         |
| BUFFER_LOADED | 2     | Nozzle present, buffer loaded      |
| LOADED        | 3     | Nozzle present, filament loaded    |
| NOT_AVAILABLE | 4     | Single-extruder printers           |

---

The operational state of an extruder on H2D dual-extruder printers.

## ExtruderStatus (IntEnum)

Operational state of a physical extruder. Mapped from BambuStudio BBL_EXTRUDER_STATE.

| Member       | Value | Condition                                          |
|--------------|-------|----------------------------------------------------|
| IDLE         | 0     | Not heating, not active                            |
| HEATING      | 1     | bit 0 set, working_bits not 2 or 3                 |
| ACTIVE       | 2     | working_bits (bits 8-9 of stat) is 2 or 3          |
| SUCCESS      | 3     | Mapping exists                                     |
| NOT_AVAILABLE| 4     | Single-extruder printers                           |

---

## Stage Mappings (parseStage — bambutools.py)

The `stg_cur` integer in `push_status` maps to human-readable print stage names.

Key stage IDs:
| ID  | Stage                           | ID  | Stage                      |
|-----|---------------------------------|-----|----------------------------|
| -1  | (empty)                         | 22  | Filament unloading         |
| 0   | (empty)                         | 24  | Filament loading           |
| 1   | Auto bed leveling               | 29  | Cooling chamber            |
| 4   | Changing filament               | 100 | Printing                   |
| 7   | Heating hotend                  | 255 | Completed                  |
| 16  | Paused by user                  | 30  | Custom Gcode pause         |
| 17  | Front cover falling             | 36  | Absolute accuracy pre-check|

Full mapping: stage IDs 0-58, 70-77, 100, 255 (see parseStage() in bambutools.py).
"""
