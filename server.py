"""
server.py — Bambu Lab MCP Server entry point.

Registers all tools, resources, and prompts with FastMCP.
On startup: loads all MQTT sessions for configured printers.
On shutdown: stops all MQTT sessions cleanly.

Usage:
  python server.py                    # stdio transport (default, for Claude Desktop)
  python server.py --transport sse    # SSE transport
"""

import atexit
import logging
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.resources import FunctionResource

# ── Knowledge modules ──────────────────────────────────────────────────────────
from resources.rules import (
    get_global_rules,
    get_printer_app_rules,
    get_printer_manager_rules,
)
from resources.knowledge import (
    get_behavioral_rules,
    get_protocol,
    get_enums,
    get_api_reference,
    get_references,
    get_fallback_strategy,
)
from prompts.context import bambu_system_context

# ── Tool modules ───────────────────────────────────────────────────────────────
import tools.state as state_tools
import tools.print_control as print_control_tools
import tools.climate as climate_tools
import tools.filament as filament_tools
import tools.nozzle as nozzle_tools
import tools.detectors as detector_tools
import tools.management as management_tools
import tools.files as file_tools
import tools.system as system_tools
import tools.discovery as discovery_tools
import tools.commands as command_tools
import tools.knowledge_search as knowledge_tools
import tools.camera as camera_tools

_log_level = logging.DEBUG if os.environ.get("BAMBU_MCP_DEBUG") else logging.INFO
_log_file = Path(__file__).parent / "bambu-mcp.log"
_file_handler = logging.FileHandler(_log_file, encoding="utf-8")
_file_handler.setLevel(logging.DEBUG)  # always capture DEBUG to file when enabled
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
))
logging.basicConfig(level=_log_level, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger().addHandler(_file_handler)
logging.getLogger().setLevel(_log_level)
log = logging.getLogger("bambu-mcp")

# ── FastMCP server ─────────────────────────────────────────────────────────────
def _pkg_version() -> str:
    try:
        from importlib.metadata import version
        return version("bambu-mcp")
    except Exception:
        return "0.0.0"

mcp = FastMCP(
    name="bambu-mcp",
    instructions=(
        "Bambu Lab MCP server. Manages Bambu Lab 3D printers via direct MQTT sessions. "
        "All knowledge about printer protocols, enums, and behavioral rules is baked in. "
        "Use the bambu_system_context prompt to load full context before any printer work."
    ),
)
# FastMCP doesn't expose version in its constructor, but the underlying MCPServer does.
# Set it here so clients receive the correct version in the MCP initialize response.
mcp._mcp_server.version = _pkg_version()

# ── Register tools ─────────────────────────────────────────────────────────────
_TOOL_MODULES = [
    state_tools,
    print_control_tools,
    climate_tools,
    filament_tools,
    nozzle_tools,
    detector_tools,
    management_tools,
    file_tools,
    system_tools,
    discovery_tools,
    command_tools,
    knowledge_tools,
    camera_tools,
]

import inspect as _inspect

for _mod in _TOOL_MODULES:
    _mod_name = _mod.__name__
    for _name in dir(_mod):
        if _name.startswith("_"):
            continue
        _fn = getattr(_mod, _name)
        # Only register functions defined in this module (not imported names like Enum)
        if (
            callable(_fn)
            and hasattr(_fn, "__module__")
            and _fn.__module__ == _mod_name
            and hasattr(_fn, "__doc__")
            and _fn.__doc__
        ):
            mcp.add_tool(_fn)

# ── Register resources ─────────────────────────────────────────────────────────
_RESOURCES = [
    ("bambu://rules/global",            get_global_rules,           "Global copilot behavioral rules (live, sensitive values redacted)"),
    ("bambu://rules/printer-app",       get_printer_app_rules,      "bambu-printer-app rules file (live, redacted)"),
    ("bambu://rules/printer-manager",   get_printer_manager_rules,  "bambu-printer-manager rules file (live, redacted)"),
    ("bambu://knowledge/behavioral-rules", get_behavioral_rules,    "Synthesized behavioral rules knowledge module"),
    ("bambu://knowledge/protocol",      get_protocol,               "Bambu Lab MQTT/HMS/3MF/SSDP protocol documentation"),
    ("bambu://knowledge/enums",         get_enums,                  "All bpm enum values and definitions"),
    ("bambu://knowledge/api-reference", get_api_reference,          "Complete BambuPrinter API reference"),
    ("bambu://knowledge/references",    get_references,             "Authoritative source hierarchy for Bambu Lab research"),
    ("bambu://knowledge/fallback-strategy", get_fallback_strategy,  "3-tier knowledge escalation policy"),
]

for _uri, _fn, _desc in _RESOURCES:
    mcp.add_resource(
        FunctionResource(
            uri=_uri,
            name=_uri.split("/")[-1],
            description=_desc,
            mime_type="text/plain",
            fn=_fn,
        )
    )

# ── Register prompt ────────────────────────────────────────────────────────────
@mcp.prompt(
    name="bambu_system_context",
    description=(
        "Load full Bambu Lab MCP system context: behavioral rules, escalation policy, "
        "tool inventory, write protection rules, and authoritative sources. "
        "Call this at the start of every Bambu Lab session."
    ),
)
def _bambu_system_context_prompt() -> str:
    return bambu_system_context()


# ── Lifecycle ──────────────────────────────────────────────────────────────────
def _startup() -> None:
    """Start MQTT sessions for all configured printers and wire data_collector."""
    try:
        from session_manager import session_manager
        from data_collector import data_collector
        from auth import get_configured_printer_names

        printer_names = get_configured_printer_names()
        if not printer_names:
            log.info("No printers configured. Use add_printer() to add one.")
            return

        session_manager.start_all()

        for name in printer_names:
            data_collector.register_printer(name)
            session_manager.register_update_callback(
                lambda n=name: data_collector.on_update(n, session_manager.get_printer(n))
            )

        log.info(f"Started sessions for {len(printer_names)} printer(s): {printer_names}")

    except Exception as exc:
        log.warning(f"Startup error (sessions not started): {exc}")


def _shutdown() -> None:
    """Stop all MQTT sessions and camera streams on exit."""
    try:
        from camera.mjpeg_server import mjpeg_server
        mjpeg_server.stop_all()
    except Exception as exc:
        log.warning(f"Camera shutdown error: {exc}")
    try:
        from session_manager import session_manager
        session_manager.stop_all()
        log.info("All printer sessions stopped.")
    except Exception as exc:
        log.warning(f"Shutdown error: {exc}")


atexit.register(_shutdown)


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    """Entry point for the bambu-mcp CLI script."""
    _startup()
    transport = "stdio"
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg in ("--transport", "-t") and i < len(sys.argv) - 1:
            transport = sys.argv[i + 1]
        elif arg in ("sse", "stdio", "streamable-http"):
            transport = arg
    log.info(f"Starting Bambu Lab MCP server (transport: {transport})")
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
