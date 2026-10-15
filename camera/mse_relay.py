"""
mse_relay.py — runs the bambu-mse-stream camera relay for one printer as a child process.

The relay (github.com/synman/bambu-mse-stream, vendored as camera/mse_relay_server.py at
MSE_STREAM_REF) turns every Bambu camera into one fragmented-MP4 stream a browser plays with
Media Source Extensions:

  rtsps  X1, X1C, X1E, H2D, H2S, P2S: the printer's H.264 is copied into fMP4, no decode.
  jpeg   A1, A1 mini, P1P, P1S: each JPEG still is encoded once as an intra H.264 frame.

It also keeps the latest frame for /snapshot.jpg and counts rates for /stats. BPA runs the same
file the same way (docker/run.sh); this module is the bambu-mcp equivalent. A child process,
not an import: the relay's reader loops forever and has no stop of its own, and a blocked TLS
read ignores any flag, so killing the process is the stop.

The child binds 127.0.0.1 only. The stream server (camera/mjpeg_server.py) proxies its routes,
so the browser sees one origin. The access code reaches the child through its environment,
never its argv, and the relay masks it in every line it logs.
"""

from __future__ import annotations

import http.client
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

# The relay is camera/mse_relay_server.py, a byte copy of server.py at this commit. To bump it, copy
# the new server.py over the file and update both values; test_mse_relay.py checks the hash.
MSE_STREAM_REF = "b36381219696cec693170e1612d01da552a45944"
MSE_STREAM_SHA256 = "e2ff9f6b458c3b2f268e9806078b1e3c8b4e4a95378d808506ec338d0fd0ee2b"
RELAY_SCRIPT = Path(__file__).resolve().parent / "mse_relay_server.py"

# bambu-mcp protocol name → the relay's camera family
_FAMILY = {"rtsps": "rtsps", "tcp_tls": "jpeg"}

_START_TIMEOUT = 5.0   # seconds for the child to start listening
_STOP_TIMEOUT = 3.0    # seconds for the child to exit on SIGTERM before SIGKILL

# printer host → running relay, so a snapshot reuses the live camera session
_by_host: dict[str, "RelayProcess"] = {}
_by_host_lock = threading.Lock()


def family_for(protocol: str) -> str:
    """The relay camera family for a bambu-mcp camera protocol ("rtsps" or "tcp_tls")."""
    return _FAMILY[protocol]


def for_host(host: str) -> "RelayProcess | None":
    """The running relay for this printer host, or None."""
    with _by_host_lock:
        relay = _by_host.get(host)
    return relay if relay is not None and relay.alive else None


class RelayProcess:
    """One printer's relay child: start it, read its routes, stop it."""

    def __init__(self, name: str, host: str, access_code: str, camera: str):
        self.name = name
        self.host = host
        self.camera = camera
        self._access_code = access_code
        self._proc: subprocess.Popen | None = None
        self.port: int | None = None

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        """Start the child on a pool port and wait until it listens. Raises RuntimeError if it does not."""
        from port_pool import port_pool as _pp
        self.port = _pp.allocate()
        env = dict(os.environ)
        env["BAMBU_PRINTERS"] = json.dumps(
            [{"name": self.name, "host": self.host, "access_code": self._access_code, "camera": self.camera}])
        log.info("relay %s: starting on 127.0.0.1:%d camera=%s ref=%s", self.name, self.port, self.camera,
                 MSE_STREAM_REF[:7])
        # Same process group as the daemon, so launchd reaps the child with it.
        self._proc = subprocess.Popen(
            [sys.executable, str(RELAY_SCRIPT), "--bind", "127.0.0.1", "--port", str(self.port)],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=str(RELAY_SCRIPT.parent), text=True, bufsize=1)
        threading.Thread(target=self._pump_log, daemon=True, name=f"relay-log-{self.name}").start()
        deadline = time.monotonic() + _START_TIMEOUT
        while time.monotonic() < deadline:
            if not self.alive:
                break
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.25):
                    with _by_host_lock:
                        _by_host[self.host] = self
                    log.info("relay %s: listening (pid %d)", self.name, self._proc.pid)
                    return
            except OSError:
                time.sleep(0.05)
        code = self._proc.poll()
        self.close()
        raise RuntimeError(f"camera relay did not start (exit {code})" if code is not None
                           else "camera relay did not start listening")

    def _pump_log(self) -> None:
        """Copy the child's output into this daemon's log."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log.info("relay %s: %s", self.name, line)

    def get(self, path: str, timeout: float = 10.0) -> tuple[int, str, bytes]:
        """GET one relay route; (status, content_type, body). Raises OSError when the child is unreachable."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, resp.getheader("Content-Type") or "", resp.read()
        finally:
            conn.close()

    def open_stream(self, timeout: float = 40.0) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        """Open /stream.mp4; the caller reads the response and closes the connection.

        The timeout bounds each read: the relay sends a fragment at least every few seconds, and ends
        the response when its camera session resets.
        """
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        conn.request("GET", "/stream.mp4")
        return conn, conn.getresponse()

    def snapshot(self) -> bytes | None:
        """The relay's latest frame as JPEG, or None when it has none yet."""
        try:
            status, _ctype, body = self.get("/snapshot.jpg")
        except OSError as e:
            log.debug("relay %s: snapshot unreachable: %s", self.name, e)
            return None
        return body if status == 200 and body else None

    def close(self) -> None:
        """Stop the child and release its port."""
        with _by_host_lock:
            if _by_host.get(self.host) is self:
                del _by_host[self.host]
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(_STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(_STOP_TIMEOUT)
            log.info("relay %s: stopped (pid %d, exit %s)", self.name, proc.pid, proc.returncode)
        if self.port is not None:
            from port_pool import port_pool as _pp
            _pp.release(self.port)
            self.port = None
