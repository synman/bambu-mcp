"""
tools/notifications.py — Pending alert retrieval for Bambu Lab printers.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

from notifications import notifications as _notifications


def get_pending_alerts(name: str, clear: bool = True) -> list[dict]:
    """Return the queued state-change alerts for the named printer.

    WHEN to use: poll for high-visibility transitions (job started, finished, failed, paused or
    resumed, new or cleared HMS errors, job health shifts) since the last call. This works
    whether or not the MCP client supports resource subscriptions, and is the recommended
    polling path for all clients.

    Sibling disambiguation: ``get_pending_alerts`` returns the queue of transitions that
    happened since you last read it, and by default empties that queue. ``get_hms_errors``
    returns the HMS errors that are active right now, and ``get_job_info`` returns the
    current job's state. Neither of those is a history of changes.

    Args:
        name: Printer name (required).
        clear: If True (default), the pending alert queue is emptied after reading, so each
            alert is returned once. Pass False to peek without consuming.

    Returns:
        A list of alert dicts. An empty list means nothing is queued: no new transitions, or
        another consumer drained the queue first (see Notes). If the printer is not
        connected, a one-element list ``[{"error": "Printer '<name>' not connected"}]``.

        Each alert dict has:
          type      -- alert type key. One of: job_started, job_finished, job_failed,
                       job_paused, job_resumed, stage_change, hms_error_new,
                       hms_error_cleared, health_escalated, health_recovered.
          printer   -- printer name string.
          timestamp -- ISO 8601 UTC timestamp when the transition was detected.
          severity  -- "high", "medium", or "low".
          payload   -- type-specific fields (see the table below).

    Notes:
        Alerts are queued in memory (at most 50 per printer, older ones dropped) as printer
        state updates arrive, so none are produced while the MQTT session is paused, and the
        first state seen for a printer after server start produces none. The queue is shared:
        ``GET /api/alerts`` drains it by default and ``DELETE /api/alerts`` clears it, so an
        alert read there is gone here. It is lost on server restart and is not reset when a
        printer session is stopped or restarted.

        Transitions that produce each job alert: job_started only on a direct change to
        RUNNING from IDLE, FINISH, FAILED or an empty state (a PREPARE to RUNNING change
        produces none); job_finished on RUNNING or PAUSE to FINISH; job_failed on any change
        into FAILED; job_paused on RUNNING to PAUSE; job_resumed on PAUSE to RUNNING.

        Payload fields by type (what the code currently emits):
          job_started:       {} (empty)
          job_finished:      {} (empty)
          job_failed:        {} (empty)
          job_paused:        stage_id (currently always null), stage_name (currently always
                             "unknown")
          job_resumed:       {} (empty)
          stage_change:      stage_id, stage_name, prev_stage_id, prev_stage_name (not
                             currently emitted, see below)
          hms_error_new:     errors=[{code, description}, ...] (description is currently
                             always "")
          hms_error_cleared: prev_error_count
          health_escalated:  from_verdict, to_verdict, score
          health_recovered:  from_verdict, to_verdict, score
        The job payloads are meant to carry subtask_name, gcode_file and plate_num (and
        job_finished elapsed_min and layer_num), but those fields are never populated: do
        not index them.

        hms_error_cleared is emitted only when every active HMS code has cleared; a partial
        clear is silent. hms_error_new lists only newly appeared codes. Health alerts exist
        only while the camera job monitor is producing verdicts, are limited to one per 60 s,
        and are never emitted for the first verdict seen or for a "standby" verdict.

        Known code defects: ``stage_change`` is never emitted, and ``job_paused`` has no
        stage, because the detector reads a ``stg_cur`` attribute that BambuState does not
        have. The job fields above are missing because SessionManager has no
        ``get_job_info()`` or ``get_progress()`` (the resulting errors are swallowed).
        ``description`` is empty because the code reads ``description``/``desc`` while HMS
        entries carry ``msg``.

        Call kb_get('bambu-state-change-alerts') for further documentation on each alert
        type, recommended actions, and severity guidance.
    """
    from session_manager import session_manager
    printer = session_manager.get_printer(name)
    if printer is None:
        return [{"error": f"Printer '{name}' not connected"}]
    return _notifications.get_pending(name, clear=clear)
