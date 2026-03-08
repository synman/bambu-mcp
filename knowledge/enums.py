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
# Bambu Lab Enum Reference — Summary

All enums sourced from bpm.bambutools (bambu-printer-manager/src/bpm/bambutools.py).
Always use enum names rather than raw integers in code (e.g. PrinterModel.H2D not "h2d").

---

## Sub-Topics

Call the relevant sub-topic for full enum values and descriptions.

### Printer model and connection state enums
PrinterModel (11 models, serial prefix mapping), PrinterSeries (X1/P1/A1/P2/H2),
ActiveTool (RIGHT_EXTRUDER/LEFT_EXTRUDER/NOT_ACTIVE), ServiceState (CONNECTED/PAUSED/QUIT),
AirConditioningMode (COOL_MODE/HEAT_MODE).
→ `get_knowledge_topic('enums/printer')`

### AMS, tray, and extruder state enums
AMSModel (AMS_2_PRO/AMS_HT/AMS_1/AMS_LITE, serial prefix mapping), AMSSeries (GEN_1/GEN_2),
AMSControlCommand (PAUSE/RESUME/RESET), AMSUserSetting, AMSHeatingState (DRYING/COOLING/...),
AMSDrySubStatus, AMSDryFanStatus, TrayState (LOADED/UNLOADED/LOADING/UNLOADING),
ExtruderInfoState, ExtruderStatus.
→ `get_knowledge_topic('enums/ams')`

### Filament, nozzle, plate, and print option enums
NozzleDiameter (0.2/0.4/0.6/0.8 mm), NozzleType (BRASS/HARDENED_STEEL/TUNGSTEN_CARBIDE/E3D),
NozzleFlowType (STANDARD/HIGH_FLOW/TPU_HIGH_FLOW), PlateType (COOL_PLATE/ENG_PLATE/HOT_PLATE/TEXTURED_PLATE),
PrintOption (AUTO_RECOVERY/SOUND_ENABLE/... with home_flag bit assignments),
DetectorSensitivity (LOW/MEDIUM/HIGH), Stage Mappings (stg_cur integer → stage name string).
→ `get_knowledge_topic('enums/filament')`

---

## Quick Enum Lookup

| Enum | Sub-topic |
|---|---|
| PrinterModel, PrinterSeries | enums/printer |
| ActiveTool, ServiceState | enums/printer |
| AirConditioningMode | enums/printer |
| AMSModel, AMSSeries | enums/ams |
| AMSControlCommand, AMSUserSetting | enums/ams |
| AMSHeatingState, AMSDrySubStatus, AMSDryFanStatus | enums/ams |
| TrayState, ExtruderInfoState, ExtruderStatus | enums/ams |
| NozzleDiameter, NozzleType, NozzleFlowType | enums/filament |
| PlateType, PrintOption, DetectorSensitivity | enums/filament |
| Stage Mappings (parseStage) | enums/filament |
"""
