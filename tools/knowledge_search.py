"""
tools/knowledge_search.py — Knowledge escalation tools for Bambu Lab protocol/API work.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_KNOWN_TOPICS: dict[str, tuple[str, str]] = {
    # Top-level topics
    "behavioral_rules": ("knowledge.behavioral_rules", "BEHAVIORAL_RULES_TEXT"),
    "protocol":         ("knowledge.protocol",          "PROTOCOL_TEXT"),
    "enums":            ("knowledge.enums",             "ENUMS_TEXT"),
    "api_reference":    ("knowledge.api_reference",     "API_REFERENCE_TEXT"),
    "references":       ("knowledge.references",        "REFERENCES_TEXT"),
    "fallback_strategy":("knowledge.fallback_strategy", "ESCALATION_POLICY_TEXT"),
    "http_api":         ("knowledge.http_api",          "HTTP_API_TEXT"),
    # behavioral_rules sub-topics
    "behavioral_rules/camera":           ("knowledge.behavioral_rules_camera",          "BEHAVIORAL_RULES_CAMERA_TEXT"),
    "behavioral_rules/job_analysis":     ("knowledge.behavioral_rules_job_analysis",    "BEHAVIORAL_RULES_JOB_ANALYSIS_TEXT"),
    "behavioral_rules/print_state":      ("knowledge.behavioral_rules_print_state",     "BEHAVIORAL_RULES_PRINT_STATE_TEXT"),
    "behavioral_rules/methodology": ("knowledge.behavioral_rules_methodology", "BEHAVIORAL_RULES_METHODOLOGY_TEXT"),
    "behavioral_rules/mcp_patterns":("knowledge.behavioral_rules_mcp_patterns","BEHAVIORAL_RULES_MCP_PATTERNS_TEXT"),
    "behavioral_rules/alerts":      ("knowledge.behavioral_rules_alerts",       "BEHAVIORAL_RULES_ALERTS_TEXT"),
    "behavioral_rules/session":     ("knowledge.behavioral_rules_session",      "BEHAVIORAL_RULES_SESSION_TEXT"),
    # api_reference sub-topics
    "api_reference/session":        ("knowledge.api_reference_session",        "API_REFERENCE_SESSION_TEXT"),
    "api_reference/files":          ("knowledge.api_reference_files",          "API_REFERENCE_FILES_TEXT"),
    "api_reference/print":          ("knowledge.api_reference_print",          "API_REFERENCE_PRINT_TEXT"),
    "api_reference/ams":            ("knowledge.api_reference_ams",            "API_REFERENCE_AMS_TEXT"),
    "api_reference/state":          ("knowledge.api_reference_state",          "API_REFERENCE_STATE_TEXT"),
    "api_reference/dataclasses":    ("knowledge.api_reference_dataclasses",    "API_REFERENCE_DATACLASSES_TEXT"),
    "api_reference/camera":         ("knowledge.api_reference_camera",         "API_REFERENCE_CAMERA_TEXT"),
    # protocol sub-topics
    "protocol/concepts":            ("knowledge.protocol_concepts",            "PROTOCOL_CONCEPTS_TEXT"),
    "protocol/mqtt":                ("knowledge.protocol_mqtt",                "PROTOCOL_MQTT_TEXT"),
    "protocol/hms":                 ("knowledge.protocol_hms",                 "PROTOCOL_HMS_TEXT"),
    "protocol/3mf":                 ("knowledge.protocol_3mf",                 "PROTOCOL_3MF_TEXT"),
    # enums sub-topics
    "enums/printer":                ("knowledge.enums_printer",                "ENUMS_PRINTER_TEXT"),
    "enums/ams":                    ("knowledge.enums_ams",                    "ENUMS_AMS_TEXT"),
    "enums/filament":               ("knowledge.enums_filament",               "ENUMS_FILAMENT_TEXT"),
    # http_api sub-topics
    "http_api/printer":  ("knowledge.http_api_printer",  "HTTP_API_PRINTER_TEXT"),
    "http_api/print":    ("knowledge.http_api_print",    "HTTP_API_PRINT_TEXT"),
    "http_api/ams":      ("knowledge.http_api_ams",      "HTTP_API_AMS_TEXT"),
    "http_api/climate":  ("knowledge.http_api_climate",  "HTTP_API_CLIMATE_TEXT"),
    "http_api/hardware": ("knowledge.http_api_hardware", "HTTP_API_HARDWARE_TEXT"),
    "http_api/files":    ("knowledge.http_api_files",    "HTTP_API_FILES_TEXT"),
    "http_api/system":   ("knowledge.http_api_system",   "HTTP_API_SYSTEM_TEXT"),
}


def search_authoritative_sources(
    query: str,
    repo_filter: str | None = None,
) -> dict:
    """
    Return structured guidance on searching the authoritative Bambu Lab repos for a query.

    Lists which repos to search in priority order (Tier 1 → Tier 3) with GitHub
    search URL patterns. If repo_filter is provided, narrows guidance to that repo.
    This tool does not perform the actual search — it provides instructions and URLs
    for the caller to execute. Use search_code or a browser for the actual lookup.
    Follows a 3-tier escalation policy: Tier 1 = baked-in knowledge modules (fastest,
    offline). Tier 2 = authoritative repos (BambuStudio, ha-bambulab, OpenBambuAPI).
    Tier 3 = broad web/GitHub search (last resort). This tool returns guidance for
    Tier 2+ searches — it does not perform the search itself.
    """
    log.debug("search_authoritative_sources: query=%s repo_filter=%s", query, repo_filter)
    from knowledge.fallback_strategy import AUTHORITATIVE_REPOS

    repos = AUTHORITATIVE_REPOS
    if repo_filter:
        repos = [
            r for r in repos
            if repo_filter.lower() in r["name"].lower()
            or repo_filter.lower() in r.get("repo", "").lower()
        ]
        if not repos:
            return {
                "error": f"No repos matched filter '{repo_filter}'",
                "available_repos": [r["name"] for r in AUTHORITATIVE_REPOS],
            }

    encoded_query = query.replace(" ", "+")
    search_guidance = []
    for r in repos:
        repo_path = r.get("repo", "")
        search_guidance.append({
            "name": r["name"],
            "url": r.get("url", ""),
            "repo": repo_path,
            "scope": r.get("scope", ""),
            "github_search_url": (
                f"https://github.com/search?q={encoded_query}+repo:{repo_path}&type=code"
                if repo_path else ""
            ),
            "github_code_search": (
                f"https://github.com/{repo_path}/search?q={encoded_query}"
                if repo_path else ""
            ),
        })

    log.debug("search_authoritative_sources: returning %d repos", len(search_guidance))
    return {
        "query": query,
        "repo_filter": repo_filter,
        "search_guidance": search_guidance,
        "instructions": (
            "Search each repo in priority order. Prefer Tier 1 (official vendor) sources first. "
            "Use github_search_url for GitHub code search or clone locally for grep. "
            "Verify field semantics using steady-state status payloads, not command acks."
        ),
    }

def get_knowledge_topic(topic: str) -> dict | str:
    """
    Return the full text of a knowledge module by topic name.

    topic must be one of: behavioral_rules, protocol, enums, api_reference,
    references, fallback_strategy, http_api. Returns a list of available topics if the
    given topic is not recognized.
    Returns the full text of the named knowledge module as a string. Returns a list
    of available topic names if the given topic is not recognized.

    Sub-topics use slash notation and return focused content slices (≤10 KB each):
    - behavioral_rules/camera — camera tool selection, stream HUD components, data_uri handling
    - behavioral_rules/job_analysis — analyze_active_job: print_health, decision_confidence, categories, thresholds
    - behavioral_rules/print_state — gcode_state FAILED, HMS active/historical, stage codes
    - behavioral_rules/methodology — KISS, quality-first, verification, parity, cross-model
    - behavioral_rules/mcp_patterns — array param pattern, multi-level hierarchy, compressed responses
    - behavioral_rules/alerts — push alert types, semantics, severity, recommended agent actions
    - behavioral_rules/session — printer name verification, post-reload checklist
    - api_reference/session — BambuPrinter session management and raw command methods
    - api_reference/files — FTPS file management methods
    - api_reference/print — print control, temperature, and fan methods
    - api_reference/ams — AMS, spool, calibration, hardware, and xcam detector methods
    - api_reference/state — properties, BambuConfig, PrinterCapabilities, BambuState
    - api_reference/dataclasses — BambuSpool, ProjectInfo, ActiveJobInfo, utility functions
    - api_reference/camera — JobStateReport dataclass and background monitor result dict
    - protocol/concepts — Bambu Lab protocol glossary and terminology
    - protocol/mqtt — MQTT topics, message types, home_flag bitfield, xcam fields
    - protocol/hms — HMS error structure and firmware upgrade state fields
    - protocol/3mf — 3MF structure, SSDP, AMS info parsing, FTPS, extruder block
    - enums/printer — PrinterModel, PrinterSeries, ActiveTool, ServiceState, AirConditioningMode
    - enums/ams — AMS, TrayState, ExtruderInfoState, ExtruderStatus enums
    - enums/filament — NozzleDiameter, NozzleType, PlateType, PrintOption, Stage enums
    - http_api/printer — printer state and session management REST routes
    - http_api/print — print control REST routes (start, pause, stop, speed, skip, gcode)
    - http_api/ams — AMS and filament REST routes
    - http_api/climate — temperature, fan, and lighting REST routes
    - http_api/hardware — nozzle config and AI vision detector REST routes
    - http_api/files — SD card file management REST routes
    - http_api/system — system, diagnostics, API documentation REST routes, and mDNS/Zeroconf service discovery
    """
    log.debug("get_knowledge_topic: topic=%s", topic)
    if topic not in _KNOWN_TOPICS:
        log.warning("get_knowledge_topic: unknown topic '%s'", topic)
        return {
            "error": f"Unknown topic '{topic}'",
            "available_topics": list(_KNOWN_TOPICS.keys()),
        }
    module_name, attr_name = _KNOWN_TOPICS[topic]
    try:
        import importlib
        log.debug("get_knowledge_topic: loading module %s", module_name)
        mod = importlib.import_module(module_name)
        text = getattr(mod, attr_name, None)
        if text is None:
            return {"error": f"Attribute '{attr_name}' not found in module '{module_name}'"}
        log.debug("get_knowledge_topic: returning text length=%d", len(text) if text else 0)
        return text
    except Exception as e:
        return {"error": f"Error loading knowledge topic '{topic}': {e}"}
