"""
Tests for camera/mse_relay.py: the vendored relay is the pinned commit's bytes, the child is started
with the access code in its environment and never in its argv, and stopping it frees its port.

The child is the real vendored relay, pointed at a printer address nothing answers (TEST-NET-1), so no
camera is contacted. Run directly (no pytest needed):  .venv/bin/python3 test_mse_relay.py
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from camera import mse_relay  # noqa: E402
from port_pool import port_pool  # noqa: E402

_CODE = "camdef-not-a-real-code"


def test_vendored_relay_is_the_pinned_commit():
    digest = hashlib.sha256(mse_relay.RELAY_SCRIPT.read_bytes()).hexdigest()
    assert digest == mse_relay.MSE_STREAM_SHA256, (
        f"camera/mse_relay_server.py changed: re-copy server.py from bambu-mse-stream at the new "
        f"commit and update MSE_STREAM_REF and MSE_STREAM_SHA256 together (hash now {digest})")
    assert len(mse_relay.MSE_STREAM_REF) == 40


def test_families_map_every_camera_protocol():
    assert mse_relay.family_for("rtsps") == "rtsps"
    assert mse_relay.family_for("tcp_tls") == "jpeg"


def test_relay_child_starts_serves_stats_and_stops_cleanly():
    relay = mse_relay.RelayProcess("camdef-relay", "192.0.2.1", _CODE, "jpeg")
    relay.start()
    port, pid = relay.port, relay._proc.pid
    try:
        assert mse_relay.for_host("192.0.2.1") is relay
        args = subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True).stdout
        assert "mse_relay_server.py" in args and _CODE not in args, args
        status, _ctype, body = relay.get("/stats")
        stats = json.loads(body)
        assert status == 200 and stats["name"] == "camdef-relay", (status, body[:200])
        assert _CODE not in body.decode(), "the relay leaked the access code in /stats"
        assert relay.snapshot() is None, "no camera, so no frame"
    finally:
        relay.close()
    assert not relay.alive and relay.port is None
    assert mse_relay.for_host("192.0.2.1") is None
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode == 0:
        time.sleep(0.1)
    assert subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode != 0, "child survived close()"
    assert port not in port_pool.get_state()["pool_claimed"], "port not released"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} passed")
