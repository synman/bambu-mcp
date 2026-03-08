"""
resources/knowledge.py — MCP resources exposing baked-in knowledge at bambu://knowledge/* URIs.

Top-level resources:
  bambu://knowledge/behavioral-rules  → knowledge/behavioral_rules.py:BEHAVIORAL_RULES_TEXT
  bambu://knowledge/protocol          → knowledge/protocol.py:PROTOCOL_TEXT
  bambu://knowledge/enums             → knowledge/enums.py:ENUMS_TEXT
  bambu://knowledge/api-reference     → knowledge/api_reference.py:API_REFERENCE_TEXT
  bambu://knowledge/references        → knowledge/references.py:REFERENCES_TEXT
  bambu://knowledge/fallback-strategy → knowledge/fallback_strategy.py:ESCALATION_POLICY_TEXT
  bambu://knowledge/http-api          → knowledge/http_api.py:HTTP_API_TEXT

Sub-topic resources (behavioral_rules):
  bambu://knowledge/behavioral-rules/camera        → behavioral_rules_camera.py
  bambu://knowledge/behavioral-rules/job-analysis  → behavioral_rules_job_analysis.py
  bambu://knowledge/behavioral-rules/print-state   → behavioral_rules_print_state.py
  bambu://knowledge/behavioral-rules/methodology   → behavioral_rules_methodology.py
  bambu://knowledge/behavioral-rules/mcp-patterns  → behavioral_rules_mcp_patterns.py

Sub-topic resources (protocol):
  bambu://knowledge/protocol/concepts → protocol_concepts.py
  bambu://knowledge/protocol/mqtt     → protocol_mqtt.py
  bambu://knowledge/protocol/hms      → protocol_hms.py
  bambu://knowledge/protocol/3mf      → protocol_3mf.py

Sub-topic resources (enums):
  bambu://knowledge/enums/printer  → enums_printer.py
  bambu://knowledge/enums/ams      → enums_ams.py
  bambu://knowledge/enums/filament → enums_filament.py

Sub-topic resources (api_reference):
  bambu://knowledge/api-reference/session     → api_reference_session.py
  bambu://knowledge/api-reference/files       → api_reference_files.py
  bambu://knowledge/api-reference/print       → api_reference_print.py
  bambu://knowledge/api-reference/ams         → api_reference_ams.py
  bambu://knowledge/api-reference/state       → api_reference_state.py
  bambu://knowledge/api-reference/dataclasses → api_reference_dataclasses.py
  bambu://knowledge/api-reference/camera      → api_reference_camera.py

Sub-topic resources (http_api):
  bambu://knowledge/http-api/printer  → http_api_printer.py
  bambu://knowledge/http-api/print    → http_api_print.py
  bambu://knowledge/http-api/ams      → http_api_ams.py
  bambu://knowledge/http-api/climate  → http_api_climate.py
  bambu://knowledge/http-api/hardware → http_api_hardware.py
  bambu://knowledge/http-api/files    → http_api_files.py
  bambu://knowledge/http-api/system   → http_api_system.py
"""

from knowledge.behavioral_rules import BEHAVIORAL_RULES_TEXT
from knowledge.behavioral_rules_camera import BEHAVIORAL_RULES_CAMERA_TEXT
from knowledge.behavioral_rules_job_analysis import BEHAVIORAL_RULES_JOB_ANALYSIS_TEXT
from knowledge.behavioral_rules_print_state import BEHAVIORAL_RULES_PRINT_STATE_TEXT
from knowledge.behavioral_rules_methodology import BEHAVIORAL_RULES_METHODOLOGY_TEXT
from knowledge.behavioral_rules_mcp_patterns import BEHAVIORAL_RULES_MCP_PATTERNS_TEXT
from knowledge.protocol import PROTOCOL_TEXT
from knowledge.protocol_concepts import PROTOCOL_CONCEPTS_TEXT
from knowledge.protocol_mqtt import PROTOCOL_MQTT_TEXT
from knowledge.protocol_hms import PROTOCOL_HMS_TEXT
from knowledge.protocol_3mf import PROTOCOL_3MF_TEXT
from knowledge.enums import ENUMS_TEXT
from knowledge.enums_printer import ENUMS_PRINTER_TEXT
from knowledge.enums_ams import ENUMS_AMS_TEXT
from knowledge.enums_filament import ENUMS_FILAMENT_TEXT
from knowledge.api_reference import API_REFERENCE_TEXT
from knowledge.api_reference_session import API_REFERENCE_SESSION_TEXT
from knowledge.api_reference_files import API_REFERENCE_FILES_TEXT
from knowledge.api_reference_print import API_REFERENCE_PRINT_TEXT
from knowledge.api_reference_ams import API_REFERENCE_AMS_TEXT
from knowledge.api_reference_state import API_REFERENCE_STATE_TEXT
from knowledge.api_reference_dataclasses import API_REFERENCE_DATACLASSES_TEXT
from knowledge.api_reference_camera import API_REFERENCE_CAMERA_TEXT
from knowledge.references import REFERENCES_TEXT
from knowledge.fallback_strategy import ESCALATION_POLICY_TEXT
from knowledge.http_api import HTTP_API_TEXT
from knowledge.http_api_printer import HTTP_API_PRINTER_TEXT
from knowledge.http_api_print import HTTP_API_PRINT_TEXT
from knowledge.http_api_ams import HTTP_API_AMS_TEXT
from knowledge.http_api_climate import HTTP_API_CLIMATE_TEXT
from knowledge.http_api_hardware import HTTP_API_HARDWARE_TEXT
from knowledge.http_api_files import HTTP_API_FILES_TEXT
from knowledge.http_api_system import HTTP_API_SYSTEM_TEXT

_KNOWLEDGE_MAP = {
    "behavioral-rules":                  BEHAVIORAL_RULES_TEXT,
    "behavioral-rules/camera":           BEHAVIORAL_RULES_CAMERA_TEXT,
    "behavioral-rules/job-analysis":     BEHAVIORAL_RULES_JOB_ANALYSIS_TEXT,
    "behavioral-rules/print-state":      BEHAVIORAL_RULES_PRINT_STATE_TEXT,
    "behavioral-rules/methodology":      BEHAVIORAL_RULES_METHODOLOGY_TEXT,
    "behavioral-rules/mcp-patterns":     BEHAVIORAL_RULES_MCP_PATTERNS_TEXT,
    "protocol":                          PROTOCOL_TEXT,
    "protocol/concepts":                 PROTOCOL_CONCEPTS_TEXT,
    "protocol/mqtt":                     PROTOCOL_MQTT_TEXT,
    "protocol/hms":                      PROTOCOL_HMS_TEXT,
    "protocol/3mf":                      PROTOCOL_3MF_TEXT,
    "enums":                             ENUMS_TEXT,
    "enums/printer":                     ENUMS_PRINTER_TEXT,
    "enums/ams":                         ENUMS_AMS_TEXT,
    "enums/filament":                    ENUMS_FILAMENT_TEXT,
    "api-reference":                     API_REFERENCE_TEXT,
    "api-reference/session":             API_REFERENCE_SESSION_TEXT,
    "api-reference/files":               API_REFERENCE_FILES_TEXT,
    "api-reference/print":               API_REFERENCE_PRINT_TEXT,
    "api-reference/ams":                 API_REFERENCE_AMS_TEXT,
    "api-reference/state":               API_REFERENCE_STATE_TEXT,
    "api-reference/dataclasses":         API_REFERENCE_DATACLASSES_TEXT,
    "api-reference/camera":              API_REFERENCE_CAMERA_TEXT,
    "references":                        REFERENCES_TEXT,
    "fallback-strategy":                 ESCALATION_POLICY_TEXT,
    "http-api":                          HTTP_API_TEXT,
    "http-api/printer":                  HTTP_API_PRINTER_TEXT,
    "http-api/print":                    HTTP_API_PRINT_TEXT,
    "http-api/ams":                      HTTP_API_AMS_TEXT,
    "http-api/climate":                  HTTP_API_CLIMATE_TEXT,
    "http-api/hardware":                 HTTP_API_HARDWARE_TEXT,
    "http-api/files":                    HTTP_API_FILES_TEXT,
    "http-api/system":                   HTTP_API_SYSTEM_TEXT,
}


def get_behavioral_rules() -> str:
    """Return the synthesized behavioral rules for the bambu-mcp agent."""
    return BEHAVIORAL_RULES_TEXT


def get_behavioral_rules_camera() -> str:
    """Return camera usage rules sub-topic (tool selection, HUD components, data_uri handling)."""
    return BEHAVIORAL_RULES_CAMERA_TEXT


def get_behavioral_rules_job_analysis() -> str:
    """Return analyze_active_job sub-topic (print_health, decision_confidence, categories, thresholds)."""
    return BEHAVIORAL_RULES_JOB_ANALYSIS_TEXT


def get_behavioral_rules_print_state() -> str:
    """Return printer state interpretation sub-topic (gcode_state FAILED, HMS, stage codes)."""
    return BEHAVIORAL_RULES_PRINT_STATE_TEXT


def get_behavioral_rules_methodology() -> str:
    """Return methodology sub-topic (KISS, quality-first, verification, telemetry parity)."""
    return BEHAVIORAL_RULES_METHODOLOGY_TEXT


def get_behavioral_rules_mcp_patterns() -> str:
    """Return MCP patterns sub-topic (array params, multi-level hierarchy, compressed responses)."""
    return BEHAVIORAL_RULES_MCP_PATTERNS_TEXT


def get_protocol() -> str:
    """Return the Bambu Lab protocol summary (MQTT, HMS, 3MF, SSDP overview)."""
    return PROTOCOL_TEXT


def get_protocol_concepts() -> str:
    """Return protocol concepts sub-topic (glossary: Bambu Lab, AMS, HMS, gcode_state, RTSPS, etc.)."""
    return PROTOCOL_CONCEPTS_TEXT


def get_protocol_mqtt() -> str:
    """Return MQTT sub-topic (topics, message types, home_flag bitfield, xcam fields)."""
    return PROTOCOL_MQTT_TEXT


def get_protocol_hms() -> str:
    """Return HMS sub-topic (error structure, print_error, two-command clear, firmware upgrade)."""
    return PROTOCOL_HMS_TEXT


def get_protocol_3mf() -> str:
    """Return 3MF sub-topic (structure, SSDP, AMS info, FTPS operations, extruder block)."""
    return PROTOCOL_3MF_TEXT


def get_enums() -> str:
    """Return all bpm enum definitions summary with sub-topic index."""
    return ENUMS_TEXT


def get_enums_printer() -> str:
    """Return printer enums sub-topic (PrinterModel, PrinterSeries, ActiveTool, ServiceState)."""
    return ENUMS_PRINTER_TEXT


def get_enums_ams() -> str:
    """Return AMS enums sub-topic (AMSModel, TrayState, ExtruderInfoState, etc.)."""
    return ENUMS_AMS_TEXT


def get_enums_filament() -> str:
    """Return filament enums sub-topic (NozzleDiameter, NozzleType, PlateType, PrintOption, Stage)."""
    return ENUMS_FILAMENT_TEXT


def get_api_reference() -> str:
    """Return the BambuPrinter API reference summary with sub-topic index."""
    return API_REFERENCE_TEXT


def get_api_reference_session() -> str:
    """Return API session sub-topic (BambuPrinter class, session management, send_gcode)."""
    return API_REFERENCE_SESSION_TEXT


def get_api_reference_files() -> str:
    """Return API files sub-topic (FTPS file management methods)."""
    return API_REFERENCE_FILES_TEXT


def get_api_reference_print() -> str:
    """Return API print sub-topic (print control, temperature, and fan methods)."""
    return API_REFERENCE_PRINT_TEXT


def get_api_reference_ams() -> str:
    """Return API AMS sub-topic (AMS, spool, calibration, hardware, xcam detector methods)."""
    return API_REFERENCE_AMS_TEXT


def get_api_reference_state() -> str:
    """Return API state sub-topic (properties, BambuConfig, PrinterCapabilities, BambuState)."""
    return API_REFERENCE_STATE_TEXT


def get_api_reference_dataclasses() -> str:
    """Return API dataclasses sub-topic (BambuSpool, ProjectInfo, ActiveJobInfo, utility functions)."""
    return API_REFERENCE_DATACLASSES_TEXT


def get_api_reference_camera() -> str:
    """Return API camera sub-topic (JobStateReport dataclass and background monitor result dict)."""
    return API_REFERENCE_CAMERA_TEXT


def get_references() -> str:
    """Return the list of authoritative sources for Bambu Lab protocol research."""
    return REFERENCES_TEXT


def get_fallback_strategy() -> str:
    """Return the 3-tier knowledge escalation policy text."""
    return ESCALATION_POLICY_TEXT


def get_http_api() -> str:
    """Return the HTTP REST API summary (base URL, auth, route category index, Swagger UI URL)."""
    return HTTP_API_TEXT


def get_http_api_printer() -> str:
    """Return HTTP API printer state routes (get_state, get_progress, temperatures, etc.)."""
    return HTTP_API_PRINTER_TEXT


def get_http_api_print() -> str:
    """Return HTTP API print control routes (print_3mf, pause, resume, stop, speed, etc.)."""
    return HTTP_API_PRINT_TEXT


def get_http_api_ams() -> str:
    """Return HTTP API AMS/filament routes (load, unload, set_filament, dryer, etc.)."""
    return HTTP_API_AMS_TEXT


def get_http_api_climate() -> str:
    """Return HTTP API climate/lighting routes (temps, fans, chamber light, etc.)."""
    return HTTP_API_CLIMATE_TEXT


def get_http_api_hardware() -> str:
    """Return HTTP API hardware routes (nozzle config, AI detector settings, etc.)."""
    return HTTP_API_HARDWARE_TEXT


def get_http_api_files() -> str:
    """Return HTTP API file management routes (list, upload, download, delete, print, etc.)."""
    return HTTP_API_FILES_TEXT


def get_http_api_system() -> str:
    """Return HTTP API system routes (session, printer CRUD, discovery, docs, health)."""
    return HTTP_API_SYSTEM_TEXT


def get_knowledge_resource(topic: str) -> str:
    """Get a knowledge resource by topic name. Returns error string if not found."""
    text = _KNOWLEDGE_MAP.get(topic)
    if text is None:
        available = list(_KNOWLEDGE_MAP.keys())
        return f"Unknown knowledge topic '{topic}'. Available: {available}"
    return text


def list_knowledge_resources() -> dict:
    """Return a summary of all available knowledge resources with their sizes."""
    return {
        f"bambu://knowledge/{name}": {"size_chars": len(text)}
        for name, text in _KNOWLEDGE_MAP.items()
    }
