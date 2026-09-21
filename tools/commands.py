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


def _permission_denied(consequence: str) -> str:
    return f"Error: user_permission must be True to perform this action. {consequence}"


def send_mqtt_command(
    name: str,
    command_json: str,
    user_permission: bool = False,
) -> dict:
    """
    Send a raw MQTT command JSON string directly to the printer's request topic.

    WHEN to use: as a LAST RESORT, only when no dedicated tool can accomplish the task (for
    example ``send_gcode``, ``swap_tool``, ``set_print_speed``, ``set_nozzle_temp``,
    ``set_bed_temp``, ``set_fan_speed``, ``load_filament``, ``print_file`` or
    ``set_print_option``). All validation and safety guardrails are bypassed. Incorrect commands can damage prints, trigger hardware faults,
    or put the printer into an unrecoverable state.

    WRITE GUARD: publishes the command to the physical printer's MQTT request topic
    (device/{serial}/request) with no validation beyond JSON syntax, so whatever the
    command asks the firmware to do happens on the hardware and may not be reversible.
    ``user_permission`` must be explicitly True (mandatory write-protection gate). With it
    False (the default) the tool changes nothing, contacts no printer, and returns the
    refusal ``{"error": "Error: user_permission must be True to perform this action. <what
    this would do>"}``.

    Sibling disambiguation: ``send_mqtt_command`` publishes any MQTT JSON command and is NOT
    gated by the active-print guard; ``send_gcode`` sends only G-code text, and is blocked
    while a print is active (gcode_state RUNNING/PREPARE). Prefer the dedicated tool
    (``send_gcode``, ``swap_tool``, ``set_print_speed``, ...) whenever one exists.

    Args:
        name: Configured printer name; the printer serial number is looked up automatically
            from the stored credentials for it, so you do not supply the serial.
        command_json: A valid JSON string matching the Bambu Lab MQTT command schema (see
            kb_get('bambu-mqtt-commands') for field details). It is parsed and re-serialised
            before publishing to device/{serial}/request via printer.send_anything().
        user_permission: Must be explicitly True to send; defaults to False (refuses).

    Returns:
        On success: ``{"success": True, "message": "Command sent to '<name>'.", "command":
        <the parsed command>}``. On failure: ``{"error": str}``, with the message being the
        refusal above when ``user_permission`` is False, ``"Printer '<name>' not
        connected"`` (returned only when no session exists under that name, not when the
        session's MQTT link is down), ``"Invalid JSON: <detail>"``, or ``"Error sending
        command to '<name>': <detail>"``.

    Notes:
        ``success`` means the command was handed to the MQTT client: the publish result is not
        checked and the firmware's acceptance is not confirmed. If the printer's MQTT connection
        is down the client may hold the message and deliver it after it reconnects, at an
        unpredictable time and possibly mid-print, so re-check ``get_printer_state`` and
        ``get_printer_connection_status`` before assuming the command did or did not run.

        CAUTION: this tool is intentionally NOT gated by the active-print guard because it
        exists as a bypass mechanism. However, sending gcode_line or set_active_tool commands
        through it while a print is active (gcode_state RUNNING/PREPARE) is EXTREMELY
        DANGEROUS: firmware does NOT reject injected G-code or tool swaps mid-print. The agent
        MUST NOT use this tool to circumvent the active-print guard on send_gcode(),
        swap_tool(), or any other gated tool.
    """
    log.debug("send_mqtt_command: called for %s user_permission=%s", name, user_permission)
    if not user_permission:
        log.debug("send_mqtt_command: permission denied for %s", name)
        return {
            "error": _permission_denied(
                "This would publish a raw, unvalidated MQTT command to the printer's request "
                "topic, bypassing every safety check including the active-print guard."
            )
        }
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
