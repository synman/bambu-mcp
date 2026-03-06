"""
resources/rules.py — MCP resources exposing live rules file content at bambu://rules/* URIs.

Three rule sources:
  bambu://rules/global          ~/.copilot/copilot-instructions.md (global rules)
  bambu://rules/printer-app     ~/bambu-printer-app/.github/copilot-instructions.md
  bambu://rules/printer-manager ~/bambu-printer-manager/.github/copilot-instructions.md

Sensitive values (IPs, serial numbers, access codes, passwords, Docker infra, CI tokens)
are redacted before serving. The baked-in knowledge/behavioral_rules.py already contains
a cleaned synthesis; these resources allow the agent to verify rules haven't changed.
"""

import re
from pathlib import Path

_RULES_FILES = {
    "global": Path.home() / ".copilot" / "copilot-instructions.md",
    "printer-app": Path.home() / "bambu-printer-app" / ".github" / "copilot-instructions.md",
    "printer-manager": Path.home() / "bambu-printer-manager" / ".github" / "copilot-instructions.md",
}

# Patterns to redact from live rule files before serving
_REDACT_PATTERNS = [
    # IP addresses
    (re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), '<ip-redacted>'),
    # Serial numbers (Bambu format: hex-like 15-char strings)
    (re.compile(r'\b[0-9A-F]{15}\b'), '<serial-redacted>'),
    # Hostnames with dots that look like printer hostnames
    (re.compile(r'bambu-[a-z0-9]+\.shellware\.com'), '<hostname-redacted>'),
    # Access codes (8-char alphanumeric)
    (re.compile(r'\b[A-Z0-9]{8}\b'), '<access-code-redacted>'),
    # Docker registry / CI tokens / passwords that might appear inline
    (re.compile(r'(?i)(password|token|secret|key)\s*[:=]\s*\S+'), r'\1: <redacted>'),
]


def _redact(text: str) -> str:
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _read_rules(name: str) -> str:
    path = _RULES_FILES.get(name)
    if path is None:
        return f"Unknown rules resource: {name}"
    if not path.exists():
        return f"Rules file not found: {path}"
    try:
        raw = path.read_text(encoding="utf-8")
        return _redact(raw)
    except Exception as exc:
        return f"Error reading rules file: {exc}"


def get_global_rules() -> str:
    """Return the global copilot-instructions.md with sensitive values redacted."""
    return _read_rules("global")


def get_printer_app_rules() -> str:
    """Return bambu-printer-app copilot-instructions.md with sensitive values redacted."""
    return _read_rules("printer-app")


def get_printer_manager_rules() -> str:
    """Return bambu-printer-manager copilot-instructions.md with sensitive values redacted."""
    return _read_rules("printer-manager")


def list_rules_resources() -> dict:
    """Return a summary of available rules resources and their availability."""
    result = {}
    for name, path in _RULES_FILES.items():
        result[f"bambu://rules/{name}"] = {
            "available": path.exists(),
            "path": str(path),
            "size_bytes": path.stat().st_size if path.exists() else 0,
        }
    return result
