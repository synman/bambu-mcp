"""
resources/knowledge.py — MCP resources exposing baked-in knowledge at bambu://knowledge/* URIs.

Available resources:
  bambu://knowledge/behavioral-rules  → knowledge/behavioral_rules.py:BEHAVIORAL_RULES_TEXT
  bambu://knowledge/protocol          → knowledge/protocol.py:PROTOCOL_TEXT
  bambu://knowledge/enums             → knowledge/enums.py:ENUMS_TEXT
  bambu://knowledge/api-reference     → knowledge/api_reference.py:API_REFERENCE_TEXT
  bambu://knowledge/references        → knowledge/references.py:REFERENCES_TEXT
  bambu://knowledge/fallback-strategy → knowledge/fallback_strategy.py:ESCALATION_POLICY_TEXT
"""

from knowledge.behavioral_rules import BEHAVIORAL_RULES_TEXT
from knowledge.protocol import PROTOCOL_TEXT
from knowledge.enums import ENUMS_TEXT
from knowledge.api_reference import API_REFERENCE_TEXT
from knowledge.references import REFERENCES_TEXT
from knowledge.fallback_strategy import ESCALATION_POLICY_TEXT

_KNOWLEDGE_MAP = {
    "behavioral-rules": BEHAVIORAL_RULES_TEXT,
    "protocol": PROTOCOL_TEXT,
    "enums": ENUMS_TEXT,
    "api-reference": API_REFERENCE_TEXT,
    "references": REFERENCES_TEXT,
    "fallback-strategy": ESCALATION_POLICY_TEXT,
}


def get_behavioral_rules() -> str:
    """Return the synthesized behavioral rules for the bambu-mcp agent."""
    return BEHAVIORAL_RULES_TEXT


def get_protocol() -> str:
    """Return the full Bambu Lab protocol documentation (MQTT, HMS, 3MF, SSDP)."""
    return PROTOCOL_TEXT


def get_enums() -> str:
    """Return all bpm enum definitions and their values."""
    return ENUMS_TEXT


def get_api_reference() -> str:
    """Return the complete BambuPrinter API reference."""
    return API_REFERENCE_TEXT


def get_references() -> str:
    """Return the list of authoritative sources for Bambu Lab protocol research."""
    return REFERENCES_TEXT


def get_fallback_strategy() -> str:
    """Return the 3-tier knowledge escalation policy text."""
    return ESCALATION_POLICY_TEXT


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
