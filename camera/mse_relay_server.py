#!/usr/bin/env python3
"""Bambu Lab camera relay: every model, one fMP4 stream for browsers, plus JPEG snapshots.

Two camera families exist and the relay picks per printer:

  rtsps  X1, X1C, X1E, H2D, H2S, P2S. The printer sends H.264 on RTSPS:322. Packets are
         copied into fragmented MP4 with no decode and no encode.
  jpeg   A1, A1 mini, P1P, P1S. The printer sends JPEG stills (about 0.5 fps) on TLS:6000.
         Each still is encoded once with libx264 as an intra-only H.264 frame, so the same
         fMP4 stream and the same <video> page serve it. One encode every two seconds.

Routes (``?printer=<name>`` selects a printer; the first configured one is the default):
  /                 viewer page with an FPS meter (index.html next to this file)
  /printers         JSON list of configured printers with their camera family
  /stream.mp4       live fMP4: init segment, then fragments from the last keyframe on
  /snapshot.jpg     one JPEG: the latest still (jpeg) or the latest frame decoded (rtsps)
  /stats            JSON counters for one printer

Configuration (environment), one printer:
  BAMBU_HOSTNAME     printer hostname or IP
  BAMBU_ACCESS_CODE  the printer's LAN access code
  BAMBU_NAME         display name, default "printer"
  BAMBU_CAMERA       "rtsps" or "jpeg"; default: detect (port 322 open → rtsps, else jpeg)
or several printers:
  BAMBU_PRINTERS     JSON list of {"name", "host", "access_code", "camera"?}

Run:
  python3 server.py [--port 8765] [--bind 127.0.0.1] [-v]
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import queue
import socket
import ssl
import struct
import sys
import threading
import time
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import av

log = logging.getLogger("bambu-mse-stream")
HERE = Path(__file__).resolve().parent

# rtsp over TLS; the printer's self-signed certificate is not verified
AV_OPTIONS = {"rtsp_transport": "tcp", "tls_verify": "0", "allowed_media_types": "video"}
# rtsps: 200 ms fragments keep MSE latency low; frag_keyframe alone would hold a whole GOP
MUX_RTSPS = {"movflags": "frag_keyframe+empty_moov+default_base_moof", "frag_duration": "200000"}
# jpeg: every still is muxed through its own one-packet container, closed at once. The mp4
# muxer only flushes a fragment when the NEXT packet arrives (frag_every_frame included), which
# would hold each still back until the following one, two or three seconds later.
MUX_JPEG = {"movflags": "frag_keyframe+empty_moov+default_base_moof"}
# a late joiner gets the fragments since the last keyframe; past this many the GOP is not
# worth replaying and the backlog is dropped until the next keyframe
GOP_FRAGS_CAP = 300
GOP_PKTS_CAP = 600
# intra-only, fast: one encode every ~2 s costs a few tens of ms
X264_JPEG = {"preset": "ultrafast", "tune": "zerolatency", "crf": "22", "x264-params": "keyint=1:scenecut=0"}
# The printer's RTP packets carry dts only (no pts), a garbage duration, and the dts
# jumps on RTCP resync. The mp4 muxer rejects all three, so timestamps are rebuilt:
# monotonic from 0, each step clamped to [1, 1 s] at 90 kHz.
TB_HZ = 90000
JPEG_NOMINAL_GAP = 2 * TB_HZ  # the A1 sends a still every 2 to 3 s
RATE_WINDOW = 10.0  # seconds the /stats rates average over


def _codec_string(ex: bytes) -> str:
    """RFC 6381 avc1.PPCCLL from the stream's extradata, avcC or Annex B SPS."""
    if len(ex) >= 4 and ex[0] == 1:  # avcC: [1, profile, compat, level, ...]
        return "avc1.%02X%02X%02X" % (ex[1], ex[2], ex[3])
    i = 0
    while i < len(ex) - 4:  # Annex B: 00 00 01 <nal>, SPS is nal_type 7
        if ex[i:i + 3] == b"\x00\x00\x01":
            nal = ex[i + 3]
            if nal & 0x1F == 7 and i + 7 <= len(ex):
                return "avc1.%02X%02X%02X" % (ex[i + 4], ex[i + 5], ex[i + 6])
            i += 3
        else:
            i += 1
    return ""


def detect_camera(host: str) -> str:
    """rtsps when the printer answers on 322, else jpeg (the A1/P1 family has no 322)."""
    try:
        with socket.create_connection((host, 322), timeout=3):
            return "rtsps"
    except OSError:
        return "jpeg"


def _stamp_moof(moof: bytes, ts: int, seq: int) -> bytes:
    """Rewrite a one-packet container's moof: its tfdt reads 0 (the muxer counts from the
    container's first packet) and its mfhd sequence reads 1. Put the still's real timestamp
    and a running sequence in, so MSE places the fragment on the stream's timeline."""
    out = bytearray(moof)
    i = 8  # moof header
    while i + 8 <= len(out):
        size, kind = struct.unpack_from(">I4s", out, i)
        if size < 8:
            break
        if kind == b"mfhd":
            struct.pack_into(">I", out, i + 12, seq & 0xFFFFFFFF)
        elif kind == b"traf":
            j = i + 8
            while j + 8 <= i + size:
                s2, k2 = struct.unpack_from(">I4s", out, j)
                if s2 < 8:
                    break
                if k2 == b"tfdt":
                    if out[j + 8] == 1:
                        struct.pack_into(">Q", out, j + 12, ts)
                    else:
                        struct.pack_into(">I", out, j + 12, ts & 0xFFFFFFFF)
                j += s2
        i += size
    return bytes(out)


class _BoxSplitter:
    """Collect muxer writes and cut them into complete MP4 boxes."""

    def __init__(self, on_box):
        self._buf = bytearray()
        self._on_box = on_box

    def write(self, data: bytes) -> int:
        self._buf += data
        while len(self._buf) >= 8:
            size, kind = struct.unpack_from(">I4s", self._buf, 0)
            if size == 1:  # 64-bit size
                if len(self._buf) < 16:
                    break
                size = struct.unpack_from(">Q", self._buf, 8)[0]
            if size < 8 or len(self._buf) < size:
                break
            self._on_box(kind.decode("latin1"), bytes(self._buf[:size]))
            del self._buf[:size]
        return len(data)

    def flush(self) -> None:
        pass


def _jpeg_frames(host: str, code: str, timeout: float = 15.0):
    """Yield JPEG stills from the A1/P1 camera on TLS:6000 until the socket fails."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        ctx.set_ciphers("DEFAULT@SECLEVEL=0")  # the printer's leaf key is short
    except ssl.SSLError:
        pass
    raw = socket.create_connection((host, 6000), timeout=timeout)
    with ctx.wrap_socket(raw, server_hostname=host) as s:
        s.settimeout(timeout)
        auth = struct.pack("<4I", 0x40, 0x3000, 0, 0) + b"bblp".ljust(32, b"\0") + code.encode().ljust(32, b"\0")
        s.sendall(auth)

        def read_exact(n: int) -> bytes:
            buf = bytearray()
            while len(buf) < n:
                chunk = s.recv(n - len(buf))
                if not chunk:
                    raise ConnectionError("camera closed the socket")
                buf += chunk
            return bytes(buf)

        while True:
            size = struct.unpack("<I", read_exact(16)[:4])[0]
            if not 0 < size < 8 * 1024 * 1024:
                raise ConnectionError(f"implausible frame size {size}")
            jpeg = read_exact(size)
            if jpeg[:2] == b"\xff\xd8" and jpeg[-2:] == b"\xff\xd9":
                yield jpeg


class Relay:
    """One printer: one camera reader, fMP4 fan-out to HTTP clients, JPEG snapshots."""

    def __init__(self, name: str, host: str, code: str, camera: str | None):
        self.name = name
        self.host = host
        self._code = code
        self._camera_cfg = camera
        self._lock = threading.Lock()
        self._clients: list[queue.Queue] = []
        self._init: bytes = b""
        self._init_done = False  # ftyp+moov seen once per session; later ones are dropped
        self._pending_moof: bytes | None = None
        # fragments since the last keyframe-led fragment, so a joiner starts decodable
        self._gop_frags: list[bytes] = []
        self._cur_key = False
        self._frag_emitted = False
        # rtsps: raw H.264 packets since the last keyframe, decoded on demand for /snapshot.jpg
        self._gop_pkts: list[bytes] = []
        self._extradata: bytes = b""
        # jpeg: the latest still, served as-is by /snapshot.jpg
        self._last_jpeg: bytes = b""
        self.stats = {
            "name": name, "camera": camera or "", "codec": "", "width": 0, "height": 0,
            "packets": 0, "bytes": 0, "keyframes": 0, "fragments": 0, "gop_packets": 0,
            "clients": 0, "reconnects": 0, "started": time.time(), "last_error": "",
        }
        # seeded so the very first /stats call measures since start instead of reading 0
        self._samples: list[tuple[float, int, int, int]] = [(time.time(), 0, 0, 0)]
        self._since_key = 0
        threading.Thread(target=self._reader, daemon=True, name=f"camera-{name}").start()

    # -- client side --------------------------------------------------------
    def subscribe(self) -> tuple[queue.Queue, bytes, list[bytes]]:
        q: queue.Queue = queue.Queue(maxsize=400)
        with self._lock:
            self._clients.append(q)
            self.stats["clients"] = len(self._clients)
            return q, self._init, list(self._gop_frags)

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)
            self.stats["clients"] = len(self._clients)

    def rates(self) -> dict:
        """Counters plus rates over the last RATE_WINDOW seconds, so a 0.5 fps camera reads
        0.5 and not a 0/1 flicker from one-second polling. The oldest sample inside the
        window is the baseline; one older sample is kept so a single call after a long
        pause still measures against something and does not read 0."""
        now = time.time()
        s = self.stats
        with self._lock:
            self._samples.append((now, s["packets"], s["bytes"], s["fragments"]))
            while len(self._samples) > 2 and now - self._samples[1][0] > RATE_WINDOW:
                self._samples.pop(0)
            t0, p0, b0, f0 = self._samples[0]
        dt = max(now - t0, 1.0)
        return {**s, "uptime_s": round(now - s["started"], 1),
                "packets_per_s": round((s["packets"] - p0) / dt, 2),
                "kbps": round((s["bytes"] - b0) * 8 / dt / 1000, 1),
                "fragments_per_s": round((s["fragments"] - f0) / dt, 2)}

    def snapshot(self) -> bytes | None:
        """Latest frame as JPEG. jpeg camera: the still itself. rtsps: decode the last GOP."""
        with self._lock:
            if self._last_jpeg:
                return self._last_jpeg
            pkts, extradata = list(self._gop_pkts), self._extradata
        if not pkts:
            return None
        dec = av.CodecContext.create("h264", "r")
        dec.extradata = extradata
        frame = None
        for data in pkts:
            for f in dec.decode(av.Packet(data)):
                frame = f
        for f in dec.decode(None):
            frame = f
        if frame is None:
            return None
        return _frame_to_jpeg(frame)

    # -- muxer output -------------------------------------------------------
    def _on_box(self, kind: str, box: bytes) -> None:
        if kind in ("ftyp", "moov"):
            if not self._init_done:
                self._init += box
                self._init_done = kind == "moov"
            return
        if kind == "moof":
            self._pending_moof = box
            return
        if kind == "mdat" and self._pending_moof is not None:
            frag = self._pending_moof + box
            self._pending_moof = None
            self._frag_emitted = True
            self.stats["fragments"] += 1
            with self._lock:
                if self._cur_key:
                    self._gop_frags = [frag]
                elif len(self._gop_frags) >= GOP_FRAGS_CAP:
                    self._gop_frags = []  # a GOP this long is not replayed; joiners wait
                else:
                    self._gop_frags.append(frag)
                for q in self._clients:
                    try:
                        q.put_nowait(frag)
                    except queue.Full:
                        pass  # slow client: drop, MSE resyncs on the next keyframe

    def _drop_clients(self) -> None:
        """End every client's response. The next session's timestamps restart at 0, and a
        browser fed those on its old timeline parks for good; a closed response makes it
        reconnect and take the new init segment instead."""
        with self._lock:
            for q in self._clients:
                try:
                    q.put_nowait(None)
                except queue.Full:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
                    q.put_nowait(None)

    def _mux(self, oc, os_, pkt, is_key: bool, raw: bytes | None) -> None:
        """Account one packet, keep the snapshot GOP, mux it, track fragment keyframes."""
        self.stats["packets"] += 1
        self.stats["bytes"] += pkt.size
        if is_key:
            self.stats["keyframes"] += 1
            if self._since_key:
                self.stats["gop_packets"] = self._since_key
            self._since_key = 0
        self._since_key += 1
        if raw is not None:
            with self._lock:
                if is_key:
                    self._gop_pkts = [raw]
                elif len(self._gop_pkts) >= GOP_PKTS_CAP:
                    self._gop_pkts = []
                else:
                    self._gop_pkts.append(raw)
        self._frag_emitted = False
        pkt.stream = os_
        oc.mux(pkt)
        # rtsps: the muxer flushes the previous fragment while writing the packet that
        # opens the next one, so after a flush this packet leads the new fragment.
        # jpeg (frag_every_frame, all intra): every fragment is keyframe-led.
        if self._frag_emitted:
            self._cur_key = is_key if self.stats["camera"] == "rtsps" else True

    def _reset_stream(self) -> None:
        """Forget the session's init segment and backlogs, and end every client. Called when
        a session starts (the new one has its own timeline) and when one fails (nothing
        stale is served while the printer is away: /stream.mp4 and /snapshot.jpg answer 503
        until a new session is up)."""
        self._drop_clients()
        with self._lock:
            self._init = b""
            self._init_done = False
            self._pending_moof = None
            self._gop_frags = []
            self._gop_pkts = []
            self._last_jpeg = b""

    # -- reader -------------------------------------------------------------
    def _reader(self) -> None:
        while True:
            try:
                # stats["camera"] is only written once a session is up, so a printer that is
                # off (322 refused → "jpeg") does not flip an RTSPS printer's family
                camera = self._camera_cfg or detect_camera(self.host)
                if camera == "rtsps":
                    self._run_rtsps()
                else:
                    self._run_jpeg()
            except Exception as e:  # noqa: BLE001
                msg = (type(e).__name__ + ": " + str(e)[:200]).replace(self._code, "****")
                self.stats["last_error"] = msg
                log.warning("%s: reader error: %s", self.name, msg)
            self._reset_stream()
            self.stats["reconnects"] += 1
            time.sleep(2)

    def _run_rtsps(self) -> None:
        url = f"rtsps://bblp:{self._code}@{self.host}:322/streaming/live/1"
        ic = av.open(url, options=AV_OPTIONS, timeout=15.0)
        try:
            vs = ic.streams.video[0]
            cc = vs.codec_context
            self.stats["camera"] = "rtsps"
            self.stats["codec"] = _codec_string(cc.extradata or b"")
            self.stats["width"], self.stats["height"] = cc.width, cc.height
            self._reset_stream()
            self._extradata = bytes(cc.extradata or b"")
            oc = av.open(_BoxSplitter(self._on_box), mode="w", format="mp4", options=MUX_RTSPS)
            try:
                os_ = oc.add_stream_from_template(vs)
                started = False
                prev_in = None
                out_ts = 0
                for pkt in ic.demux(vs):
                    if pkt.dts is None:
                        continue
                    if not started:
                        if not pkt.is_keyframe:
                            continue  # the init + first fragment must start on a keyframe
                        started = True
                        self._cur_key = True
                    if prev_in is not None:
                        step = pkt.dts - prev_in
                        out_ts += step if 0 < step <= TB_HZ else TB_HZ // 30
                    prev_in = pkt.dts
                    raw = bytes(pkt)
                    pkt.dts = pkt.pts = out_ts
                    pkt.duration = 0
                    self._mux(oc, os_, pkt, bool(pkt.is_keyframe), raw)
            finally:
                try:
                    oc.close()
                except Exception:  # noqa: BLE001
                    pass
        finally:
            ic.close()

    def _run_jpeg(self) -> None:
        dec = av.CodecContext.create("mjpeg", "r")
        # The encoder lives on a stream of a container that is never written to; it is the
        # template every per-still container copies its codec parameters (SPS/PPS) from.
        enc_host = av.open(io.BytesIO(), mode="w", format="mp4")
        enc = None
        t0 = None
        try:
            for jpeg in _jpeg_frames(self.host, self._code):
                frames = dec.decode(av.Packet(jpeg))
                if not frames:
                    continue
                frame = frames[-1].reformat(format="yuv420p")
                if enc is None:
                    self.stats["camera"] = "jpeg"
                    self.stats["width"], self.stats["height"] = frame.width, frame.height
                    self._reset_stream()
                    enc = enc_host.add_stream("libx264", rate=1)
                    enc.width, enc.height = frame.width, frame.height
                    enc.pix_fmt = "yuv420p"
                    enc.time_base = Fraction(1, TB_HZ)
                    enc.options = dict(X264_JPEG)
                    self._cur_key = True  # every still is an intra frame
                    t0 = time.monotonic()
                with self._lock:
                    self._last_jpeg = jpeg
                ts = int((time.monotonic() - t0) * TB_HZ)
                frame.pts = ts
                frame.time_base = Fraction(1, TB_HZ)
                for pkt in enc.encode(frame):
                    # the encoder stamps packets in its own 1/1 time base; the muxer would
                    # rescale pts and duration by 90000 and refuse the result
                    pkt.time_base = Fraction(1, TB_HZ)
                    pkt.pts = pkt.dts = ts
                    pkt.duration = JPEG_NOMINAL_GAP
                    if not self.stats["codec"]:
                        # the encoder is open now: writing the host's header copies its
                        # SPS/PPS into the template's parameters, which every per-still
                        # container's moov (avcC) is built from
                        enc_host.start_encoding()
                        self.stats["codec"] = _codec_string(bytes(enc.codec_context.extradata or b""))
                    # one container per still: closing it flushes the fragment now. Its
                    # ftyp/moov repeat the first session's and _on_box drops them; its moof
                    # is stamped with the still's timestamp and a running sequence number.
                    seq = self.stats["packets"] + 1
                    on_box = lambda k, b, ts=ts, seq=seq: self._on_box(k, _stamp_moof(b, ts, seq) if k == "moof" else b)  # noqa: E731
                    oc = av.open(_BoxSplitter(on_box), mode="w", format="mp4", options=MUX_JPEG)
                    try:
                        os_ = oc.add_stream_from_template(enc)
                        self._mux(oc, os_, pkt, True, None)
                    finally:
                        oc.close()
        finally:
            try:
                enc_host.close()
            except Exception:  # noqa: BLE001
                pass


def _frame_to_jpeg(frame) -> bytes:
    out = io.BytesIO()
    oc = av.open(out, mode="w", format="mjpeg")
    try:
        s = oc.add_stream("mjpeg", rate=1)
        s.width, s.height = frame.width, frame.height
        s.pix_fmt = "yuvj420p"
        for pkt in s.encode(frame.reformat(format="yuvj420p")):
            oc.mux(pkt)
        for pkt in s.encode(None):
            oc.mux(pkt)
    finally:
        oc.close()
    return out.getvalue()


class Handler(BaseHTTPRequestHandler):
    relays: dict[str, Relay] = {}

    def log_message(self, fmt, *args):  # quiet
        log.debug(fmt, *args)

    def _relay(self, query: dict) -> Relay | None:
        name = (query.get("printer") or [next(iter(self.relays))])[0]
        return self.relays.get(name)

    def do_GET(self):  # noqa: N802
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        path = parts.path
        if path == "/":
            self._send(200, "text/html; charset=utf-8", (HERE / "index.html").read_bytes())
            return
        if path == "/printers":
            body = [{"name": r.name, "camera": r.stats["camera"]} for r in self.relays.values()]
            self._send(200, "application/json", json.dumps(body).encode())
            return
        relay = self._relay(query)
        if relay is None:
            self._send(404, "text/plain", b"unknown printer")
        elif path == "/stats":
            self._send(200, "application/json", json.dumps(relay.rates()).encode())
        elif path == "/stream.mp4":
            self._stream(relay)
        elif path == "/snapshot.jpg":
            try:
                jpeg = relay.snapshot()
            except Exception as e:  # noqa: BLE001
                log.warning("%s: snapshot failed: %s", relay.name, e)
                jpeg = None
            if jpeg:
                self._send(200, "image/jpeg", jpeg)
            else:
                self._send(503, "text/plain", b"no frame yet")
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _stream(self, relay: Relay) -> None:
        q, init, backlog = relay.subscribe()
        try:
            deadline = time.time() + 20
            while not init and time.time() < deadline:
                time.sleep(0.2)
                relay.unsubscribe(q)
                q, init, backlog = relay.subscribe()
            if not init:
                self._send(503, "text/plain", b"no stream yet")
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(init)
            for frag in backlog:
                self.wfile.write(frag)
            self.wfile.flush()
            while True:
                frag = q.get(timeout=30)
                if frag is None:
                    break  # the session ended; the client reconnects for the new one
                self.wfile.write(frag)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, queue.Empty, OSError):
            pass
        finally:
            relay.unsubscribe(q)


def load_printers() -> list[dict]:
    raw = os.environ.get("BAMBU_PRINTERS", "").strip()
    if raw:
        printers = json.loads(raw)
        for p in printers:
            if not p.get("name") or not p.get("host") or not p.get("access_code"):
                sys.exit("BAMBU_PRINTERS entries need name, host and access_code")
        return printers
    host = os.environ.get("BAMBU_HOSTNAME", "")
    code = os.environ.get("BAMBU_ACCESS_CODE", "")
    if not host or not code:
        sys.exit("set BAMBU_HOSTNAME and BAMBU_ACCESS_CODE, or BAMBU_PRINTERS, in the environment")
    return [{"name": os.environ.get("BAMBU_NAME", "printer"), "host": host, "access_code": code,
             "camera": os.environ.get("BAMBU_CAMERA") or None}]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--bind", default="127.0.0.1", help="0.0.0.0 to view from another device on the LAN")
    ap.add_argument("-v", action="store_true", help="debug logging")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.v else logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    for p in load_printers():
        camera = p.get("camera") or None
        if camera not in (None, "rtsps", "jpeg"):
            sys.exit(f"printer {p['name']!r}: camera must be rtsps or jpeg")
        Handler.relays[p["name"]] = Relay(p["name"], p["host"], p["access_code"], camera)
        log.info("printer %s: host %s camera %s", p["name"], p["host"], camera or "detect")

    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    srv.daemon_threads = True
    log.info("viewer: http://%s:%d/", "localhost" if args.bind == "127.0.0.1" else args.bind, args.port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
