"""Tests for the stdio server ending with the session that spawned it (server.py main).

Why it exists: a stdio bambu-mcp whose session died kept running. EOF on stdin makes mcp.run()
return, main() returns, and the interpreter then joins every non-daemon thread BEFORE it runs
atexit handlers. With a printer configured those threads are bpm's MQTT loop and watchdog and the
REST API server, none of which ends on its own, so _shutdown (an atexit handler) never ran and
the process lived on. 11 such orphans each ran the job monitor against the H2D camera and held
the Mac at load 21 on 12 cores (2026-10-25).

Each test runs server.main() in a child process with stdin already closed, a non-daemon thread
standing in for the printer session threads, and `_startup` / `_shutdown` replaced, so no printer,
broker or camera is touched.

Run directly (no pytest needed):  .venv/bin/python3 test_stdio_exit.py
"""

import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

_CHILD = r"""
import pathlib, sys, threading
sys.path.insert(0, {root!r})
sys.argv = ["server.py", "--transport", "stdio"]
import server

marker = pathlib.Path({marker!r})
release = threading.Event()

def fake_shutdown():
    marker.write_text("shutdown")
    if {release!r}:
        release.set()

# A thread that is not a daemon, like bpm's "bambuprinter-session": it keeps the interpreter from
# finalizing until it returns. Started here, not inside _startup, because the real _startup runs on
# a daemon thread that stdin EOF can outrun in a test, though never against a real printer connect.
threading.Thread(target=release.wait, name="bambuprinter-session").start()

server._startup = lambda: None
server._shutdown = fake_shutdown
server._STDIO_EXIT_GRACE_S = {grace!r}
server.main()
"""


def _run_server_at_eof(release: bool, grace: float, timeout: float):
    """Run main() with stdin closed. Returns (exited, seconds, shutdown_ran)."""
    with tempfile.TemporaryDirectory() as tmp:
        marker = Path(tmp) / "shutdown"
        code = _CHILD.format(root=str(ROOT), marker=str(marker), release=release, grace=grace)
        t0 = time.monotonic()
        proc = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            proc.wait(timeout=timeout)
            exited = True
        except subprocess.TimeoutExpired:
            exited = False
            proc.kill()
            proc.wait()
        return exited, time.monotonic() - t0, marker.exists()


def test_stdio_eof_tears_down_and_the_process_exits():
    """The thread ends because teardown released it, long before the backstop would fire."""
    exited, secs, shutdown_ran = _run_server_at_eof(release=True, grace=60, timeout=40)
    assert exited, "process still alive 40 s after stdin closed (main() returned, threads joined)"
    assert shutdown_ran, "_shutdown did not run on stdin EOF"
    assert secs < 40, f"took {secs:.1f}s, so only the backstop could have ended it"


def test_stdio_eof_exits_even_when_a_session_thread_never_ends():
    """A thread that teardown cannot stop (bpm quit() blocks forever on a bad disconnect)."""
    exited, secs, shutdown_ran = _run_server_at_eof(release=False, grace=2, timeout=40)
    assert exited, "process still alive 40 s after stdin closed with a stuck session thread"
    assert shutdown_ran, "_shutdown did not run on stdin EOF"


if __name__ == "__main__":
    import traceback

    failed = 0
    for tname, fn in sorted(globals().items()):
        if tname.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {tname}")
            except Exception:
                failed += 1
                print(f"FAIL {tname}")
                traceback.print_exc()
    print(f"{failed} failed")
    sys.exit(1 if failed else 0)
