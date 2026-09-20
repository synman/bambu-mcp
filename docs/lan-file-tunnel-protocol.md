# LAN File Tunnel (port 6000) — protocol reference and read-only client

Reference for the Bambu Lab **LAN file tunnel** on TCP port 6000 (H2 series, P2S, X2D): what it is, how to talk to it, what the H2D
answers, and what fails. It is the only LAN route to the printer's internal (eMMC) model cache. **Read-only client** — the code below never uploads,
deletes, or changes printer state. The one measured write is described under Writes and needs the operator's explicit permission.

- **Scope / owner:** `bambu-mcp` docs (agent-managed). Applies to the ecosystem (`bpm`, `bpa`, `bambu-mcp`); none of them has a `:6000` client today.
- **Not covered:** the A1/P1 MJPEG camera (also port 6000, different protocol) — see `bambu://knowledge/protocol`.
- **Depth lives in the KB.** This page is the operator/agent how-to. Facts, evidence tiers and correction history are in
  `kb_get("bambu-tcp-6000-protocol")`, `kb_get("bambu-tcp-6000-command-reference")`, `kb_get("bambu-h2d-internal-storage-emmc-model-cache")`,
  `kb_get("bambu-mqtt-storage-capability-fields")`.

## When to use it

| You want to | Use |
|---|---|
| List the internal cache (what Bambu Studio "Print Plate" sent) | `list_files(storage="internal")` |
| List the USB stick's models | `list_files()` |
| Page a long list (either storage) | `list_files(storage="internal", start=10, count=5)`; `start` is a 0-based offset, and `count` past the end clamps |
| Read one member of a `.3mf` on the USB stick (`Metadata/slice_info.config` is verified) without downloading it | `SUB_FILE` `<path>#<member>` |
| Download a file from the USB stick | `download(path)` (FTPS also works) |
| Read or download a file from the **internal** cache | **not possible** on H2D fw 01.03.00.00 — every path outside `/media/usb0` returns `result 2` |
| Read a member of the printer's **current or last job** project (`slice_info.config`, `model_settings.config`, plate PNGs) | `project_member(member)` (Studio's part-skip route, `mem:/16`); past jobs are not reachable |

## Preconditions

- **Target an H2 series, P2S or X2D printer only. Never an A1 or P1**: their port 6000 is the MJPEG camera, a different protocol. Only an H2D has been tested; X2D reportedly differs.
- Printer on the LAN, **LAN access code** available (never print or log it; resolve it in-process, e.g. `bambu-mcp/auth.py` `get_printer_credentials`).
- Python 3.12 (the `bambu-mcp` venv), stdlib only for the client. It sets `DEFAULT@SECLEVEL=0` because the H2D certificate has a weak key; certificate verification is off.
- Printer write protection (`bambu-ecosystem.md`) still applies: this client's helpers send only `LIST_INFO` (1) and `FILE_DOWNLOAD` (4), and `call` also sends `SUB_FILE` (2). `call` is a raw passthrough, so read-only holds by discipline: send only cmdtypes 1, 2, 4 and the read-only ability query 7.
  Do **not** send `FILE_UPLOAD`, `FILE_DEL` or `TASK_CANCEL` without the operator's explicit permission in the current turn.

## Protocol in one screen

Implicit TLS. Every frame = 16-byte little-endian header `payload_len · magic · seq · 0` + payload.

```text
login   magic 0x0101013f  payload = "bblp"+NULs to 8 bytes  +  access code+NULs to 8 bytes     -> ack (4 zero bytes)
setup   magic 0x0102013f  {"sequence":0,"mtype":12291,"req":{"t_av":1,"mtype":12289,"peer_t":3,"pid":"x","ver":"02.03.00.00"}}
rpc     magic 0x0102013f  {"mtype":12289,"cmdtype":N,"sequence":S,"req":{...}}   -> replies: {"cmdtype","result","sequence","reply"} [+ "\n\n" + bytes]
        result 1 = more frames follow; the first result != 1 ends the request
```

`cmdtype`: 1 `LIST_INFO`, 2 `SUB_FILE`, 3 `FILE_DEL`, 4 `FILE_DOWNLOAD`, 5 `FILE_UPLOAD`, 7 `REQUEST_MEDIA_ABILITY`, `0x1000` `TASK_CANCEL`.

## Reference client (tested against an H2D, fw 01.03.00.00)

Save it as `tunnel6000.py` outside the repo (for example in a scratch directory) and put that directory on `PYTHONPATH`. Written from the protocol facts above; it is not derived from any third-party implementation's code.

```python
"""Minimal stdlib client for the Bambu Lab :6000 LAN file tunnel (H2/P2S/X2D). Read-only helpers.

Wire facts: see docs/lan-file-tunnel-protocol.md. Written from those facts, not copied from any
third-party implementation.
"""
import hashlib
import json
import socket
import ssl
import struct

MAGIC_LOGIN = 0x0101013F  # client -> printer, subchannel 0x01
MAGIC_CTRL = 0x0102013F   # client -> printer, subchannel 0x02 (setup + every RPC)
CONTINUE = 1              # result code: more frames follow for this sequence


class TunnelError(RuntimeError):
    pass


class Tunnel:
    def __init__(self, host, access_code, port=6000, timeout=30.0):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE                # printer cert is a weak-key leaf, CN = serial
        ctx.set_ciphers("DEFAULT@SECLEVEL=0")          # required: the H2D key is below OpenSSL's default level
        self.sock = ctx.wrap_socket(socket.create_connection((host, port), timeout), server_hostname=host)
        self.frame_seq = 0
        self.cmd_seq = 0
        self._send(MAGIC_LOGIN, b"bblp".ljust(8, b"\0") + access_code.encode().ljust(8, b"\0"))
        self._recv()                                   # login ack: 4 zero bytes
        setup = {"sequence": 0, "mtype": 12291,
                 "req": {"t_av": 1, "mtype": 12289, "peer_t": 3, "pid": "probe", "ver": "02.03.00.00"}}
        self._send(MAGIC_CTRL, json.dumps(setup).encode())
        self._recv()                                   # setup ack: {"result":0,...}

    def close(self):
        self.sock.close()

    def _send(self, magic, payload):
        self.sock.sendall(struct.pack("<IIII", len(payload), magic, self.frame_seq, 0) + payload)
        self.frame_seq += 1

    def _read(self, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise TunnelError("connection closed")
            buf.extend(chunk)
        return bytes(buf)

    def _recv(self):
        length, magic, _seq, _ = struct.unpack("<IIII", self._read(16))
        return magic, self._read(length)

    def rpc(self, cmdtype, req, param=b""):
        """Send one request (optional binary parameter after a blank line); yield (reply_json, body_bytes) per frame until the result is not CONTINUE."""
        self.cmd_seq += 1
        payload = json.dumps({"mtype": 12289, "cmdtype": cmdtype, "sequence": self.cmd_seq, "req": req}).encode()
        self._send(MAGIC_CTRL, payload + (b"\n\n" + param if param else b""))
        while True:
            _magic, payload = self._recv()
            head, _, body = payload.partition(b"\n\n")
            reply = json.loads(head)
            if reply.get("sequence") != self.cmd_seq or "result" not in reply:
                continue                               # printer-initiated notify, or a stale frame
            yield reply, body
            if reply["result"] != CONTINUE:
                return

    def call(self, cmdtype, req):
        """Run rpc() to completion: (final_reply, concatenated_body)."""
        body, last = bytearray(), None
        for last, chunk in self.rpc(cmdtype, req):
            body.extend(chunk)
        return last, bytes(body)

    def list_files(self, type="model", storage=None, start=None, count=None):
        """LIST_INFO. storage: None/'external' = USB stick; 'internal' = eMMC cache (any other string also = internal)."""
        req = {"type": type, "api_version": 2, "notify": "DETAIL"}
        if storage is not None:
            req["storage"] = storage
        if start is not None:
            req["start"] = start                       # integer JSON only; strings/floats are ignored
        if count is not None:
            req["count"] = count
        reply, _ = self.call(1, req)
        if reply["result"] != 0:
            raise TunnelError(f"LIST_INFO result {reply['result']}")
        return reply["reply"]["file_lists"]

    def project_member(self, member):
        """FILE_DOWNLOAD mem:/16: one member of the printer's CURRENT or last job project (Studio's get_project_file). Verifies md5."""
        param = json.dumps({"sequence_id": 1, "version": 1, "peer_host": "studio",
                            "command": "get_project_file", "file_rel_path": member}).encode()
        req = {"path": "mem:/16", "offset": 0, "mem_dl_param_size": len(param)}
        head, data, final = None, bytearray(), None
        for reply, chunk in self.rpc(4, req, param):
            info = reply.get("reply", {})
            if head is None and "mem_dl_param_size" in info:
                n = info["mem_dl_param_size"]
                head = json.loads(chunk[:n])
                data.extend(chunk[n:])
            else:
                data.extend(chunk)
            final = info
        if head is None or head.get("err_no") != 0:
            raise TunnelError(f"project member {member!r}: err_no {None if head is None else head.get('err_no')}")
        if hashlib.md5(data).hexdigest() != final.get("file_md5"):
            raise TunnelError("md5 mismatch")
        return bytes(data)

    def download(self, path):
        """FILE_DOWNLOAD a file under /media/usb0 (eMMC paths are rejected: result 2). Verifies md5."""
        reply, data = self.call(4, {"path": path})
        if reply["result"] != 0:
            raise TunnelError(f"FILE_DOWNLOAD result {reply['result']}")
        info = reply["reply"]
        if len(data) != info["total"] or hashlib.md5(data).hexdigest() != info["file_md5"]:
            raise TunnelError("size or md5 mismatch")
        return data
```

## Recipes

Resolve the address and code in-process, then:

```python
import auth                                    # bambu-mcp/auth.py, run with cwd = bambu-mcp
from tunnel6000 import Tunnel, TunnelError

print(auth.get_configured_printer_names())     # names are user-chosen and case-sensitive
c = auth.get_printer_credentials("<name>")     # {"ip", "access_code", "serial"}
t = Tunnel(c["ip"], c["access_code"])

internal = t.list_files(storage="internal")    # [{"name","path","size","time"}] (history/ jobs plus bbl/ factory samples): size in bytes, path like /userdata/model/history/<name>.gcode.3mf, newest first, flat, no directory entries; time unit not verified
usb      = t.list_files()                      # same shape, /media/usb0/... paths
page     = t.list_files(storage="internal", start=10, count=5)   # JSON integers only; a string or float is silently ignored (you get everything)

# metadata of a USB .3mf without downloading it: SUB_FILE '<path>#<member>'
reply, body = t.call(2, {"paths": [usb[0]["path"] + "#Metadata/slice_info.config"]})
assert reply["result"] == 0                    # reply = {"result": 0, ...}; body = the raw XML bytes of that one member. Only single-path requests are measured.

data = t.download(usb[0]["path"])              # md5 and size verified

# members of the CURRENT/last job project, whatever storage it came from
xml = t.project_member("Metadata/slice_info.config")   # raises TunnelError on err_no -2 (absent or refused)
t.close()
```

Rules that bite:

- **`storage`**: omit it (or send exactly `"external"`) for the USB stick; **any other string means internal**, including nonsense. Use `"internal"` and `"external"`; do not use `"emmc"`/`"udisk"` (advertised by the printer, not honoured by `LIST_INFO`).
- **`type`** is `model`, `timelapse` or `video`; anything else is `result 16`.
- **`api_version`** in `LIST_INFO` is ignored. `REQUEST_MEDIA_ABILITY` accepts 1–3 and returns `result 18` for 4+.
- `project_member` serves only the printer's current or last project. On 2026-09-19 it returned `slice_info.config`, `model_settings.config`, `plate_1.png` and `pick_1.png`, and `err_no -2` for `plate_1.gcode`, `3D/3dmodel.model` and `project_settings.config`.
- `LIST_INFO` cannot walk directories: path fields are ignored and only indexed `.3mf` models come back.
- `FILE_DOWNLOAD` has no resume: a request `offset` is ignored and the whole file streams in 20,480-byte frames.

## Writes (measured once on the H2D, operator-authorised 2026-09-19)

Not implemented in the client on purpose. Ask the operator first, every time.

- **Upload works.** `FILE_UPLOAD` init `{"type":"model","storage":"emmc","path":"<name>","total":<bytes>}` returns `result 1` with `{"chunk_size":255,"offset":0}` (KiB). For a file under 255 KiB send one fragment on the same sequence, `{"frag_id":0,"offset":0,"size":<bytes>,"file_md5":"<md5>"}` then a blank line then the bytes, and read one final `result 0`. The file then heads the internal list.
- **It silently evicts.** `history/` is an 8-file FIFO. A ninth file pushed out the oldest job, and nothing warned. Assume any upload destroys the oldest cached job.
- **External (USB) has full create, read and delete over `:6000`.** Upload with `"storage":"udisk"` lands at `/media/usb0/<name>` and downloads back byte-identical. Delete with the full-path form `{"paths":["/media/usb0/<name>"]}` removes it (`result 0`, re-list to confirm). Uploading the same name again overwrites it (init `19`, then `0`). There is no rename command: an unknown cmdtype such as 6 gets no reply, so a move is download, upload under the new name, then delete the old.
- **Internal delete does not work.** `FILE_DEL` `{"delete":["<name>"],"storage":"emmc"}` returns `result 0` but the reply's `paths` shows `/media/usb0/timelapse/<name>`, so nothing internal is removed. The `paths` form returns `result 2`. Re-list after any delete and never trust `result 0`. The uploaded test file, `zz_mcp_tunnel_test.gcode.3mf`, is still in the cache.

## Failure branches

| Symptom | Meaning | Action |
|---|---|---|
| `ssl.SSLError … HANDSHAKE_FAILURE` | client TLS level too high for the weak certificate | keep `set_ciphers("DEFAULT@SECLEVEL=0")` |
| login rejected or no reply after the first frame (the client does not check the two acks, and how a bad access code surfaces is not characterized) | wrong login layout: the 64-byte auth used by the A1/P1 camera is a different protocol | use the 16-byte login above |
| `result 2` on `FILE_DOWNLOAD` / `SUB_FILE` | path is not under `/media/usb0` (validated, even for non-existent files) | expected for the eMMC cache on H2D fw 01.03; nothing to retry |
| `result 15` on `FILE_DOWNLOAD` | path is under `/media/usb0` but the file is missing; or you used the `{file:<name>}` form, which resolves under `/media/usb0/timelapse/` | check the path against a fresh `list_files()` |
| `result 14` on `SUB_FILE` | no such member in the container, or a bare path with no `#member` | check the member name against the 3MF layout |
| `result 16` on `LIST_INFO` | `type` not one of `model`/`timelapse`/`video` | fix `type` |
| `result 18` on ability | `api_version` > 3 | send ≤ 3 |
| a job you just sent is not listed | you listed the wrong `storage`, or the cache evicted it (the `history/` area is an 8-file FIFO, so the ninth job pushes out the oldest) | list `internal` again |
| connection closes mid-session, or resets right after connect | malformed frame, or too many sessions opened in a short time (resets began after about seven in under a minute) | wait, then reconnect once from login; do not loop |
| `TunnelError: project member ...: err_no -2` | that member is absent or refused for the printer's current project | try `Metadata/slice_info.config`; the gcode and `3D/3dmodel.model` were refused on the H2D |
| `REQUEST_MEDIA_ABILITY` never replies | the request had no `peer` field | send `{"peer": "studio", "api_version": 3}` |

Stop and report to the operator (do not retry loops) if login is refused repeatedly: repeated failed authentication can lock the printer's services.

## Verification

Run against a real printer before trusting a change to this page or the client. Read-only: `LIST_INFO`, one `SUB_FILE`, one small `FILE_DOWNLOAD`, and one download that is expected to be refused. Needs a USB stick holding at least one model under 300 KB, at least one internal file, and a printer that has run a job since boot.

```bash
cd ~/ai/forge/bambu/bambu-mcp && D=$(mktemp -d) && trap 'rm -rf "$D"' EXIT && python3 - "$D" <<'EOF'
import pathlib, re, sys
doc = pathlib.Path("docs/lan-file-tunnel-protocol.md").read_text()
fence = chr(96) * 3
code = re.search("## Reference client.*?" + fence + "python\n(.*?)" + fence, doc, re.S).group(1)
pathlib.Path(sys.argv[1], "tunnel6000.py").write_text(code)
EOF
PRINTER=H2D PYTHONPATH="$D:." .venv/bin/python3 - <<'EOF'
import os
import auth
from tunnel6000 import Tunnel, TunnelError
name = os.environ["PRINTER"]                    # an H2/P2S/X2D printer, never an A1/P1 (port 6000 is its camera)
c = auth.get_printer_credentials(name)
t = Tunnel(c["ip"], c["access_code"])
usb, internal = t.list_files(), t.list_files(storage="internal")
print("counts", len(usb), len(internal))
print("paging exact", t.list_files(start=2, count=2) == usb[2:4])
small = min((e for e in usb if e["size"] < 300000), key=lambda e: e["size"])
print("usb download", len(t.download(small["path"])), "bytes verified")
reply, body = t.call(2, {"paths": [small["path"] + "#Metadata/slice_info.config"]})
print("sub_file", reply["result"], len(body), "bytes", body[:5])
print("project_member", len(t.project_member("Metadata/slice_info.config")), "bytes")
try:
    t.download(internal[0]["path"])
    print("internal download SUCCEEDED: doc is stale")
except TunnelError as err:
    print("internal download refused:", err)
EOF
```

Expected: `paging exact True`, the USB download verified, `sub_file 0` with a body starting `<?xml`, a `project_member` byte count, and `internal download refused: FILE_DOWNLOAD result 2`. Observed 2026-09-19 on the operator's H2D (fw 01.03.00.00), output below. The internal count of 15 is 8 jobs under `/userdata/model/history/` plus 7 factory samples under `/userdata/model/bbl/`.

Stale signals, either direction: `internal download SUCCEEDED` means a firmware lifted the allowlist and the "When to use" table is wrong. `result 2` on a `/media/usb0` path, a failed paging check, or any exception means the client or the firmware protocol changed.

```text
counts 57 15
paging exact True
usb download 80770 bytes verified
sub_file 0 1522 bytes b'<?xml'
project_member 3638 bytes
internal download refused: FILE_DOWNLOAD result 2
```

## Known limits (2026-09-19, H2D fw 01.03.00.00)

Not established: what `dir_refresh_cnt` means (always 0); printer-initiated notification payloads; what `api_version` 3 adds; `zip:true`, other `mem:/N` indexes, and why the current project's gcode is refused; anything
about `TASK_CANCEL`, multi-fragment uploads, or a working internal delete on this firmware. Other models differ: X2D reportedly allows eMMC downloads.

## Keeping this page current

- **Update when:** the H2D/P2S/X2D firmware changes (re-run the verification above — a firmware that lifts the `/media/usb0` allowlist changes the "When to use" table); Bambu Studio's
  `src/slic3r/GUI/Printer/PrinterFileSystem.{cpp,h}` changes (new commands or result codes); a KB topic above is corrected.
- **Stale signals:** see the two directions listed under Verification.
- **Owner:** the agent working `bambu-mcp`; the KB topics are the evidence of record and are updated in the same change.
- **Source anchors:** Studio `bambulab/BambuStudio` @ `77b9dd94d1e3c432d5e74a18ab8de146ccf3b7c7`; ClusterM/open-bambu-networking `research/06.04-port-6000.md`.
