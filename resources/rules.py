"""
resources/rules.py — MCP resources exposing live rules file content at bambu://rules/* URIs.

Four slots, each read from the file its setting names. A slot with no setting answers
"not configured", so a fresh install serves nothing it was not pointed at:
  bambu://rules/global           BAMBU_MCP_RULES_GLOBAL
  bambu://rules/printer-app      BAMBU_MCP_RULES_PRINTER_APP
  bambu://rules/printer-manager  BAMBU_MCP_RULES_PRINTER_MANAGER
  bambu://rules/bambu-mcp        BAMBU_MCP_RULES_BAMBU_MCP

Sensitive values (IPs, serial numbers, access codes, passwords, CI tokens) are redacted before
serving. BAMBU_MCP_REDACT_HOST_PATTERN adds one regular expression for hostnames to redact; it
has no default.
"""

import os
import re
from pathlib import Path

_RULES_ENV = {
    "global": "BAMBU_MCP_RULES_GLOBAL",
    "printer-app": "BAMBU_MCP_RULES_PRINTER_APP",
    "printer-manager": "BAMBU_MCP_RULES_PRINTER_MANAGER",
    "bambu-mcp": "BAMBU_MCP_RULES_BAMBU_MCP",
}


def _rules_path(name: str) -> Path | None:
    var = _RULES_ENV.get(name)
    value = os.environ.get(var, "").strip() if var else ""
    return Path(value).expanduser() if value else None


# Patterns to redact from live rule files before serving
_REDACT_PATTERNS = [
    # IP addresses
    (re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), '<ip-redacted>'),
    # Serial numbers (Bambu format: hex-like 15-char strings)
    (re.compile(r'\b[0-9A-F]{15}\b'), '<serial-redacted>'),
    # Access codes (8-char alphanumeric)
    (re.compile(r'\b[A-Z0-9]{8}\b'), '<access-code-redacted>'),
    # Docker registry / CI tokens / passwords that might appear inline
    (re.compile(r'(?i)(password|token|secret|key)\s*[:=]\s*\S+'), r'\1: <redacted>'),
]


def _redact(text: str) -> str:
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    host_pattern = os.environ.get("BAMBU_MCP_REDACT_HOST_PATTERN", "").strip()
    if host_pattern:
        text = re.sub(host_pattern, '<hostname-redacted>', text)
    return text


def _read_rules(name: str) -> str:
    if name not in _RULES_ENV:
        return f"Unknown rules resource: {name}"
    path = _rules_path(name)
    if path is None:
        return f"Rules file not configured: set {_RULES_ENV[name]}"
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
    """Return the print app copilot-instructions.md with sensitive values redacted."""
    return _read_rules("printer-app")


def get_printer_manager_rules() -> str:
    """Return bambu-printer-manager copilot-instructions.md with sensitive values redacted."""
    return _read_rules("printer-manager")


def get_bambu_mcp_rules() -> str:
    """Return bambu-mcp copilot-instructions.md with sensitive values redacted."""
    return _read_rules("bambu-mcp")


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
