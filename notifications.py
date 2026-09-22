"""
notifications.py — Per-printer state change alert store and push bridge.

Detects high-visibility state transitions (gcode_state, stage, active HMS
faults, job health verdict) and queues structured alert dicts for consumption by
the AI agent via get_pending_alerts() or MCP resource subscription.

Push mechanism:
  session_manager.on_update() calls notifications.check_and_emit(name) on
  every printer state push. Transitions are appended to a per-printer deque
  (max 50 alerts). When an MCP client subscribes to bambu://alerts/{name},
  send_resource_updated() is called via asyncio bridge so the client
  re-reads the resource on the next available turn.

Consumption:
  - MCP tool:     get_pending_alerts(name, clear=True)    — works in all clients
  - HTTP:         GET  /api/alerts?name=<name>
                  DELETE /api/alerts?name=<name>
  - MCP resource: bambu://alerts/{name}                   — requires client subscription

Alert schema:
  {
    "type":      str,   # alert type key (e.g. "job_failed")
    "printer":   str,   # printer name
    "timestamp": str,   # ISO 8601 UTC
    "severity":  str,   # "high", "medium", or "low"
    "payload":   dict,  # type-specific fields (see kb_get('bambu-state-change-alerts'))
  }
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

# ── Stages ────────────────────────────────────────────────────────────────────
# Stage codes and names are bpm's: the printer reports the raw code as stg_cur, bpm stores
# it on the job record (ActiveJobInfo.stage_id) and names it with bambutools.parseStage.
# There is deliberately no second table here: a local copy was numbered differently from
# parseStage (codes 5-21 off by one) and covered only 22 of the ~60 codes bpm knows.

# Codes that are not a phase worth alerting on: -1/0 = no stage (idle / printing normally),
# 100 = "Printing", 255 = "Completed". Entering any other stage is a stage_change.
_QUIET_STAGES = frozenset({-1, 0, 100, 255})

_IDLE_STATES   = {"IDLE", "FINISH", "FAILED", ""}
_ACTIVE_STATES = {"RUNNING", "PAUSE"}

MAX_ALERTS        = 50    # max queued alerts per printer
STAGE_DEBOUNCE_S  = 30.0  # suppress a stage_change into a stage already alerted within window
HMS_DEBOUNCE_S    = 30.0  # suppress a re-alert for an HMS code already alerted within window
HEALTH_DEBOUNCE_S = 60.0  # suppress health verdict re-fire within window


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _job_stage_id(job: Any) -> Optional[int]:
    """The current stage code from bpm's job record (ActiveJobInfo), or None if unknown."""
    try:
        return int(getattr(job, "stage_id", None))
    except (TypeError, ValueError):
        return None


def _stage_name(stage_id: int) -> str:
    """bpm's human name for a stage code; "unknown" where bpm has no name (codes -1 and 0)."""
    from bpm.bambutools import parseStage
    return parseStage(stage_id) or "unknown"


def _from_telemetry(state: Any) -> bool:
    """True once a print status frame has populated the state.

    bpm's service_state setter notifies on EVERY connection change, and it builds a state from
    the get_version reply, both before any status frame has arrived: what the detector is then
    handed is the never-populated default (gcode_state "IDLE"), which by value is
    indistinguishable from a real idle printer. Taking it as the baseline makes the first real
    frame of a print already running a legal IDLE to RUNNING transition, a false ``job_started``.

    The marker is wifi_signal_strength: bpm writes it only from a push_status frame's
    ``wifi_signal`` (present in every H2D, A1 and P1S push_status frame in bpm's test fixtures),
    it survives in the state across the frames that omit the key, and its default "" is not a
    value the printer reports. bpm's ``recent_update`` is not usable here: it is set by the
    get_version reply, ahead of the first status frame, so it is already True for that reply's
    default state. If a firmware ever omits the key from its status frames, this gate never
    opens and that printer raises no alerts.
    """
    return bool(getattr(state, "wifi_signal_strength", ""))


class _PrinterAlertState:
    """Per-printer prior-state snapshot used to detect transitions."""

    __slots__ = (
        "last_gcode_state",
        "last_stage",
        "last_hms_codes",
        "hms_alerted",
        "hms_alert_times",
        "last_verdict",
        "stage_alert_times",
        "last_health_time",
        "alerts",
        "lock",
    )

    def __init__(self) -> None:
        self.last_gcode_state: Optional[str] = None
        self.last_stage: Optional[int]       = None
        self.last_hms_codes: frozenset[str]  = frozenset()  # ACTIVE codes at the last update
        self.hms_alerted: set[str]           = set()  # codes with a raised, not yet cleared alert
        self.hms_alert_times: dict[str, float] = {}  # HMS code -> monotonic time last alerted
        self.last_verdict: Optional[str]     = None
        self.stage_alert_times: dict[int, float] = {}  # stage code -> monotonic time last alerted
        self.last_health_time: float         = 0.0
        self.alerts: deque[dict]             = deque(maxlen=MAX_ALERTS)
        self.lock: threading.Lock            = threading.Lock()


class NotificationManager:
    """Thread-safe state tracker and alert store for all printers."""

    def __init__(self) -> None:
        self._states: dict[str, _PrinterAlertState] = {}
        self._global_lock = threading.Lock()
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        self._mcp_server: Any = None  # FastMCP server instance, set by wire_mcp_server()

    # ── Wiring ────────────────────────────────────────────────────────────────

    def wire_mcp_server(self, mcp_server: Any, loop: asyncio.AbstractEventLoop) -> None:
        """
        Store the FastMCP server instance and its event loop for out-of-band push.
        Call from server._startup() after the event loop is known.
        """
        self._mcp_server = mcp_server
        self._event_loop = loop
        log.info("notifications: MCP server wired for resource push")

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_state(self, name: str) -> _PrinterAlertState:
        with self._global_lock:
            if name not in self._states:
                self._states[name] = _PrinterAlertState()
            return self._states[name]

    def _emit(self, ps: _PrinterAlertState, name: str,
              alert_type: str, severity: str, payload: dict) -> None:
        alert = {
            "type":      alert_type,
            "printer":   name,
            "timestamp": _now_iso(),
            "severity":  severity,
            "payload":   payload,
        }
        ps.alerts.append(alert)
        log.info("notifications[%s]: %s (%s) payload=%s", name, alert_type, severity, payload)
        self._try_push(name)

    def _try_push(self, name: str) -> None:
        """Fire notifications/resources/updated to any subscribed MCP clients."""
        if self._event_loop is None or self._mcp_server is None:
            return
        try:
            from pydantic import AnyUrl  # type: ignore[import]
            uri = AnyUrl(f"bambu://alerts/{name}")
            underlying = self._mcp_server._mcp_server
            if not hasattr(underlying, "_sessions") or not underlying._sessions:
                return

            async def _push() -> None:
                for session in list(underlying._sessions.values()):
                    try:
                        await session.send_resource_updated(uri)
                    except Exception as exc:
                        log.debug("notifications: push failed: %s", exc)

            asyncio.run_coroutine_threadsafe(_push(), self._event_loop)
        except Exception as exc:
            log.debug("notifications: _try_push error: %s", exc)

    # ── State check helpers ───────────────────────────────────────────────────

    def _check_gcode_state(self, ps: _PrinterAlertState, name: str, state: Any,
                           active_job: Any) -> None:
        new_gs = (getattr(state, "gcode_state", None) or "").upper()
        prev_gs = ps.last_gcode_state
        if prev_gs == new_gs:
            return
        ps.last_gcode_state = new_gs
        if prev_gs is None:
            return  # first observation — no transition to emit

        # Harvest job context
        job: dict = {}
        try:
            from session_manager import session_manager
            ji = session_manager.get_job_info(name)
            if ji:
                job = {
                    "subtask_name": getattr(ji, "subtask_name", None),
                    "gcode_file":   getattr(ji, "gcode_file", None),
                    "plate_num":    getattr(ji, "plate_num", None),
                }
        except Exception:
            pass

        if new_gs == "RUNNING" and prev_gs in _IDLE_STATES:
            self._emit(ps, name, "job_started", "high", job)
            try:
                import job_project
                from session_manager import session_manager
                job_project.remember(name, session_manager.get_job(name))
            except Exception as exc:
                log.debug("notifications: job_project.remember failed: %s", exc)

        elif new_gs == "FINISH" and prev_gs in _ACTIVE_STATES:
            try:
                import job_project
                job_project.forget(name)
            except Exception as exc:
                log.debug("notifications: job_project.forget failed: %s", exc)
            try:
                from session_manager import session_manager
                prog = session_manager.get_progress(name)
                if prog:
                    job["elapsed_min"] = getattr(prog, "elapsed_time", None)
                    job["layer_num"]   = getattr(prog, "layer_num", None)
            except Exception:
                pass
            self._emit(ps, name, "job_finished", "high", job)

        elif new_gs == "FAILED":
            self._emit(ps, name, "job_failed", "high", job)
            try:
                import job_project
                job_project.forget(name)
            except Exception as exc:
                log.debug("notifications: job_project.forget failed: %s", exc)

        elif new_gs == "PAUSE" and prev_gs == "RUNNING":
            sid = _job_stage_id(active_job)
            self._emit(ps, name, "job_paused", "medium", {
                **job,
                "stage_id":   sid,
                "stage_name": _stage_name(sid) if sid is not None else "unknown",
            })

        elif new_gs == "RUNNING" and prev_gs == "PAUSE":
            self._emit(ps, name, "job_resumed", "low", job)

    def _check_stage(self, ps: _PrinterAlertState, name: str, active_job: Any) -> None:
        stage_id = _job_stage_id(active_job)
        if stage_id is None:
            return

        prev = ps.last_stage
        if stage_id == prev:
            return  # same stage: not a transition, however long the stage lasts
        ps.last_stage = stage_id  # tracked even when the transition is not alerted

        if prev is None or stage_id in _QUIET_STAGES:
            return  # first observation, or entering idle / normal printing

        now = time.monotonic()
        last_alert = ps.stage_alert_times.get(stage_id)
        if last_alert is not None and now - last_alert < STAGE_DEBOUNCE_S:
            return  # flapping back into a stage that was just alerted
        ps.stage_alert_times[stage_id] = now

        self._emit(ps, name, "stage_change", "medium", {
            "stage_id":        stage_id,
            "stage_name":      _stage_name(stage_id),
            "prev_stage_id":   prev,
            "prev_stage_name": _stage_name(prev),
        })

    def _check_hms(self, ps: _PrinterAlertState, name: str, state: Any) -> None:
        """Alert on ACTIVE HMS faults only.

        bpm's hms_errors list carries no active/historical distinction (decodeHMS labels an
        entry from its mask byte alone, so a stale code reads "Fatal"). The server's rule,
        which get_hms_errors applies, is that a fault is active only as a device_error plus
        the first device_hms; every other device_hms is relabelled "Historical". The telemetry
        re-sends and drops those stale entries about once a second, so alerting on the raw list
        queued a new/cleared pair per flap.
        """
        try:
            from tools.state import _apply_hms_historical
            raw = getattr(state, "hms_errors", None) or []
            active = [
                e for e in _apply_hms_historical(
                    [e for e in raw if isinstance(e, dict) and (e.get("code") or e.get("attr"))])
                if e.get("severity") != "Historical"
            ]
            new_codes = frozenset(str(e.get("code") or e.get("attr") or "") for e in active)
        except Exception:
            return

        prev_codes = ps.last_hms_codes
        if new_codes == prev_codes:
            return
        ps.last_hms_codes = new_codes

        now = time.monotonic()
        added = {
            c for c in new_codes - prev_codes
            if not (c in ps.hms_alert_times and now - ps.hms_alert_times[c] < HMS_DEBOUNCE_S)
        }  # a code flapping back in within the window was just alerted
        if added:
            errors_payload = [
                {
                    "code":        str(e.get("code") or e.get("attr") or ""),
                    "description": e.get("msg") or "No description in the HMS catalogue",
                }
                for e in active
                if str(e.get("code") or e.get("attr") or "") in added
            ]
            for code in added:
                ps.hms_alert_times[code] = now
            ps.hms_alerted |= added
            self._emit(ps, name, "hms_error_new", "high", {"errors": errors_payload})

        if prev_codes and not new_codes and ps.hms_alerted:
            ps.hms_alerted.clear()  # an alert was raised for this episode: close it
            self._emit(ps, name, "hms_error_cleared", "medium", {
                "prev_error_count": len(prev_codes),
            })

    def _check_health(self, ps: _PrinterAlertState, name: str) -> None:
        try:
            from camera import job_monitor
            result = job_monitor.get_latest_result(name)
            if not result:
                return
            verdict = result.get("stable_verdict")
            if not verdict:
                return

            prev = ps.last_verdict
            if verdict == prev:
                return

            _RANK = {"clean": 0, "warning": 1, "critical": 2, "standby": -1}
            rank_new  = _RANK.get(verdict.lower(), -1)
            rank_prev = _RANK.get((prev or "").lower(), -1)

            if rank_new < 0 or rank_prev < 0:
                ps.last_verdict = verdict
                return

            now = time.monotonic()
            if now - ps.last_health_time < HEALTH_DEBOUNCE_S:
                return

            ps.last_verdict    = verdict
            ps.last_health_time = now

            payload = {
                "from_verdict": prev,
                "to_verdict":   verdict,
                "score":        result.get("anomaly_score"),
            }
            if rank_new > rank_prev:
                self._emit(ps, name, "health_escalated", "high",   payload)
            else:
                self._emit(ps, name, "health_recovered", "medium", payload)
        except Exception:
            pass

    # ── Main entry point (called from session_manager.on_update) ─────────────

    def check_and_emit(self, name: str) -> None:
        """
        Check current printer state for transitions and queue any alerts.
        Must be fast and non-blocking — called on every MQTT state update.
        """
        try:
            from session_manager import session_manager
            state = session_manager.get_state(name)
            if state is None or not _from_telemetry(state):
                return
            # The stage lives on the job record, not on BambuState (which has no stg_cur).
            active_job = session_manager.get_job(name)
            ps = self._get_state(name)
            with ps.lock:
                self._check_gcode_state(ps, name, state, active_job)
                self._check_stage(ps, name, active_job)
                self._check_hms(ps, name, state)
                self._check_health(ps, name)
        except Exception as exc:
            log.debug("notifications[%s]: check_and_emit error: %s", name, exc)

    # ── Consumption API ───────────────────────────────────────────────────────

    def get_pending(self, name: str, clear: bool = True) -> list[dict]:
        """Return (and optionally clear) queued alerts for a named printer."""
        ps = self._get_state(name)
        with ps.lock:
            alerts = list(ps.alerts)
            if clear:
                ps.alerts.clear()
        return alerts

    def get_all_pending(self, clear: bool = True) -> dict[str, list[dict]]:
        """Return pending alerts for all printers as {name: [alerts]} dict."""
        with self._global_lock:
            names = list(self._states.keys())
        return {n: a for n in names if (a := self.get_pending(n, clear=clear))}

    def clear(self, name: str) -> None:
        """Discard all queued alerts for a printer without returning them."""
        ps = self._get_state(name)
        with ps.lock:
            ps.alerts.clear()


# Singleton — imported by server.py, tools/notifications.py, api_server.py
notifications = NotificationManager()
