"""
enums_ams.py — AMS, TrayState, and Extruder enums.

Sub-topic of enums. Access via get_knowledge_topic('enums/ams').
"""

from __future__ import annotations

ENUMS_AMS_TEXT: str = """
# Bambu Lab Enums — AMS, Tray & Extruder

All enums sourced from bpm.bambutools.

---

## AMSModel (IntEnum)

Identifies attached AMS hardware. AMS_2_PRO = standard 4-spool unit for most filaments.
AMS_HT = single-spool high-temperature unit (PA/PC/ABS) on H2D printers.

| Member | Value | BambuStudio ID | Description |
|---|---|---|---|
| UNKNOWN | 0 | — | |
| AMS_1 | 1 | — | Original AMS |
| AMS_LITE | 2 | — | AMS Lite |
| AMS_2_PRO | 3 | N3F | AMS 2 Pro (4-slot) |
| AMS_HT | 4 | N3S | AMS HT (single-slot, high-temp) |

Serial prefix → AMSModel (getAMSModelBySerial):
| Prefix | Model | | Prefix | Model |
|---|---|---|---|---|
| 19C | AMS_2_PRO | | 006 | AMS_1 |
| 19F | AMS_HT | | 03C | AMS_LITE |

---

## AMSSeries (Enum)

Groups AMS units by generation/capability tier.

| Member | Value | Models |
|---|---|---|
| UNKNOWN | 0 | |
| GEN_1 | 1 | AMS_1, AMS_LITE |
| GEN_2 | 2 | AMS_2_PRO, AMS_HT |

---

## AMSControlCommand (Enum)

Sent via `BambuPrinter.send_ams_control_command()`.

| Member | Value | Effect |
|---|---|---|
| PAUSE | 0 | Pause AMS operation |
| RESUME | 1 | Resume AMS + auto-calls resume_print |
| RESET | 2 | Reset AMS to initial state |

---

## AMSUserSetting (Enum)

Sent via `BambuPrinter.set_ams_user_setting()`. All three settings are sent together
in one command; only the targeted setting changes.

| Member | Value | BambuConfig field |
|---|---|---|
| CALIBRATE_REMAIN_FLAG | 0 | config.calibrate_remain_flag |
| STARTUP_READ_OPTION | 1 | config.startup_read_option |
| TRAY_READ_OPTION | 2 | config.tray_read_option |

---

## AMSHeatingState (IntEnum)

AMS drying/heater states extracted from bits 4-7 of ams_info.
Only AMS 2 Pro and AMS HT support active drying states.

| Member | Value | Description |
|---|---|---|
| OFF | 0 | No drying active |
| CHECKING | 1 | Checking drying status |
| DRYING | 2 | Active drying phase |
| COOLING | 3 | Cooling after drying |
| STOPPING | 4 | Stopping drying process |
| ERROR | 5 | Error state |
| CANNOT_STOP_HEAT_OOC | 6 | Heat control out of control |
| PRODUCT_TEST | 7 | Product testing mode |

---

## AMSDrySubStatus (IntEnum)

AMS drying sub-status from bits 22-25 of ams_info.

| Member | Value | Description |
|---|---|---|
| OFF | 0 | No active drying phase |
| HEATING | 1 | Heating phase |
| DEHUMIDIFY | 2 | Dehumidification phase |

---

## AMSDryFanStatus (IntEnum)

AMS drying fan status. Two fans: fan1=bits 18-19, fan2=bits 20-21 of ams_info.

| Member | Value | Description |
|---|---|---|
| OFF | 0 | Fan is off |
| ON | 1 | Fan is running |

---

## TrayState (IntEnum)

Loading state of an AMS spool slot. Read from get_spool_info() and get_ams_units().

| Member | Value | Description |
|---|---|---|
| UNLOADED | 0 | No filament in extruder |
| LOADED | 1 | Filament loaded and active |
| LOADING | 2 | Filament loading in progress (stage 24) |
| UNLOADING | 3 | Filament unloading in progress (stage 22) |

---

## ExtruderInfoState (IntEnum)

Decoded from `extruder.info` bitmask (H2D). Bit 3=nozzle present, bit 1=loaded,
bit 2=buffer loaded.

| Member | Value | Condition |
|---|---|---|
| NO_NOZZLE | 0 | Bit 3 not set |
| EMPTY | 1 | Nozzle present, not loaded |
| BUFFER_LOADED | 2 | Nozzle present, buffer loaded |
| LOADED | 3 | Nozzle present, filament loaded |
| NOT_AVAILABLE | 4 | Single-extruder printers |

---

## ExtruderStatus (IntEnum)

Operational state of a physical extruder. Mapped from BambuStudio BBL_EXTRUDER_STATE.

| Member | Value | Condition |
|---|---|---|
| IDLE | 0 | Not heating, not active |
| HEATING | 1 | bit 0 set, working_bits not 2 or 3 |
| ACTIVE | 2 | working_bits (bits 8-9 of stat) is 2 or 3 |
| SUCCESS | 3 | Mapping exists |
| NOT_AVAILABLE | 4 | Single-extruder printers |
"""
