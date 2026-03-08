"""
port_pool.py — Shared ephemeral port pool for all TCP listener components.

All TCP listener components in bambu-mcp (REST API server, MJPEG stream servers)
draw from this single shared pool.  The pool allocates ports on demand via a live
OS-level socket.bind() probe and releases them when a listener stops.

Port range
----------
Pool is anchored at 49152 — the first port in the IANA Dynamic/Private/Ephemeral
range defined by RFC 6335 §6.  IANA intentionally registers zero services in
49152–65535, so there is no static exclusion list; is_available() socket-bind
probing handles all runtime conflicts (OS outbound ports, AirPlay, Windows RPC,
Apple Xsan, etc.) automatically.

OS alignment:
  macOS:          49152–65535 (same start — no kernel collision at pool start)
  Windows modern: 49152–65535 (same start — no kernel collision at pool start)
  Linux default:  32768–60999 (overlaps; is_available() handles in-use ports)

Environment variables
---------------------
  BAMBU_PORT_POOL_START  — first port in the pool (default 49152)
  BAMBU_PORT_POOL_END    — last port in the pool inclusive (default 49251; 100-port window)

Usage
-----
  from port_pool import port_pool

  port = port_pool.allocate()                  # next free port in pool
  port = port_pool.allocate(preferred=49200)   # try 49200 first, rotate on failure
  port_pool.release(port)                      # return port to pool
  state = port_pool.get_state()                # {pool_start, pool_end, pool_claimed}
"""

from __future__ import annotations

import logging
import os
import socket
import threading

log = logging.getLogger(__name__)

_POOL_START_DEFAULT = 49152
_POOL_END_DEFAULT   = 49251


class PortPool:
    """Thread-safe singleton port pool for all TCP listener components."""

    def __init__(self, start: int | None = None, end: int | None = None) -> None:
        log.debug("PortPool.__init__: called start=%s end=%s", start, end)
        self._start: int = start if start is not None else int(
            os.environ.get("BAMBU_PORT_POOL_START", str(_POOL_START_DEFAULT))
        )
        self._end: int = end if end is not None else int(
            os.environ.get("BAMBU_PORT_POOL_END", str(_POOL_END_DEFAULT))
        )
        self._claimed: set[int] = set()
        self._lock = threading.Lock()
        log.debug("PortPool.__init__: pool range %d–%d", self._start, self._end)

    # ── Public API ────────────────────────────────────────────────────────────

    def allocate(self, preferred: int | None = None) -> int:
        """
        Allocate a port and return it.

        If *preferred* is provided and the port is not already claimed and is
        OS-available (socket.bind() succeeds), that port is returned immediately.
        Preferred values outside the pool range are still attempted first before
        pool rotation begins.

        Otherwise, the pool is scanned from *pool_start* upward, skipping any
        port that is already claimed or fails an OS-level availability check.

        Returns:
            The allocated port number (marked as claimed until released).

        Raises:
            OSError: If every port in the pool range is unavailable.
        """
        log.debug("PortPool.allocate: called preferred=%s", preferred)
        with self._lock:
            # Try preferred port first (may be outside pool range)
            if preferred is not None and preferred not in self._claimed and self._is_available_locked(preferred):
                self._claimed.add(preferred)
                log.info("PortPool.allocate: allocated preferred port %d", preferred)
                return preferred

            # Scan pool range
            for port in range(self._start, self._end + 1):
                if port not in self._claimed and self._is_available_locked(port):
                    self._claimed.add(port)
                    log.info("PortPool.allocate: allocated pool port %d", port)
                    return port

            log.error(
                "PortPool.allocate: pool exhausted (range %d–%d, claimed=%s)",
                self._start, self._end, sorted(self._claimed),
            )
            raise OSError(
                f"bambu-mcp port pool exhausted: no available port in {self._start}–{self._end}"
            )

    def release(self, port: int) -> None:
        """Release a previously allocated port back to the pool."""
        log.debug("PortPool.release: called port=%d", port)
        with self._lock:
            if port in self._claimed:
                self._claimed.discard(port)
                log.info("PortPool.release: released port %d", port)
            else:
                log.debug("PortPool.release: port %d was not claimed — no-op", port)

    def is_available(self, port: int) -> bool:
        """
        Return True if *port* is not currently claimed and passes an OS-level
        socket.bind() probe.  Thread-safe.
        """
        log.debug("PortPool.is_available: called port=%d", port)
        with self._lock:
            result = port not in self._claimed and self._is_available_locked(port)
        log.debug("PortPool.is_available: port=%d → %s", port, result)
        return result

    def get_state(self) -> dict:
        """
        Return the current pool state.

        Returns:
            dict with keys:
              pool_start   — first port in the configured pool range
              pool_end     — last port in the configured pool range (inclusive)
              pool_claimed — sorted list of currently claimed port numbers
        """
        log.debug("PortPool.get_state: called")
        with self._lock:
            state = {
                "pool_start":   self._start,
                "pool_end":     self._end,
                "pool_claimed": sorted(self._claimed),
            }
        log.debug("PortPool.get_state: → %s", state)
        return state

    # ── Internal ──────────────────────────────────────────────────────────────

    def _is_available_locked(self, port: int) -> bool:
        """OS-level socket probe.  Must be called while holding self._lock."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("", port))
                return True
            except OSError:
                return False


# Module-level singleton — import this everywhere
port_pool = PortPool()
