"""tools/_guards.py — Shared safety guards for MCP tools and HTTP routes."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def check_active_print_guard(printer, name: str, operation: str) -> dict | None:
    """Return an error dict if the printer is actively printing; None if safe.

    Blocks when gcode_state is RUNNING or PREPARE — these states indicate the
    printer is executing toolpath moves or preparing to do so.  Sending
    disruptive commands (GCode injection, tool swaps, filament changes, new
    print jobs) during these states risks toolhead crashes, failed prints, or
    hardware damage.

    Args:
        printer: BambuPrinter instance (already resolved from session_manager).
        name: Human-readable printer name (for error messages).
        operation: Short description of the blocked operation (for error messages).

    Returns:
        dict with "error" key if blocked, or None if the operation may proceed.
    """
    try:
        state = printer.printer_state
        gcode_state = getattr(state, "gcode_state", None) or ""
    except Exception:
        gcode_state = ""

    if gcode_state.upper() in ("RUNNING", "PREPARE"):
        log.warning(
            "active_print_guard: BLOCKED %s on '%s' — gcode_state=%s",
            operation, name, gcode_state,
        )
        return {
            "error": (
                f"Blocked: '{name}' is currently {gcode_state}. "
                f"{operation} is not safe while a print is active. "
                "Wait for the print to finish, pause it first, or cancel it."
            ),
        }
    return None
