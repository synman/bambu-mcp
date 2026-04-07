"""
rtsps_stream.py — Bambu Lab RTSPS camera stream client (X1/H2D series).

Uses PyAV (av>=14.0) which bundles libav natively — no system ffmpeg required.

RTSPS URL format: rtsps://bblp:{access_code}@{ip}:322/streaming/live/1
  - Port 322 is fixed for all Bambu RTSPS streams
  - Username is always "bblp" (literal string)
  - TLS certificate verification disabled (self-signed Bambu CA)
  - TCP transport mode (more reliable on LAN than UDP)

av.open() options:
  rtsp_transport: "tcp"
  tls_verify: "0"
  allowed_media_types: "video"

Architecture: RTSPSFrameBuffer runs a single background thread that owns all PyAV
calls (av.open, container.decode, _frame_to_jpeg). Multiple HTTP clients share the
latest JPEG frame via a threading.Condition — identical to TCPFrameBuffer / webcamd.
This prevents libav segfaults from concurrent PyAV calls across ThreadingHTTPServer
worker threads.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import gc
import io
import logging
import os
import platform
import socket
import struct
import threading
import time
from typing import Iterator

import av

log = logging.getLogger(__name__)

# Regex to strip embedded access codes from RTSPS URLs in log messages.
_RTSPS_CRED_RE = __import__("re").compile(r"(rtsps://bblp:)[^@]+(@)")


def _redact_url(text: str) -> str:
    """Replace access codes in rtsps:// URLs with '****'."""
    return _RTSPS_CRED_RE.sub(r"\1****\2", text)


# libc handle for raw getpeername()/shutdown() syscalls — no socket.fromfd() dup needed.
try:
    _libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
except Exception:
    _libc = None

# sockaddr_in layout: BSD/macOS prefixes a 1-byte sin_len before sin_family; Linux does not.
_FAMILY_OFFSET = 1 if platform.system() == "Darwin" else 0
_FAMILY_FMT = "B" if platform.system() == "Darwin" else "H"


def _find_camera_fd(target_ip: str) -> int | None:
    """Scan open FDs of this process for a TCP socket connected to target_ip:322.

    Calls getpeername() via ctypes — avoids socket.fromfd() which would dup() the FD.
    Returns the raw integer FD, or None if not found.
    """
    if _libc is None:
        return None
    try:
        resolved = socket.gethostbyname(target_ip)
    except OSError:
        resolved = target_ip
    try:
        fd_names = os.listdir("/dev/fd")
    except OSError:
        return None

    buf = ctypes.create_string_buffer(28)  # large enough for sockaddr_in
    alen = ctypes.c_uint32(28)
    for name in fd_names:
        try:
            fd = int(name)
        except ValueError:
            continue
        alen.value = 28
        if _libc.getpeername(fd, buf, ctypes.byref(alen)) != 0:
            continue
        # sin_family offset differs between macOS (offset 1, uint8) and Linux (offset 0, uint16)
        if struct.unpack_from(_FAMILY_FMT, buf.raw, _FAMILY_OFFSET)[0] != socket.AF_INET:
            continue
        # sin_port at offset 2 (network byte order), sin_addr at offset 4
        if socket.ntohs(struct.unpack_from("H", buf.raw, 2)[0]) != 322:
            continue
        if socket.inet_ntoa(buf.raw[4:8]) == resolved:
            log.debug("_find_camera_fd: FD=%d for %s:322", fd, resolved)
            return fd
    log.debug("_find_camera_fd: no match for %s:322", target_ip)
    return None


def _shutdown_fd(fd: int) -> bool:
    """Shut down both ends of socket FD to interrupt a blocking read (e.g. SSLRead).

    Does NOT close the FD — FFmpeg can still call close() on it afterwards.
    Returns True on success.
    """
    if _libc is None:
        return False
    return _libc.shutdown(fd, 2) == 0  # SHUT_RDWR = 2 on POSIX


def _set_rcvtimeo(fd: int, seconds: int) -> bool:
    """Set SO_RCVTIMEO on a raw socket FD to bound SSLRead blocking duration.

    SO_RCVTIMEO is a BSD kernel option applied inside SSLRead/poll. It forces
    the syscall to return ETIMEDOUT after `seconds` seconds with no data,
    giving FFmpeg a chance to check its interrupt state and allowing the reader
    thread to detect _client_count == 0 without waiting for the next frame.

    timeval layout on macOS 64-bit: struct { long tv_sec; long tv_usec; } = 16 bytes.
    On Linux 64-bit the layout is identical. Verified via setsockopt with both
    'li' (12 bytes, fails) and 'll' (16 bytes, succeeds) formats on macOS 15.
    Does NOT tear down the connection — reader can retry normally after ETIMEDOUT.
    """
    if _libc is None:
        return False
    SO_RCVTIMEO = 4102 if platform.system() == "Darwin" else 20
    tv = struct.pack("ll", seconds, 0)  # 16 bytes on 64-bit
    ret = _libc.setsockopt(fd, socket.SOL_SOCKET, SO_RCVTIMEO, tv, len(tv))
    return ret == 0


_AV_OPTIONS = {
    "rtsp_transport": "tcp",
    "tls_verify": "0",
    "allowed_media_types": "video",
}


def _frame_to_jpeg(frame: av.VideoFrame) -> bytes:
    """Convert an av VideoFrame to JPEG bytes. Must be called from the reader thread."""
    log.debug("_frame_to_jpeg: encoding frame size=%dx%d fmt=%s", frame.width, frame.height, frame.format.name)
    try:
        log.debug("_frame_to_jpeg: calling frame.reformat → yuvj420p")
        yuv_frame = frame.reformat(format="yuvj420p")
        log.debug("_frame_to_jpeg: reformat complete, opening mjpeg output container")
        output = io.BytesIO()
        output_container = av.open(output, mode="w", format="mjpeg")
        log.debug("_frame_to_jpeg: output container opened, adding mjpeg stream %dx%d", yuv_frame.width, yuv_frame.height)
        try:
            jpeg_stream = output_container.add_stream("mjpeg")
            jpeg_stream.width = yuv_frame.width
            jpeg_stream.height = yuv_frame.height
            jpeg_stream.pix_fmt = "yuvj420p"
            log.debug("_frame_to_jpeg: encoding frame packets")
            for packet in jpeg_stream.encode(yuv_frame):
                output_container.mux(packet)
            log.debug("_frame_to_jpeg: flushing encoder")
            for packet in jpeg_stream.encode(None):
                output_container.mux(packet)
        finally:
            log.debug("_frame_to_jpeg: closing output container")
            output_container.close()
        result = output.getvalue()
        log.debug("_frame_to_jpeg: → %d bytes", len(result))
        return result
    except Exception as e:
        log.error("_frame_to_jpeg: error encoding frame: %s", e, exc_info=True)
        raise


def _build_url(ip: str, access_code: str) -> str:
    return f"rtsps://bblp:{access_code}@{ip}:322/streaming/live/1"


def capture_frame(ip: str, access_code: str, timeout: float = 15.0) -> bytes:
    """
    Open the RTSPS stream, decode the first video frame, convert to JPEG bytes,
    and close the container.

    Returns raw JPEG bytes.
    """
    log.debug("capture_frame: entry ip=%s timeout=%s", ip, timeout)
    log.info("capture_frame: connecting to %s:322 timeout=%.1fs", ip, timeout)
    url = _build_url(ip, access_code)
    options = dict(_AV_OPTIONS)
    options["stimeout"] = str(int(timeout * 1_000_000))  # microseconds
    container = av.open(url, options=options)
    log.info("capture_frame: connected, waiting for first frame")
    try:
        log.debug("capture_frame: decoding first frame")
        for frame in container.decode(video=0):
            log.debug("capture_frame: got frame, converting to JPEG")
            result = _frame_to_jpeg(frame)
            log.info("capture_frame: → %d bytes from %s", len(result), ip)
            return result
        log.warning("capture_frame: no frames received from %s", ip)
        raise RuntimeError("No frames received from RTSPS stream")
    finally:
        log.debug("capture_frame: container closed for %s", ip)
        container.close()


class RTSPSFrameBuffer:
    """
    Single background thread owns all PyAV calls for an RTSPS stream.

    Multiple HTTP clients share the latest JPEG frame via a Condition variable —
    same pattern as TCPFrameBuffer. Prevents libav segfaults from concurrent PyAV
    calls across ThreadingHTTPServer worker threads.
    """

    def __init__(self, ip: str, access_code: str, timeout: float = 15.0):
        self._ip = ip
        self._access_code = access_code
        self._timeout = timeout
        self._running = True
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._last_frame: bytes | None = None
        self._last_frame_time: float = time.monotonic()
        self._container_lock = threading.Lock()
        self._container = None  # current av.InputContainer; written by reader, read by watchdog
        self._camera_fd: int | None = None  # TCP socket FD for the active RTSPS connection
        log.debug("RTSPSFrameBuffer.__init__: ip=%s timeout=%s", ip, timeout)
        self._client_count = 0
        self._client_wake_event = threading.Event()
        # Settable after construction (to avoid circular dependency at creation time).
        # When set, _schedule_idle_stop() calls this after 10s with no consumers.
        # Typically: lambda: mjpeg_server.stop(name)
        self._on_idle: "Callable[[], None] | None" = None
        self._thread = threading.Thread(target=self._reader_loop, daemon=True,
                                        name=f"rtsps-cam-{ip}")
        self._thread.start()
        self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True,
                                                 name=f"rtsps-watchdog-{ip}")
        self._watchdog_thread.start()
        log.debug("RTSPSFrameBuffer.__init__: reader + watchdog threads started")

    def _reader_loop(self) -> None:
        """Background thread: all av.open / container.decode / _frame_to_jpeg calls happen here."""
        log.debug("_reader_loop: starting for %s", self._ip)
        reconnect_count = 0
        backoff = 1.0  # exponential backoff: 1s → 2s → 4s → … → 30s cap

        while self._running:
            container = None
            reconnect_count += 1
            log.debug("_reader_loop: connect attempt #%d to %s:322", reconnect_count, self._ip)
            try:
                url = _build_url(self._ip, self._access_code)
                options = dict(_AV_OPTIONS)
                options["stimeout"] = str(int(self._timeout * 1_000_000))
                options["rw_timeout"] = "3000000"
                log.debug("_reader_loop: calling av.open url=rtsps://bblp:<redacted>@%s:322/...", self._ip)
                container = av.open(url, options=options, timeout=(self._timeout, 2.0))
                log.debug("_reader_loop: av.open succeeded")
                # Capture the TCP socket FD so we can interrupt SSLRead via shutdown()
                # when the last client detaches.  container.close() from another thread
                # cannot interrupt a concurrent SSLRead on macOS (SSLContextRef lock).
                cam_fd = _find_camera_fd(self._ip)
                if cam_fd is not None:
                    ok = _set_rcvtimeo(cam_fd, 2)
                    log.debug("_reader_loop: SO_RCVTIMEO(2s) on FD=%d → %s", cam_fd, "ok" if ok else "failed")
                else:
                    log.debug("_reader_loop: camera_fd not found — SO_RCVTIMEO not set")
                with self._container_lock:
                    self._container = container
                    self._camera_fd = cam_fd
                frames_received = 0
                backoff = 1.0  # reset on successful connect

                for frame in container.decode(video=0):
                    if not self._running:
                        break
                    if self._client_count == 0:
                        log.warning("_reader_loop[%s]: no active clients, stopping decode", self._ip)
                        break
                    jpeg = _frame_to_jpeg(frame)
                    frames_received += 1
                    if frames_received % 500 == 0:
                        gc.collect()
                    log.debug("_reader_loop: frame #%d ready, size=%d", frames_received, len(jpeg))
                    with self._cond:
                        self._last_frame = jpeg
                        self._last_frame_time = time.monotonic()
                        self._cond.notify_all()

                log.warning("_reader_loop: stream ended after %d frames, reconnecting", frames_received)

            except Exception as e:
                if self._client_count == 0:
                    log.debug("_reader_loop: read timeout with no clients — going idle")
                else:
                    log.warning(
                        "_reader_loop: error: %s — reconnecting in %.0fs",
                        _redact_url(str(e)), backoff,
                    )
            finally:
                with self._container_lock:
                    self._container = None
                    self._camera_fd = None
                if container is not None:
                    try:
                        container.close()
                        log.debug("_reader_loop: container closed")
                    except Exception:
                        log.debug("_reader_loop: error closing container", exc_info=True)

            if self._running:
                if self._client_count > 0:
                    log.debug("_reader_loop: sleeping %.0fs before reconnect", backoff)
                    self._client_wake_event.wait(timeout=backoff)
                    self._client_wake_event.clear()
                    backoff = min(backoff * 2, 30.0)
                else:
                    log.debug("_reader_loop: no clients — idle-waiting for first client")
                    while self._running and self._client_count == 0:
                        self._client_wake_event.wait(timeout=5.0)
                        self._client_wake_event.clear()
                    backoff = 1.0

        log.debug("_reader_loop: exiting (running=False)")

    def _watchdog_loop(self) -> None:
        """Force-close the av container if no new frame arrives in 30 s.

        Closing the container from outside interrupts the blocking container.decode()
        call in the reader thread, causing it to catch the exception and reconnect.
        This recovers from silent RTSPS freezes where the TCP connection stays open
        but the server stops sending video frames.
        """
        log.debug("_watchdog_loop: starting for %s", self._ip)
        while self._running:
            time.sleep(5)
            if not self._running:
                break
            # Safety net: if all clients have disconnected but the container is still
            # open, close it so the reader's container.decode() unblocks promptly.
            # The primary fix is in iter_frames() but this catches any edge case where
            # that close was skipped (e.g., exception during finally).
            if self._client_count == 0:
                with self._container_lock:
                    container = self._container
                if container is not None:
                    log.warning(
                        "_watchdog_loop[%s]: zero clients but container open — interrupting decode",
                        self._ip,
                    )
                    self._interrupt_decode()
                continue
            stale_secs = time.monotonic() - self._last_frame_time
            if stale_secs >= 30:
                with self._container_lock:
                    container = self._container
                if container is not None:
                    log.warning(
                        "_watchdog_loop: no frame for %.0fs — closing container to force reconnect",
                        stale_secs,
                    )
                    try:
                        container.close()
                    except Exception as e:
                        log.debug("_watchdog_loop: error closing container: %s", e)
        log.debug("_watchdog_loop: exiting (running=False)")

    def _interrupt_decode(self) -> None:
        """Interrupt a blocking container.decode() call by shutting down the camera socket.

        On macOS, container.close() from a different thread cannot interrupt SSLRead
        because macOS SSLContextRef serializes access — the close blocks until the reader
        thread releases the lock (which never happens while SSLRead is outstanding).

        Instead, we shut down the underlying TCP socket FD via shutdown(SHUT_RDWR).
        This is handled by the kernel: SSLRead returns errSSLClosedAbort immediately,
        PyAV raises av.AVError, and the reader thread's except block sees _client_count==0
        and transitions to idle-wait.

        Falls back to container.close() only if no socket FD was captured (e.g. if
        _find_camera_fd failed to locate the connection).
        """
        with self._container_lock:
            fd = self._camera_fd
            container = self._container
        if fd is not None:
            ok = _shutdown_fd(fd)
            log.warning(
                "_interrupt_decode[%s]: shutdown(SHUT_RDWR) FD=%d → %s",
                self._ip, fd, "ok" if ok else "failed",
            )
        elif container is not None:
            log.warning(
                "_interrupt_decode[%s]: no camera_fd, falling back to container.close()",
                self._ip,
            )
            try:
                container.close()
            except Exception:
                pass

    def wait_first_frame(self, timeout: float = 15.0) -> bool:
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

    def get_latest_frame(self) -> bytes | None:
        """Return the most recently decoded JPEG frame without blocking."""
        with self._cond:
            return self._last_frame

    def iter_frames(self) -> Iterator[bytes]:
        """Yield frames as they arrive. Blocks between frames. Safe for multiple callers.

        Yields the already-buffered frame immediately on first call (no wait) so the
        browser receives data before any Safari speculative-connection timeout fires.
        """
        log.debug("iter_frames: client attached (thread=%s)", threading.current_thread().name)
        with self._cond:
            self._client_count += 1
            if self._client_count == 1:
                self._client_wake_event.set()
        last: bytes | None = None
        frames_yielded = 0
        try:
            while self._running:
                with self._cond:
                    if self._last_frame is last:
                        self._cond.wait(timeout=5)
                    frame = self._last_frame
                if not self._thread.is_alive():
                    log.error("iter_frames: reader thread died — raising to trigger browser retry")
                    raise RuntimeError("RTSPSFrameBuffer reader thread died")
                if frame is not None and frame is not last:
                    last = frame
                    frames_yielded += 1
                    log.debug("iter_frames: yielding frame #%d size=%d", frames_yielded, len(frame))
                    yield frame
                elif frame is last:
                    stale_secs = time.monotonic() - self._last_frame_time
                    if stale_secs > 10:
                        log.warning("iter_frames: no new frame for %.0fs — watchdog failed, raising to trigger browser retry", stale_secs)
                        raise RuntimeError(f"RTSPSFrameBuffer stream stalled ({stale_secs:.0f}s)")
        finally:
            with self._cond:
                self._client_count -= 1
                last_client = self._client_count == 0
            if last_client:
                # Wake the reader out of backoff sleep so it can see _client_count == 0.
                self._client_wake_event.set()
                # Interrupt the blocking container.decode() call by shutting down the
                # TCP socket.  container.close() from this thread cannot interrupt SSLRead
                # on macOS — use socket shutdown(SHUT_RDWR) instead, which forces SSLRead
                # to return errSSLClosedAbort immediately.
                log.warning("iter_frames[%s]: last client detached — interrupting decode", self._ip)
                self._interrupt_decode()
                # Tear down the whole pipeline after 10s idle — auto-stop on zero consumers.
                # Stream restarts automatically on next view_stream() call.
                self._schedule_idle_stop()
            log.debug(
                "iter_frames: client detached after %d frames (remaining=%d)",
                frames_yielded,
                self._client_count,
            )

    def _schedule_idle_stop(self) -> None:
        """Schedule stream teardown 10s after all consumers disconnect.

        Called from iter_frames() finally when the last client detaches.
        The grace period tolerates brief reconnects — browser refresh or single-frame
        /snapshot requests — without tearing down the whole RTSPS pipeline.

        on_idle() (= mjpeg_server.stop) blocks internally on server.shutdown(), so it
        MUST run on a daemon thread; calling it from inside a request handler would deadlock.
        """
        on_idle = self._on_idle
        if on_idle is None:
            return
        ip = self._ip

        def _delayed() -> None:
            time.sleep(10.0)
            if self._running and self._client_count == 0:
                log.info("RTSPSFrameBuffer[%s]: no consumers for 10s — auto-stopping stream", ip)
                try:
                    on_idle()
                except Exception as exc:
                    log.warning("RTSPSFrameBuffer[%s]: on_idle error: %s", ip, exc)

        threading.Thread(target=_delayed, daemon=True, name=f"rtsps-idle-{ip}").start()

    def close(self) -> None:
        log.debug("RTSPSFrameBuffer.close: stopping reader thread")
        with self._cond:
            self._running = False
            self._cond.notify_all()
        self._client_wake_event.set()  # unblock any idle-waiting _reader_loop
        self._interrupt_decode()       # force SSLRead to return if reader is active
        log.debug("RTSPSFrameBuffer.close: done")
