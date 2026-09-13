"""Tests for the uploads-dir traversal guard in api_server.py.

Covers the 2026-09-13 security finding: the three file routes joined
client-supplied names straight into `_UPLOADS` (`os.path.join(_UPLOADS,
f.filename)` and friends), giving arbitrary file write via
`/api/upload_file_to_host` and arbitrary local-file read (exfiltrated to the
printer SD card) via `/api/upload_file_to_printer`. The guard `_safe_upload_path`
strips directory components and containment-checks the resolved path while
preserving every legal filename character — '+', spaces, unicode round-trip
unchanged (this server, unlike bambu-printer-app, deliberately does not
sanitize names).

The source-scan tests assert the three exact pre-fix sink expressions (copied
verbatim from the pre-fix source at lines 1645/1667/1689) stay gone —
reverting any route's hunk turns them red.

Run directly (no pytest needed):  .venv/bin/python3 test_upload_path_guard.py
api_server.py is import-safe: flask/bpm are lazy imports and the server only
starts via start().
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

API_SERVER = Path(__file__).resolve().parent / "api_server.py"


def _guard():
    from api_server import _UPLOADS, _safe_upload_path
    return Path(_UPLOADS).resolve(), _safe_upload_path


# ── behavior ────────────────────────────────────────────────────────────────

def test_legal_special_char_names_preserved():
    root, guard = _guard()
    for name in ("Microfiber+Holder.gcode.3mf", "my file (v2).gcode.3mf", "名前.3mf"):
        got = guard(name)
        assert got is not None, f"{name!r} wrongly rejected"
        assert got.name == name, f"{name!r} was altered to {got.name!r}"
        assert got.parent == root, f"{name!r} escaped to {got}"


def test_traversal_is_neutralized_not_obeyed():
    root, guard = _guard()
    for hostile in ("../../evil.py", "/etc/passwd", "..\\..\\evil.py", "a/b/../c.3mf"):
        got = guard(hostile)
        assert got is not None, f"{hostile!r}: basename should be salvageable"
        assert got.parent == root, f"{hostile!r} escaped containment: {got}"
        assert ".." not in got.name and "/" not in got.name and "\\" not in got.name


def test_degenerate_names_rejected():
    _, guard = _guard()
    for bad in ("", ".", "..", "   ", "///", "x/.."):
        assert guard(bad) is None, f"{bad!r} should be rejected"


# ── source contract (mutation-visible: revert a route hunk → red) ───────────

def test_raw_join_sinks_are_gone():
    src = API_SERVER.read_text()
    for sink in (
        "os.path.join(_UPLOADS, f.filename)",   # upload_file_to_host (write)
        "os.path.join(_UPLOADS, src)",          # upload_file_to_printer (read)
        "os.path.join(_UPLOADS, filename)",     # download_file_from_printer (write)
    ):
        assert sink not in src, f"pre-fix traversal sink resurfaced: {sink}"
    assert src.count("_safe_upload_path(") >= 4, "guard not applied at all three routes"


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failures += 1
                print(f"FAIL {name}: {exc}")
            except Exception as exc:  # noqa: BLE001 - report, don't mask
                failures += 1
                print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    sys.exit(1 if failures else 0)
