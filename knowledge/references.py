"""
references.py — Authoritative source hierarchy for Bambu Lab protocol/API work.

Sourced from:
  ~/bambu-printer-manager/.github/copilot-instructions.md
  ~/bambu-printer-app/.github/copilot-instructions.md
  ~/.copilot/copilot-instructions.md
"""

from typing import Any

REFERENCES: list[dict[str, Any]] = [
    {
        "name": "BambuStudio",
        "url": "https://github.com/bambulab/BambuStudio",
        "tier": 1,
        "category": "official_vendor",
        "description": (
            "Official Bambu Lab open-source slicer and client. "
            "Authoritative source for protocol definitions, firmware integration, "
            "MQTT command/telemetry formats, AMS mapping logic (DevMapping.cpp, "
            "DeviceManager.cpp), xcam field semantics, and home_flag bitfield layout. "
            "Highest-authority source for field semantics and intended behavior."
        ),
        "key_files": [
            "src/slic3r/GUI/DeviceCore/DevMapping.cpp",
            "src/slic3r/GUI/DeviceCore/DeviceManager.cpp",
            "src/slic3r/GUI/DeviceCore/NetworkAgent.cpp",
        ],
    },
    {
        "name": "ha-bambulab / pybambu",
        "url": "https://github.com/greghesp/ha-bambulab",
        "pybambu_url": "https://github.com/greghesp/ha-bambulab/tree/main/custom_components/bambu_lab/pybambu",
        "tier": 2,
        "category": "platform_integration",
        "description": (
            "Home Assistant integration for Bambu Lab printers. "
            "The pybambu subdirectory is a low-level Python MQTT client implementation "
            "with comprehensive field-level documentation, edge-case handling derived "
            "from real-world usage, message parsing, and telemetry field mappings. "
            "Best source for field semantics and cross-model edge cases."
        ),
        "key_files": [
            "custom_components/bambu_lab/pybambu/bambu_client.py",
            "custom_components/bambu_lab/pybambu/models.py",
            "custom_components/bambu_lab/pybambu/const.py",
        ],
    },
    {
        "name": "OpenBambuAPI",
        "url": "https://github.com/Doridian/OpenBambuAPI",
        "tier": 2,
        "category": "community_implementation",
        "description": (
            "Alternative API implementation with detailed protocol documentation "
            "for undocumented Bambu Lab printer endpoints. Covers MQTT message "
            "structures, command formats, AMS mapping configuration, and "
            "protocol behaviors not documented in official sources."
        ),
        "key_files": [
            "mqtt.md",
            "ftp.md",
        ],
    },
    {
        "name": "X1Plus",
        "url": "https://github.com/X1Plus/X1Plus",
        "tier": 2,
        "category": "community_firmware",
        "description": (
            "Community firmware and protocol analysis for extended Bambu Lab printer "
            "capabilities. Valuable for firmware internals, low-level telemetry "
            "field analysis, and undocumented behavior in X1-series printers."
        ),
    },
    {
        "name": "OrcaSlicer",
        "url": "https://github.com/OrcaSlicer/OrcaSlicer",
        "tier": 2,
        "category": "community_implementation",
        "description": (
            "Community fork of BambuStudio with enhanced telemetry handling, "
            "data structure examples, and slicer features. Useful for cross-verifying "
            "3MF structure parsing, filament color handling, and AMS mapping logic. "
            "Also contains independent implementations of MQTT command handling."
        ),
    },
    {
        "name": "bambu-node",
        "url": "https://github.com/THE-SIMPLE-MARK/bambu-node",
        "tier": 3,
        "category": "cross_language",
        "description": (
            "Node.js implementation of the Bambu Lab printer API. "
            "Provides independent cross-language verification of field interpretations "
            "and protocol behavior. Useful for validating field semantics against "
            "an implementation in a completely different language and ecosystem."
        ),
    },
    {
        "name": "Bambu-HomeAssistant-Flows",
        "url": "https://github.com/WolfwithSword/Bambu-HomeAssistant-Flows",
        "tier": 3,
        "category": "platform_integration",
        "description": (
            "Node-RED workflow patterns and integration examples using Bambu Lab "
            "printers with Home Assistant. Provides practical integration patterns, "
            "MQTT topic usage examples, and real-world automation flows that "
            "demonstrate how fields are consumed in production."
        ),
    },
]

REFERENCES_TEXT: str = """
# Authoritative Sources for Bambu Lab Protocol / API Work

Before starting any protocol, API, or telemetry work, establish a source hierarchy.
Use the nearest tier that answers the question. Document which tier was used.

---

## Source Hierarchy (Priority Order)

### Tier 1 — Official Vendor Sources (Highest Authority)

**BambuStudio**
URL: https://github.com/bambulab/BambuStudio
Category: Official Bambu Lab open-source slicer and client
Use for: Protocol definitions, firmware integration, MQTT command/telemetry formats,
AMS mapping logic (DevMapping.cpp, DeviceManager.cpp), xcam field semantics,
home_flag bitfield layout. Authoritative source for field semantics and intended behavior.
Key files: src/slic3r/GUI/DeviceCore/DevMapping.cpp,
           src/slic3r/GUI/DeviceCore/DeviceManager.cpp

---

### Tier 2 — Platform Integrations & Community Implementations

**ha-bambulab / pybambu**
URL: https://github.com/greghesp/ha-bambulab
pybambu: https://github.com/greghesp/ha-bambulab/tree/main/custom_components/bambu_lab/pybambu
Category: Home Assistant integration + low-level Python MQTT client
Use for: Comprehensive field-level documentation, edge-case handling from real-world
usage, message parsing, telemetry field mappings, cross-model behavior.
Best source for field semantics and edge cases.

**OpenBambuAPI**
URL: https://github.com/Doridian/OpenBambuAPI
Category: Alternative API implementation with detailed protocol documentation
Use for: Undocumented Bambu Lab printer endpoints, MQTT message structures, command
formats, AMS mapping configuration, protocol behaviors not in official docs.
Key files: mqtt.md, ftp.md

**X1Plus**
URL: https://github.com/X1Plus/X1Plus
Category: Community firmware and protocol analysis
Use for: Firmware internals, low-level telemetry field analysis, undocumented
behavior in X1-series printers.

**OrcaSlicer**
URL: https://github.com/OrcaSlicer/OrcaSlicer
Category: BambuStudio community fork with enhanced telemetry handling
Use for: Cross-verifying 3MF structure parsing, filament color handling, AMS mapping
logic, slicer features. Independent MQTT command handling implementations.

---

### Tier 3 — Cross-Language and Ecosystem Integrations

**bambu-node**
URL: https://github.com/THE-SIMPLE-MARK/bambu-node
Category: Node.js implementation (cross-language verification)
Use for: Independent cross-language verification of field interpretations and
protocol behavior. Validates field semantics from a different ecosystem.

**Bambu-HomeAssistant-Flows**
URL: https://github.com/WolfwithSword/Bambu-HomeAssistant-Flows
Category: Node-RED workflow patterns
Use for: Practical MQTT topic usage examples, real-world automation flows,
integration patterns showing how fields are consumed in production.

---

## Authoritative Source Checklist

Before coding any protocol/field mapping:
- [ ] Did I identify at least one official vendor source (Tier 1)?
- [ ] Did I identify at least one community integration with active maintenance (Tier 2)?
- [ ] Did I confirm which source governs steady-state field values vs. transient acks?
- [ ] Are my sources current (not stale forks or archived repos)?
- [ ] Did I document which tier resolved the question?

## Steady-State vs. Command Ack Sources

CRITICAL DISTINCTION:
- Command ack payloads (`"result": "success"`) confirm command ACCEPTANCE only.
  They are transient and do NOT represent steady-state printer state.
- Status payloads (push_status) and bitfields (home_flag, xcam.cfg, fun, stat)
  confirm STEADY-STATE state.

For any field, always implement using the steady-state source unless proven otherwise.
"""
