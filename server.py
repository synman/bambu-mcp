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
import signal
import sys

import setproctitle
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.resources import FunctionResource

# ── Knowledge modules ──────────────────────────────────────────────────────────
from resources.rules import (
    get_global_rules,
    get_printer_app_rules,
    get_printer_manager_rules,
    get_bambu_mcp_rules,
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
import tools.camera as camera_tools
import tools.notifications as notification_tools
import tools.url_factory as url_factory_tools
import tools.charts as charts_tools

_env_level = os.environ.get("BAMBU_MCP_LOG_LEVEL", "WARNING").upper()
_log_level = getattr(logging, _env_level, logging.WARNING)
_log_file = Path(__file__).parent / "bambu-mcp.log"
_file_handler = logging.FileHandler(_log_file, encoding="utf-8")
_file_handler.setLevel(_log_level)
_file_handler.setFormatter(logging.Formatter(
    "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
))
# Stderr capped at WARNING regardless of log level — prevents Copilot stdio pipe overflow
_stream_handler = logging.StreamHandler()
_stream_handler.setLevel(logging.WARNING)
_stream_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
_root = logging.getLogger()
_root.setLevel(_log_level)
_root.addHandler(_file_handler)
_root.addHandler(_stream_handler)
_bpm_log_level = _log_level if os.environ.get("BAMBU_MCP_BPM_VERBOSE") else logging.WARNING
logging.getLogger("bpm").setLevel(_bpm_log_level)
log = logging.getLogger("bambu-mcp")

# ── Runtime log-level management ───────────────────────────────────────────────
_LOG_LEVEL_CYCLE = [logging.DEBUG, logging.INFO, logging.WARNING]


def set_log_level(level_name: str) -> str:
    """Update the root logger and file handler to *level_name* (case-insensitive).

    The stderr handler is intentionally left at WARNING to avoid flooding the
    Copilot stdio MCP transport.  The bpm logger is updated only when
    BAMBU_MCP_BPM_VERBOSE is not set (i.e. when it tracks the root level).
    """
    level = getattr(logging, level_name.upper(), None)
    if level is None:
        raise ValueError(f"Unknown log level: {level_name!r}")
    _root.setLevel(level)
    _file_handler.setLevel(level)
    if not os.environ.get("BAMBU_MCP_BPM_VERBOSE"):
        logging.getLogger("bpm").setLevel(level)
    log.warning("Log level changed to %s", logging.getLevelName(level))
    return logging.getLevelName(level)


def _cycle_log_level() -> None:
    """Cycle through DEBUG → INFO → WARNING → DEBUG (SIGUSR1 handler)."""
    current = _root.level
    idx = _LOG_LEVEL_CYCLE.index(current) if current in _LOG_LEVEL_CYCLE else 2
    next_level = _LOG_LEVEL_CYCLE[(idx + 1) % len(_LOG_LEVEL_CYCLE)]
    set_log_level(logging.getLevelName(next_level))

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
    camera_tools,
    notification_tools,
    url_factory_tools,
    charts_tools,
]

# Functions registered from url_factory_tools instead of their original modules.
# These names are skipped during registration of all other modules.
_URL_FACTORY_NAMES: frozenset[str] = frozenset({
    "get_snapshot",
    "get_monitoring_data",
    "get_monitoring_history",
    "get_monitoring_series",
})

import inspect as _inspect

for _mod in _TOOL_MODULES:
    _mod_name = _mod.__name__
    for _name in dir(_mod):
        if _name.startswith("_"):
            continue
        if _name in _URL_FACTORY_NAMES and _mod_name != "tools.url_factory":
            continue  # registered from url_factory_tools — skip original module version
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
    ("bambu://rules/bambu-mcp",         get_bambu_mcp_rules,        "bambu-mcp project rules file (live, redacted)"),
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

# ── Alert subscription resource template ──────────────────────────────────────
@mcp.resource(
    "bambu://alerts/{name}",
    description=(
        "Per-printer push alert feed. Subscribe to receive notifications when high-visibility "
        "printer state transitions occur (job start/finish/fail, HMS errors, health changes). "
        "Returns pending alerts as JSON array. Clear them after reading via get_pending_alerts(). "
        "See kb_get('bambu-state-change-alerts') (node-kb-mcp) for full alert type semantics."
    ),
    mime_type="application/json",
)
def _get_alerts_resource(name: str) -> str:
    """Return pending alerts for a printer as a JSON array (non-destructive read)."""
    import json
    try:
        from notifications import notifications as _notif
        alerts = _notif.get_pending(name, clear=False)
        return json.dumps(alerts, default=str)
    except Exception:
        return "[]"

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
    """Start MQTT sessions for all configured printers and wire data_collector + job_monitor."""
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

            def _data_cb(n: str = name) -> None:
                p = session_manager.get_printer(n)
                if p is not None:
                    data_collector.on_update(n, p)

            session_manager.register_update_callback(_data_cb)

        # Wire job monitor — auto-reference + 60s analysis loop per printer.
        try:
            from camera import job_monitor
            for name in printer_names:
                job_monitor.register(name)
                session_manager.register_update_callback(
                    lambda n=name: job_monitor.on_update(n)
                )
                log.info("job_monitor: registered for %s", name)
        except Exception as exc:
            log.warning("job_monitor startup error (monitor disabled): %s", exc)

        log.info(f"Started sessions for {len(printer_names)} printer(s): {printer_names}")

        # Wire notification manager — captures event loop for out-of-band resource push.
        # _startup() runs in a non-async daemon thread, so there is no running event
        # loop.  Create one explicitly rather than relying on get_event_loop().
        try:
            from notifications import notifications as _notifications
            import asyncio
            loop = asyncio.new_event_loop()
            _notifications.wire_mcp_server(mcp, loop)
        except Exception as exc:
            log.warning("notifications wiring error (push disabled): %s", exc)

    except Exception as exc:
        log.warning(f"Startup error (sessions not started): {exc}")

    try:
        import api_server
        api_server.start(log_level_setter=set_log_level)
    except Exception as exc:
        log.warning(f"API server startup error: {exc}")


def _shutdown() -> None:
    """Stop all MQTT sessions, camera streams, and job monitors on exit."""
    # api_server runs autonomously in a non-daemon thread — do not stop it here.
    # It keeps the process alive after the MCP stdio transport exits, and is
    # terminated only when the process is explicitly killed.
    try:
        from camera import job_monitor
        job_monitor.stop_all()
    except Exception as exc:
        log.warning(f"job_monitor shutdown error: {exc}")
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


def _signal_handler(signum: int, _frame) -> None:
    """Log the signal that killed us, then exit (atexit._shutdown will fire)."""
    sig_name = signal.Signals(signum).name
    log.warning("Received %s — shutting down", sig_name)
    sys.exit(128 + signum)


for _sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(_sig, _signal_handler)

signal.signal(signal.SIGUSR1, lambda _sig, _frame: _cycle_log_level())


# ── Main ───────────────────────────────────────────────────────────────────────
def main() -> None:
    """Entry point for the bambu-mcp CLI script."""
    setproctitle.setproctitle("bambu-mcp")
    transport = "stdio"
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg in ("--transport", "-t") and i < len(sys.argv) - 1:
            transport = sys.argv[i + 1]
        elif arg in ("sse", "stdio", "streamable-http"):
            transport = arg

    if transport == "streamable-http":
        import atexit
        import threading
        import daemon_port as _dp
        _dp.ensure_singleton()  # hard stop if a live daemon already exists
        _dp.set_daemon_mode(True)
        _port = _dp.resolve_port()
        _dp.write_pid_file()
        _dp.write_port_file(_port)
        atexit.register(_dp.cleanup_files)
        mcp.settings.host = "127.0.0.1"
        mcp.settings.port = _port
        # Run startup in a background thread — printer SSL handshakes can block
        # for 30+ seconds, exceeding the daemon script's port-file wait window.
        threading.Thread(target=_startup, daemon=True, name="bambu-mcp-startup").start()
        log.info(f"Starting Bambu Lab MCP server (transport: {transport}, port: {_port})")
        mcp.run(transport=transport)
    elif transport == "stdio":
        # Run startup in a background thread so mcp.run() can respond to the
        # MCP initialize request immediately. Copilot CLI has a 60s init timeout;
        # printer SSL handshakes block startup long enough to exceed it. Tools
        # are available instantly — printer sessions connect in the background
        # and become fully functional once established.
        import threading
        threading.Thread(target=_startup, daemon=True, name="bambu-mcp-startup").start()
        log.info(f"Starting Bambu Lab MCP server (transport: {transport})")
        mcp.run(transport=transport)
    else:
        _startup()
        log.info(f"Starting Bambu Lab MCP server (transport: {transport})")
        mcp.run(transport=transport)


if __name__ == "__main__":
    main()
