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

import io
import logging
import threading
import time
from typing import Iterator

import av

log = logging.getLogger(__name__)


_AV_OPTIONS = {
    "rtsp_transport": "tcp",
    "tls_verify": "0",
    "allowed_media_types": "video",
}


def _frame_to_jpeg(frame: av.VideoFrame) -> bytes:
    """Convert an av VideoFrame to JPEG bytes. Must be called from the reader thread."""
    log.debug("_frame_to_jpeg: encoding frame size=%dx%d", frame.width, frame.height)
    yuv_frame = frame.reformat(format="yuvj420p")
    output = io.BytesIO()
    output_container = av.open(output, mode="w", format="mjpeg")
    jpeg_stream = output_container.add_stream("mjpeg")
    jpeg_stream.width = yuv_frame.width
    jpeg_stream.height = yuv_frame.height
    jpeg_stream.pix_fmt = "yuvj420p"
    for packet in jpeg_stream.encode(yuv_frame):
        output_container.mux(packet)
    for packet in jpeg_stream.encode(None):
        output_container.mux(packet)
    output_container.close()
    result = output.getvalue()
    log.debug("_frame_to_jpeg: encoded %d bytes", len(result))
    return result


def _build_url(ip: str, access_code: str) -> str:
    return f"rtsps://bblp:{access_code}@{ip}:322/streaming/live/1"


def capture_frame(ip: str, access_code: str, timeout: float = 15.0) -> bytes:
    """
    Open the RTSPS stream, decode the first video frame, convert to JPEG bytes,
    and close the container.

    Returns raw JPEG bytes.
    """
    log.debug("capture_frame: entry ip=%s timeout=%s", ip, timeout)
    url = _build_url(ip, access_code)
    options = dict(_AV_OPTIONS)
    options["stimeout"] = str(int(timeout * 1_000_000))  # microseconds
    log.debug("capture_frame: connecting to %s:322", ip)
    container = av.open(url, options=options)
    log.debug("capture_frame: av.open succeeded")
    try:
        log.debug("capture_frame: decoding first frame")
        for frame in container.decode(video=0):
            log.debug("capture_frame: got frame, converting to JPEG")
            result = _frame_to_jpeg(frame)
            log.debug("capture_frame: returning %d bytes", len(result))
            return result
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
        log.debug("RTSPSFrameBuffer.__init__: ip=%s timeout=%s", ip, timeout)
        self._thread = threading.Thread(target=self._reader_loop, daemon=True,
                                        name=f"rtsps-cam-{ip}")
        self._thread.start()
        log.debug("RTSPSFrameBuffer.__init__: reader thread started tid=%s", self._thread.ident)

    def _reader_loop(self) -> None:
        """Background thread: all av.open / container.decode / _frame_to_jpeg calls happen here."""
        log.debug("_reader_loop: starting for %s", self._ip)
        reconnect_count = 0

        while self._running:
            container = None
            reconnect_count += 1
            log.debug("_reader_loop: connect attempt #%d to %s:322", reconnect_count, self._ip)
            try:
                url = _build_url(self._ip, self._access_code)
                options = dict(_AV_OPTIONS)
                options["stimeout"] = str(int(self._timeout * 1_000_000))
                log.debug("_reader_loop: calling av.open url=rtsps://bblp:<redacted>@%s:322/...", self._ip)
                container = av.open(url, options=options)
                log.debug("_reader_loop: av.open succeeded")
                frames_received = 0

                for frame in container.decode(video=0):
                    if not self._running:
                        break
                    jpeg = _frame_to_jpeg(frame)
                    frames_received += 1
                    log.debug("_reader_loop: frame #%d ready, size=%d", frames_received, len(jpeg))
                    with self._cond:
                        self._last_frame = jpeg
                        self._cond.notify_all()

                log.warning("_reader_loop: stream ended after %d frames, reconnecting", frames_received)

            except Exception as e:
                log.warning("_reader_loop: error: %s — reconnecting in 1s", e, exc_info=True)
            finally:
                if container is not None:
                    try:
                        container.close()
                        log.debug("_reader_loop: container closed")
                    except Exception:
                        pass

            if self._running:
                log.debug("_reader_loop: sleeping 1s before reconnect")
                time.sleep(1)

        log.debug("_reader_loop: exiting (running=False)")

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

    def iter_frames(self) -> Iterator[bytes]:
        """Yield frames as they arrive. Blocks between frames. Safe for multiple callers.

        Yields the already-buffered frame immediately on first call (no wait) so the
        browser receives data before any Safari speculative-connection timeout fires.
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
                log.error("iter_frames: reader thread died — raising to trigger browser retry")
                raise RuntimeError("RTSPSFrameBuffer reader thread died")
            if frame is not None and frame is not last:
                last = frame
                frames_yielded += 1
                log.debug("iter_frames: yielding frame #%d size=%d", frames_yielded, len(frame))
                yield frame
        log.debug("iter_frames: client detached after %d frames", frames_yielded)

    def close(self) -> None:
        log.debug("RTSPSFrameBuffer.close: stopping reader thread")
        with self._cond:
            self._running = False
            self._cond.notify_all()
        log.debug("RTSPSFrameBuffer.close: done")
