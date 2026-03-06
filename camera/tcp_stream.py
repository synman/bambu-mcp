"""
tcp_stream.py — Bambu Lab TCP+TLS binary camera stream client (A1/P1/P1S series).

Protocol (port 6000, TLS with cert verification disabled):

Auth packet (64 bytes, sent once after TLS handshake):
  Offset  Size  Value
  0       4B    LE uint32: payload size = 0x40 (64)
  4       4B    LE uint32: type = 0x3000
  8       4B    LE uint32: flags = 0
  12      4B    LE uint32: reserved = 0
  16      32B   username = "bblp", ASCII, null-padded to 32 bytes
  48      32B   password = access_code, ASCII, null-padded to 32 bytes

Frame header (16 bytes, delivered alone before each JPEG):
  Offset  Size  Value
  0       4B    LE uint32: JPEG payload size (does not include this header)
  4       4B    LE uint32: 0x00000000
  8       4B    LE uint32: 0x00000001
  12      4B    LE uint32: 0x00000000

JPEG magic: starts with b'\\xff\\xd8', ends with b'\\xff\\xd9'.
Data arrives in chunks of up to 4096 bytes.

Architecture mirrors webcamd: one background reader thread per stream stores the
latest frame in a shared buffer. Multiple HTTP clients all read from that buffer
via a Condition variable — only one TCP connection to the printer at a time.
"""

from __future__ import annotations

import logging
import socket
import ssl
import struct
import threading
import time
from typing import Iterator

log = logging.getLogger(__name__)


def _make_ssl_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _make_auth(access_code: str) -> bytes:
    header = struct.pack("<4I", 0x40, 0x3000, 0, 0)
    user = b"bblp".ljust(32, b"\x00")
    pwd = access_code.encode("ascii").ljust(32, b"\x00")
    return header + user + pwd


def _read_exactly(sock, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(4096, n - len(buf)))
        if not chunk:
            raise ConnectionError("Stream closed")
        buf.extend(chunk)
    return bytes(buf)


def capture_frame(ip: str, access_code: str, timeout: float = 30.0) -> bytes:
    """
    Connect to the printer camera, authenticate, read one JPEG frame, and disconnect.

    Uses a blocking connection — for one-shot snapshot use only.
    Returns raw JPEG bytes.
    """
    log.debug("capture_frame: connecting to %s:6000", ip)
    ctx = _make_ssl_context()
    with socket.create_connection((ip, 6000), timeout=timeout) as raw:
        sock = ctx.wrap_socket(raw, server_hostname=ip)
        log.debug("capture_frame: TLS handshake complete, sending auth")
        sock.sendall(_make_auth(access_code))
        raw_header = _read_exactly(sock, 16)
        size = int.from_bytes(raw_header[:4], "little")
        log.debug("capture_frame: reading JPEG payload size=%d", size)
        data = _read_exactly(sock, size)
        log.debug("capture_frame: received %d bytes", len(data))
        return data


class TCPFrameBuffer:
    """
    Single background TCP+TLS reader thread. Continuously reads JPEG frames from the
    printer and stores the latest frame. Multiple HTTP stream clients share one
    connection via a Condition variable — identical to webcamd's lastImage model.
    """

    MAX_READ_TIMEOUTS = 10

    def __init__(self, ip: str, access_code: str):
        self._ip = ip
        self._access_code = access_code
        self._running = True
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._last_frame: bytes | None = None
        log.debug("TCPFrameBuffer: starting reader thread for %s", ip)
        self._thread = threading.Thread(target=self._reader_loop, daemon=True,
                                        name=f"tcp-cam-{ip}")
        self._thread.start()
        log.debug("TCPFrameBuffer: reader thread started (tid=%s)", self._thread.ident)

    def _reader_loop(self) -> None:
        log.debug("_reader_loop: entering loop for %s", self._ip)
        ctx = _make_ssl_context()
        auth = _make_auth(self._access_code)
        reconnect_count = 0

        while self._running:
            sock = None
            reconnect_count += 1
            log.debug("_reader_loop: connect attempt #%d to %s:6000", reconnect_count, self._ip)
            try:
                raw = socket.create_connection((self._ip, 6000), timeout=15)
                log.debug("_reader_loop: TCP connected, starting TLS handshake")
                sock = ctx.wrap_socket(raw, server_hostname=self._ip)
                log.debug("_reader_loop: TLS handshake complete, sending auth")
                sock.write(auth)
                sock.setblocking(False)
                log.debug("_reader_loop: auth sent, entering non-blocking read loop")

                img: bytearray | None = None
                payload_size = 0
                read_timeouts = 0
                frames_received = 0

                while self._running and read_timeouts < self.MAX_READ_TIMEOUTS:
                    try:
                        dr = sock.recv(4096)
                    except ssl.SSLWantReadError:
                        time.sleep(1)
                        read_timeouts += 1
                        log.debug("_reader_loop: SSLWantReadError timeout %d/%d",
                                  read_timeouts, self.MAX_READ_TIMEOUTS)
                        continue
                    except Exception as e:
                        log.warning("_reader_loop: recv error: %s", e)
                        break

                    if not dr:
                        time.sleep(1)
                        read_timeouts += 1
                        log.debug("_reader_loop: empty recv, timeout %d/%d",
                                  read_timeouts, self.MAX_READ_TIMEOUTS)
                        continue

                    read_timeouts = 0

                    if img is not None:
                        img += dr
                        if len(img) == payload_size:
                            if img[:2] == b"\xff\xd8" and img[-2:] == b"\xff\xd9":
                                frames_received += 1
                                log.debug("_reader_loop: frame #%d complete, size=%d",
                                          frames_received, len(img))
                                with self._cond:
                                    self._last_frame = bytes(img)
                                    self._cond.notify_all()
                            else:
                                log.warning("_reader_loop: frame failed JPEG magic check "
                                            "(start=%s end=%s)", img[:2].hex(), img[-2:].hex())
                            img = None
                        elif len(img) > payload_size:
                            log.warning("_reader_loop: frame overrun len=%d expected=%d, discarding",
                                        len(img), payload_size)
                            img = None
                    elif len(dr) == 16:
                        payload_size = int.from_bytes(dr[0:3], "little")
                        log.debug("_reader_loop: header received, payload_size=%d", payload_size)
                        img = bytearray()

                if read_timeouts >= self.MAX_READ_TIMEOUTS:
                    log.warning("_reader_loop: max read timeouts reached (%d), reconnecting",
                                self.MAX_READ_TIMEOUTS)

            except ConnectionResetError as e:
                log.warning("_reader_loop: connection reset: %s", e)
            except Exception as e:
                log.error("_reader_loop: unexpected error: %s", e, exc_info=True)
            finally:
                if sock is not None:
                    try:
                        sock.close()
                        log.debug("_reader_loop: socket closed")
                    except Exception as e:
                        log.debug("_reader_loop: error closing socket: %s", e)

            if self._running:
                log.debug("_reader_loop: sleeping 2s before reconnect")
                time.sleep(2)

        log.debug("_reader_loop: exiting (running=False)")

    def wait_first_frame(self, timeout: float = 30.0) -> bool:
        """Block until the first frame is available or timeout. Returns True if a frame arrived."""
        log.debug("wait_first_frame: waiting up to %.1fs for first frame", timeout)
        with self._cond:
            deadline = time.monotonic() + timeout
            while self._last_frame is None and self._running:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    log.warning("wait_first_frame: timed out after %.1fs", timeout)
                    return False
                self._cond.wait(timeout=remaining)
        got = self._last_frame is not None
        log.debug("wait_first_frame: → %s", "ready" if got else "no frame")
        return got

    def iter_frames(self) -> Iterator[bytes]:
        """Yield frames as they arrive. Blocks between frames. Safe for multiple callers.

        Yields the already-buffered frame immediately on first call (no wait) so the
        browser receives data before any Safari speculative-connection timeout fires.
        Raises RuntimeError if the reader thread has died, so the MJPEG server surfaces
        the failure and the browser's onerror handler fires the 10s retry.
        """
        log.debug("iter_frames: client attached (thread=%s)", threading.current_thread().name)
        last: bytes | None = None
        frames_yielded = 0
        while self._running:
            with self._cond:
                if self._last_frame is last:
                    self._cond.wait(timeout=30)
                frame = self._last_frame
            if not self._thread.is_alive():
                log.error("iter_frames: reader thread is dead — raising to trigger browser retry")
                raise RuntimeError("TCPFrameBuffer reader thread died")
            if frame is not None and frame is not last:
                last = frame
                frames_yielded += 1
                log.debug("iter_frames: yielding frame #%d size=%d", frames_yielded, len(frame))
                yield frame
        log.debug("iter_frames: client detached after %d frames", frames_yielded)

    def close(self) -> None:
        log.debug("TCPFrameBuffer.close: stopping reader thread")
        with self._cond:
            self._running = False
            self._cond.notify_all()
        log.debug("TCPFrameBuffer.close: done")
