"""
session_manager.py — Manages persistent BambuPrinter MQTT sessions.

One BambuPrinter instance per configured printer, started at MCP init.
Tools access printers via get_printer(name) — never create BambuPrinter ad-hoc.
"""

from __future__ import annotations

import logging
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


class SessionManager:
    def __init__(self):
        self._printers: dict = {}  # name → BambuPrinter
        self._lock = threading.Lock()
        self._update_callbacks: list[Callable[[str], None]] = []

    def on_update(self, name: str) -> None:
        """Called by BambuPrinter.on_update — notifies all registered callbacks."""
        logger.debug("on_update: called for name=%s", name)
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
        config = _BambuConfig(
            hostname=creds["ip"],
            access_code=creds["access_code"],
            serial_number=creds["serial"],
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
        """Resume a paused MQTT session."""
        logger.debug("resume_session: called for name=%s", name)
        p = self.get_printer(name)
        if p:
            p.start_session()


# Module-level singleton used by all tools
session_manager = SessionManager()
