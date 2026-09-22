"""
session_manager.py — Manages persistent BambuPrinter MQTT sessions.

One BambuPrinter instance per configured printer, started at MCP init.
Tools access printers via get_printer(name) — never create BambuPrinter ad-hoc.
"""

from __future__ import annotations

import contextlib
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


class _NameGuard:
    """One printer name's lock plus the number of threads holding or waiting for it."""

    def __init__(self):
        self.lock = threading.Lock()
        self.users = 0


class SessionManager:
    def __init__(self):
        self._printers: dict = {}  # name → BambuPrinter
        # Guards _printers and _restart_locks; never held across quit() or start_session().
        self._lock = threading.Lock()
        # name → _NameGuard serialising every start and stop of that one printer. Entries exist
        # only while some thread holds or waits for them (see _restart_lock).
        self._restart_locks: dict = {}
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
        """Start a session for a single printer (after add_printer).

        Safe to call for a name that already has a session, including concurrently: the old
        session is quit and replaced (see ``_start_printer``), so this is a restart, never a
        second live client. If the new session's ``start_session`` raises, the error propagates and
        no session stays registered for the name (see ``_start_printer``).
        """
        logger.debug("start_printer: called for name=%s", name)
        _ensure_imports()
        self._start_printer(name)

    @contextlib.contextmanager
    def _restart_lock(self, name: str):
        """Hold the per-name lock that serialises every start and stop of ``name``.

        The table entry is reference-counted and dropped when its last user leaves, so the table
        never outgrows the set of names with a start or stop in flight. That is deliberately not
        "prune when the name is removed": a thread already waiting on the old lock would then
        share the name with a newcomer holding a fresh one, and the two would run together.
        Counting under ``self._lock`` makes the drop safe: nobody can still hold a reference at
        zero users. ``self._lock`` is never held while waiting for the name lock.
        """
        with self._lock:
            guard = self._restart_locks.get(name)
            if guard is None:
                guard = self._restart_locks[name] = _NameGuard()
            guard.users += 1
        try:
            with guard.lock:
                yield
        finally:
            with self._lock:
                guard.users -= 1
                if guard.users == 0:
                    del self._restart_locks[name]

    def _start_printer(self, name: str) -> None:
        """Build a BambuPrinter for ``name``, register it and start its MQTT session.

        A session already registered under ``name`` is quit and replaced. Rebinding the entry
        without quitting it left the old client connected with its threads and update callback
        running, and both clients share the default MQTT client id, so the old one is quit
        before the new one starts. Building first means a credential or construction failure
        leaves the live session untouched. The old client is quit exactly once: a caller that
        stops the name itself first (``stop_printer`` pops the entry) leaves nothing to quit here.
        The replaced client's ``on_update`` is cleared under the registry lock, in the same step
        that rebinds the entry: bpm's ``quit()`` notifies through it, and after the rebind that
        notification would report the NEW client's blank state for the name. The registry entry
        is rebound, never popped, so lookups see no gap. (``stop_printer`` needs no detach: it
        pops first and holds the per-name lock, so ``get_state`` is None for the whole quit.)

        If ``start_session`` raises, the new printer is unregistered (only if it is still the
        registered one) and quit, and the error is re-raised: a failed start leaves no printer
        registered, so ``get_printer`` never returns a client that never started. The old
        session was already replaced and is not restored.

        Registering and quitting the old client and starting the new one run as one step under a
        per-name lock that ``stop_printer`` takes too, so concurrent starts and stops of the same
        name run one after another. Concurrent restarts: the last to run wins, each taking the
        previous call's printer, already started, and quitting it, so exactly one client stays
        live and registered. A stop racing a restart: it runs entirely before or entirely after
        it, and never quits a printer whose ``start_session`` is still in flight. A lock around
        only the pop-and-rebind is not enough: a second call could then quit the first call's
        printer while its ``start_session`` is still running, and that call would finish
        starting a client nobody holds. ``self._lock`` still guards only the registry dict and
        is never held across ``quit()`` or ``start_session()``, which can block, so lookups
        never wait on a restart. Nothing here takes the per-name lock twice: the failure cleanup
        below calls ``_quit_printer`` directly, not ``stop_printer``.
        """
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
        with self._restart_lock(name):
            with self._lock:
                previous = self._printers.get(name)
                if previous is not None:
                    # Detach before the swap: bpm's quit() notifies through on_update, and once the
                    # registry holds the new printer that notification would resolve the name to the
                    # NEW client's blank state and read as a job boundary to every consumer.
                    previous.on_update = None
                self._printers[name] = printer
            if previous is not None:
                self._quit_printer(name, previous)
            logger.debug("_start_printer: calling start_session for '%s'", name)
            try:
                printer.start_session()
            except BaseException:
                # start_session() may have spawned its threads before raising, and quit() copes
                # with a client that only partly started (it joins a thread only if one exists).
                with self._lock:
                    if self._printers.get(name) is printer:
                        del self._printers[name]
                self._quit_printer(name, printer)
                raise
        logger.info("Session started for printer: %s", name)

    def stop_all(self) -> None:
        """Stop all active sessions cleanly."""
        with self._lock:
            names = list(self._printers.keys())
        logger.info("stop_all: stopping all sessions, count=%d", len(names))
        for name in names:
            self.stop_printer(name)

    def stop_printer(self, name: str) -> None:
        """Quit and unregister the session for ``name``; a no-op for a name with none.

        Serialised with ``_start_printer`` on the per-name lock, so a stop that arrives while a
        restart of the same name is in flight waits for it and then stops the result. Only the
        registry pop happens under ``self._lock``; ``quit()`` runs outside it.
        """
        logger.debug("stop_printer: called for name=%s", name)
        with self._restart_lock(name):
            with self._lock:
                printer = self._printers.pop(name, None)
            if printer is None:
                logger.debug("stop_printer: printer '%s' not found (already stopped?)", name)
            else:
                self._quit_printer(name, printer)

    def _quit_printer(self, name: str, printer) -> None:
        """Quit a printer already removed from (or replaced in) the registry; never raises."""
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

        Guards against a race with paho's own auto-reconnect (bpm sets
        reconnect_delay_set(1, 1) and runs loop_forever(retry_first_connection=True)
        on a dedicated thread): client.is_connected() — and bpm's own
        service_state label — can both read as disconnected during a
        transient blip while that ORIGINAL thread is still alive and
        reconnecting on its own. Calling start_session() in that window
        spawns a second client/thread/watchdog on top of the first, since
        bpm's own "already active" guard also keys off is_connected().
        The session thread's own liveness is the one signal that actually
        tells the two cases apart.
        """
        logger.debug("resume_session: called for name=%s", name)
        p = self.get_printer(name)
        if not p:
            return
        _ensure_imports()
        thread = getattr(p, "_mqtt_client_thread", None)
        thread_alive = bool(thread and thread.is_alive())
        if p.service_state != _ServiceState.PAUSED:
            # Not paused: covers both "already fully connected, nothing to
            # do" and "mid-blip, paho is reconnecting on its own" — either
            # way the session thread is still alive and start_session()
            # would duplicate it. Reconnect only when the thread has
            # genuinely exited.
            if thread_alive:
                logger.debug(
                    "resume_session: %s is not paused (state=%s) and its session "
                    "thread is still alive; nothing to resume", name, p.service_state,
                )
                return
            logger.debug(
                "resume_session: %s session thread has exited (state=%s); "
                "reconnecting via start_session", name, p.service_state,
            )
            p.start_session()
            return
        p.resume_session()
        if p.service_state != _ServiceState.CONNECTED and not thread_alive:
            logger.debug(
                "resume_session: %s not connected after resume_session (state=%s); "
                "falling back to start_session", name, p.service_state,
            )
            p.start_session()


# Module-level singleton used by all tools
session_manager = SessionManager()
