"""
tools/commands.py — Raw MQTT command tool for Bambu Lab printers.

CAUTION: This module sends raw, unvalidated MQTT commands directly to the printer.
It bypasses all safety checks and command abstractions provided by higher-level tools.
Use only as a last resort when no other tool can accomplish the task.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

from session_manager import session_manager


def _permission_denied() -> str:
    return "Error: user_permission must be True to perform this action."


def send_mqtt_command(
    name: str,
    command_json: str,
    user_permission: bool = False,
) -> dict:
    """
    Send a raw MQTT command JSON string directly to the printer's request topic.

    THIS IS A LAST-RESORT TOOL. Use it only when no higher-level tool (print_control,
    climate, nozzle, filament, system, files, etc.) can accomplish the task. All
    validation and safety guardrails are bypassed. Incorrect commands can damage
    prints, trigger hardware faults, or put the printer into an unrecoverable state.

    user_permission must be explicitly True — this is a mandatory write-protection gate.
    command_json must be a valid JSON string. It is parsed and re-serialised before
    publishing to device/{serial}/request via printer.send_anything().
    The printer serial number is looked up automatically from the stored credentials for
    name — you do not need to supply it. command_json must be a valid JSON string matching
    the Bambu Lab MQTT command schema (see bambu://knowledge/protocol for field details).
    """
    log.debug("send_mqtt_command: called for %s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("send_mqtt_command: permission denied for %s", name)
        return {"error": _permission_denied()}
    printer = session_manager.get_printer(name)
    if printer is None:
        log.warning("send_mqtt_command: printer not connected: %s", name)
        return {"error": f"Printer '{name}' not connected"}
    log.debug("send_mqtt_command: validating JSON, length=%d", len(command_json))
    try:
        json.loads(command_json)
    except json.JSONDecodeError as e:
        log.debug("send_mqtt_command: JSON parse failed for %s: %s", name, e)
        return {"error": f"Invalid JSON: {e}"}
    try:
        log.debug("send_mqtt_command: sending command to %s: %s", name, command_json)
        printer.send_anything(command_json)
        log.info("send_mqtt_command: command sent to %s", name)
        log.debug("send_mqtt_command: → success, command=%s", command_json[:80])
        return {
            "success": True,
            "message": f"Command sent to '{name}'.",
            "command": json.loads(command_json),
        }
    except Exception as e:
        log.error("send_mqtt_command: error for %s: %s", name, e, exc_info=True)
        return {"error": f"Error sending command to '{name}': {e}"}
