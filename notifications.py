"""
notifications.py — Per-printer state change alert store and push bridge.

Detects high-visibility state transitions (gcode_state, stage, HMS errors,
job health verdict) and queues structured alert dicts for consumption by
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
    "payload":   dict,  # type-specific fields (see behavioral_rules/alerts knowledge module)
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

# ── Stage name map (all 22 known firmware stage codes) ────────────────────────
STAGE_NAMES: dict[int, str] = {
    0:   "idle",
    1:   "auto-leveling",
    2:   "heatbed preheating",
    3:   "sweeping XY mech",
    4:   "changing filament",
    6:   "M400 pause",
    7:   "paused: filament runout",
    8:   "heating nozzle",
    9:   "calibrating extrusion",
    10:  "scanning bed surface",
    11:  "inspecting first layer",
    12:  "identifying build plate",
    13:  "calibrating micro lidar",
    14:  "homing toolhead",
    15:  "cleaning nozzle",
    16:  "checking extruder temp",
    17:  "paused by user",
    18:  "paused: front cover removed",
    19:  "calibrating extrusion flow",
    20:  "paused: nozzle temp malfunction",
    21:  "paused: heat bed temp malfunction",
    255: "printing normally",
}

# Stages that warrant a stage_change alert (excludes 255 = printing normally, 0 = idle)
NOTABLE_STAGES: set[int] = set(STAGE_NAMES.keys()) - {0, 255}

_IDLE_STATES   = {"IDLE", "FINISH", "FAILED", ""}
_ACTIVE_STATES = {"RUNNING", "PAUSE"}

MAX_ALERTS        = 50    # max queued alerts per printer
STAGE_DEBOUNCE_S  = 30.0  # suppress stage_change re-fire for same stage within window
HEALTH_DEBOUNCE_S = 60.0  # suppress health verdict re-fire within window


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _PrinterAlertState:
    """Per-printer prior-state snapshot used to detect transitions."""

    __slots__ = (
        "last_gcode_state",
        "last_stage",
        "last_hms_codes",
        "last_verdict",
        "last_stage_time",
        "last_health_time",
        "alerts",
        "lock",
    )

    def __init__(self) -> None:
        self.last_gcode_state: Optional[str] = None
        self.last_stage: Optional[int]       = None
        self.last_hms_codes: frozenset[str]  = frozenset()
        self.last_verdict: Optional[str]     = None
        self.last_stage_time: float          = 0.0
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

    def _check_gcode_state(self, ps: _PrinterAlertState, name: str, state: Any) -> None:
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

        elif new_gs == "FINISH" and prev_gs in _ACTIVE_STATES:
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

        elif new_gs == "PAUSE" and prev_gs == "RUNNING":
            sid = getattr(state, "stg_cur", None)
            self._emit(ps, name, "job_paused", "medium", {
                **job,
                "stage_id":   sid,
                "stage_name": STAGE_NAMES.get(int(sid), "unknown") if sid is not None else "unknown",
            })

        elif new_gs == "RUNNING" and prev_gs == "PAUSE":
            self._emit(ps, name, "job_resumed", "low", job)

    def _check_stage(self, ps: _PrinterAlertState, name: str, state: Any) -> None:
        raw_stage = getattr(state, "stg_cur", None)
        if raw_stage is None:
            return
        try:
            stage_id = int(raw_stage)
        except (TypeError, ValueError):
            return

        if stage_id not in NOTABLE_STAGES:
            ps.last_stage = stage_id  # track even if not notable
            return

        if stage_id == ps.last_stage:
            if time.monotonic() - ps.last_stage_time < STAGE_DEBOUNCE_S:
                return  # same stage, within debounce window

        prev = ps.last_stage
        ps.last_stage = stage_id
        ps.last_stage_time = time.monotonic()

        if prev is None:
            return  # first observation

        self._emit(ps, name, "stage_change", "medium", {
            "stage_id":        stage_id,
            "stage_name":      STAGE_NAMES.get(stage_id, f"stage_{stage_id}"),
            "prev_stage_id":   prev,
            "prev_stage_name": STAGE_NAMES.get(prev, f"stage_{prev}"),
        })

    def _check_hms(self, ps: _PrinterAlertState, name: str, state: Any) -> None:
        try:
            raw = getattr(state, "hms_errors", None) or []
            new_codes = frozenset(
                str(e.get("code") or e.get("attr") or "")
                for e in raw
                if isinstance(e, dict) and (e.get("code") or e.get("attr"))
            )
        except Exception:
            return

        prev_codes = ps.last_hms_codes
        if new_codes == prev_codes:
            return
        ps.last_hms_codes = new_codes

        added = new_codes - prev_codes
        if added:
            errors_payload: list[dict] = []
            try:
                for e in (getattr(state, "hms_errors", None) or []):
                    code = str(e.get("code") or e.get("attr") or "")
                    if code in added:
                        errors_payload.append({
                            "code":        code,
                            "description": e.get("description") or e.get("desc") or "",
                        })
            except Exception:
                errors_payload = [{"code": c, "description": ""} for c in added]
            self._emit(ps, name, "hms_error_new", "high", {"errors": errors_payload})

        if prev_codes and not new_codes:
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
                "score":        result.get("composite_score"),
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
            if state is None:
                return
            ps = self._get_state(name)
            with ps.lock:
                self._check_gcode_state(ps, name, state)
                self._check_stage(ps, name, state)
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
