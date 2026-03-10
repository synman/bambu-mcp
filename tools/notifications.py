"""
tools/notifications.py — Pending alert retrieval for Bambu Lab printers.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

from notifications import notifications as _notifications


def get_pending_alerts(name: str, clear: bool = True) -> list[dict]:
    """
    Return pending state-change alerts for the named printer.

    Alerts are generated automatically when high-visibility state transitions
    are detected: gcode_state changes, notable stage changes, HMS error fault
    changes, and significant job health verdict shifts.

    This tool works regardless of whether the MCP client supports resource
    subscriptions. It is the recommended polling path for all clients.

    name: printer name (required).
    clear: if True (default), the pending alert queue is emptied after reading.
        Pass False to peek without consuming.

    Returns a list of alert dicts. Empty list means no new transitions since
    the last call (or since the printer session started).

    Each alert dict:
      type      — alert type key. One of: job_started, job_finished, job_failed,
                  job_paused, job_resumed, stage_change, hms_error_new,
                  hms_error_cleared, health_escalated, health_recovered.
      printer   — printer name string.
      timestamp — ISO 8601 UTC timestamp when the transition was detected.
      severity  — "high", "medium", or "low".
      payload   — type-specific fields (see knowledge/behavioral_rules/alerts).

    Payload fields by type:
      job_started:       subtask_name, gcode_file, plate_num
      job_finished:      subtask_name, gcode_file, plate_num, elapsed_min, layer_num
      job_failed:        subtask_name, gcode_file, plate_num
      job_paused:        subtask_name, gcode_file, plate_num, stage_id, stage_name
      job_resumed:       subtask_name, gcode_file, plate_num
      stage_change:      stage_id, stage_name, prev_stage_id, prev_stage_name
      hms_error_new:     errors=[{code, description}, ...]
      hms_error_cleared: prev_error_count
      health_escalated:  from_verdict, to_verdict, score
      health_recovered:  from_verdict, to_verdict, score

    Call get_knowledge_topic('behavioral_rules/alerts') for full semantic
    documentation on each alert type, recommended actions, and severity guidance.
    """
    from session_manager import session_manager
    printer = session_manager.get_printer(name)
    if printer is None:
        return [{"error": f"Printer '{name}' not connected"}]
    return _notifications.get_pending(name, clear=clear)
