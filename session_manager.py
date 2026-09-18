"""
session_manager.py — Manages persistent BambuPrinter MQTT sessions.

One BambuPrinter instance per configured printer, started at MCP init.
Tools access printers via get_printer(name) — never create BambuPrinter ad-hoc.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Callable

import auth

logger = logging.getLogger(__name__)

# Lazy imports to avoid import-time MQTT connection attempts
_BambuPrinter = None
_BambuConfig = None
_ServiceState = None


def _ensure_imports():
    global _BambuPrinter, _BambuConfig, _ServiceState
    if _BambuPrinter is None:
        from bpm.bambuprinter import BambuPrinter
        from bpm.bambuconfig import BambuConfig
        from bpm.bambutools import ServiceState
        _BambuPrinter = BambuPrinter
        _BambuConfig = BambuConfig
        _ServiceState = ServiceState


def _env_positive_int(name: str) -> int | None:
    """Read a positive int from the environment.

    Returns None when the variable is unset, empty, unparseable, or non-positive,
    so the caller omits the kwarg entirely and BambuConfig's own default applies.
    bpm stays the single source of the default — never mirror its value here.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an integer — falling back to the bpm default", name, raw)
        return None
    if value <= 0:
        logger.warning("%s=%d must be positive — falling back to the bpm default", name, value)
        return None
    return value


def _bpm_config_overrides() -> dict:
    """Env-backed BambuConfig kwargs. Absent env var → absent kwarg → bpm default.

    `ftps_connection_timeout` (bpm `bambuconfig.py:81`, default 15s) bounds the FTPS
    control-connection handshake used for every SD-card file operation. It was
    unreachable from bambu-mcp until this shim: bpm exposed the knob, no interface
    passed it, so a slow or wedged printer stalled file calls for the fixed default.
    """
    overrides: dict = {}
    ftps_timeout = _env_positive_int("BAMBU_MCP_FTPS_TIMEOUT")
    if ftps_timeout is not None:
        overrides["ftps_connection_timeout"] = ftps_timeout
    return overrides


class SessionManager:
    def __init__(self):
        self._printers: dict = {}  # name → BambuPrinter
        self._lock = threading.Lock()
        self._update_callbacks: list[Callable[[str], None]] = []

    def on_update(self, name: str) -> None:
        """Called by BambuPrinter.on_update — notifies all registered callbacks."""
        logger.debug("on_update: called for name=%s", name)
        try:
            from notifications import notifications as _notifications
            _notifications.check_and_emit(name)
        except Exception as _e:
            logger.debug("on_update: notifications error for %s: %s", name, _e)
        for cb in self._update_callbacks:
            try:
                logger.debug("on_update: calling callback %s for %s", cb, name)
                cb(name)
            except Exception as e:
                logger.warning("on_update callback error for %s: %s", name, e)

    def register_update_callback(self, cb: Callable[[str], None]) -> None:
        self._update_callbacks.append(cb)
        logger.debug("register_update_callback: registered callback %s", cb)

    def start_all(self) -> None:
        """Start sessions for all configured printers."""
        _ensure_imports()
        names = auth.get_configured_printer_names()
        logger.info("start_all: starting sessions for %d printers: %s", len(names), names)
        for name in names:
            try:
                self._start_printer(name)
            except Exception as e:
                logger.error("Failed to start session for %s: %s", name, e, exc_info=True)

    def start_printer(self, name: str) -> None:
        """Start a session for a single printer (after add_printer)."""
        logger.debug("start_printer: called for name=%s", name)
        _ensure_imports()
        self._start_printer(name)

    def _start_printer(self, name: str) -> None:
        logger.debug("_start_printer: called for name=%s", name)
        creds = auth.get_printer_credentials(name)
        logger.debug("_start_printer: creating config ip=%s serial=%s access_code=<redacted>", creds["ip"], creds["serial"])
        overrides = _bpm_config_overrides()
        if overrides:
            logger.info("_start_printer: BambuConfig overrides for '%s': %s", name, overrides)
        config = _BambuConfig(
            hostname=creds["ip"],
            access_code=creds["access_code"],
            serial_number=creds["serial"],
            **overrides,
        )
        printer = _BambuPrinter(config=config)
        logger.debug("_start_printer: BambuPrinter object created for '%s'", name)
        printer.on_update = lambda _printer: self.on_update(name)
        with self._lock:
            self._printers[name] = printer
        logger.debug("_start_printer: calling start_session for '%s'", name)
        printer.start_session()
        logger.info("Session started for printer: %s", name)

    def stop_all(self) -> None:
        """Stop all active sessions cleanly."""
        with self._lock:
            names = list(self._printers.keys())
        logger.info("stop_all: stopping all sessions, count=%d", len(names))
        for name in names:
            self.stop_printer(name)

    def stop_printer(self, name: str) -> None:
        logger.debug("stop_printer: called for name=%s", name)
        with self._lock:
            printer = self._printers.pop(name, None)
        if printer is None:
            logger.debug("stop_printer: printer '%s' not found (already stopped?)", name)
        if printer:
            try:
                printer.quit()
                logger.info("Session stopped for printer: %s", name)
            except Exception as e:
                logger.warning("Error stopping printer %s: %s", name, e, exc_info=True)

    def get_printer(self, name: str):
        """Return the live BambuPrinter instance, or None if not connected."""
        logger.debug("get_printer: called for name=%s", name)
        with self._lock:
            result = self._printers.get(name)
        logger.debug("get_printer: %s -> %s", name, "found" if result is not None else "not found")
        return result

    def get_state(self, name: str):
        """Return BambuState for a printer, or None."""
        logger.debug("get_state: called for name=%s", name)
        p = self.get_printer(name)
        state = p.printer_state if p else None
        logger.debug("get_state: %s -> %s", name, "None" if state is None else "state present")
        return state

    def get_job(self, name: str):
        """Return ActiveJobInfo for a printer, or None."""
        logger.debug("get_job: called for name=%s", name)
        p = self.get_printer(name)
        job = p.active_job_info if p else None
        logger.debug("get_job: %s -> %s", name, "None" if job is None else "job present")
        return job

    def get_config(self, name: str):
        """Return BambuConfig for a printer, or None."""
        logger.debug("get_config: called for name=%s", name)
        p = self.get_printer(name)
        config = p.config if p else None
        logger.debug("get_config: %s -> %s", name, "None" if config is None else "config present")
        return config

    def is_connected(self, name: str) -> bool:
        """Return True if the printer session is active."""
        logger.debug("is_connected: called for name=%s", name)
        p = self.get_printer(name)
        if not p:
            logger.debug("is_connected: %s -> False (no printer)", name)
            return False
        try:
            _ensure_imports()
            result = p.service_state == _ServiceState.CONNECTED
            logger.debug("is_connected: %s -> %s", name, result)
            return result
        except Exception:
            logger.debug("is_connected: %s -> False (exception)", name)
            return False

    def list_connected(self) -> list[str]:
        """Return names of all currently active sessions."""
        logger.debug("list_connected: called")
        with self._lock:
            result = list(self._printers.keys())
        logger.debug("list_connected: returning %d connected printers: %s", len(result), result)
        return result

    def pause_session(self, name: str) -> None:
        """Pause MQTT session (stop receiving updates)."""
        logger.debug("pause_session: called for name=%s", name)
        p = self.get_printer(name)
        if p:
            p.pause_session()

    def resume_session(self, name: str) -> None:
        """Resume a paused MQTT session.

        Delegates to BambuPrinter.resume_session(), which re-subscribes in
        place when the MQTT client is still connected. If the client has
        actually dropped, resume_session() leaves service_state at QUIT
        instead of CONNECTED — in that case fall back to a full
        start_session() reconnect.
        """
        logger.debug("resume_session: called for name=%s", name)
        p = self.get_printer(name)
        if not p:
            return
        _ensure_imports()
        if p.service_state != _ServiceState.PAUSED:
            # Not paused: bpm's resume_session() would flip a live session to
            # QUIT and start_session() would then raise "a session is already
            # active". Reconnect only when the client is actually gone;
            # otherwise there is nothing to resume.
            if p.client and p.client.is_connected():
                logger.debug(
                    "resume_session: %s is not paused (state=%s); nothing to resume",
                    name, p.service_state,
                )
                return
            logger.debug(
                "resume_session: %s client dropped (state=%s); reconnecting via start_session",
                name, p.service_state,
            )
            p.start_session()
            return
        p.resume_session()
        if p.service_state != _ServiceState.CONNECTED:
            logger.debug(
                "resume_session: %s not connected after resume_session (state=%s); "
                "falling back to start_session", name, p.service_state,
            )
            p.start_session()


# Module-level singleton used by all tools
session_manager = SessionManager()
