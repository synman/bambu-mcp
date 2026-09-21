"""Tests for the external spool holder handling in bambu-mcp.

A dual-nozzle printer has two external holders: telemetry names the LEFT one 254 and the
RIGHT one 255 (a single-nozzle printer has one, 254). get_external_spool used to look only
at 254, so a spool on the right holder read as "not loaded". /api/print_3mf returned a
bpm refusal (a ValueError from print_3mf_file, e.g. a dual-nozzle plate with no extruder
map) as a generic 500; it is a 400 because the request cannot be honored.

Nothing here touches a printer: session_manager and the printer are stubbed.

Run directly (no pytest needed):  .venv/bin/python3 test_external_spool_tools.py
"""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import api_server  # noqa: E402
from bpm.bambuspool import BambuSpool  # noqa: E402
from tools import filament as filament_mod  # noqa: E402


def _spool(tray_id, type_="", color=""):
    return BambuSpool(id=tray_id, slot_id=tray_id, ams_id=-1, type=type_, color=color)


def _with_spools(spools):
    class _Manager:
        def get_state(self, name):
            return types.SimpleNamespace(spools=spools)
    original = filament_mod.session_manager
    filament_mod.session_manager = _Manager()
    try:
        return filament_mod.get_external_spool("H2D")
    finally:
        filament_mod.session_manager = original


def test_right_holder_only_is_loaded():
    r = _with_spools([_spool(254), _spool(255, "TPU", "black")])
    assert r["loaded"] is True
    assert r["spool"]["type"] == "TPU", r
    assert sorted(s["slot_id"] for s in r["spools"]) == [254, 255], r


def test_left_holder_wins_when_both_hold_a_filament():
    r = _with_spools([_spool(255, "TPU", "black"), _spool(254, "ABS", "white")])
    assert r["spool"]["type"] == "ABS", r
    assert len(r["spools"]) == 2


def test_single_nozzle_holder_is_unchanged():
    r = _with_spools([_spool(254, "PLA", "white")])
    assert r["loaded"] is True and r["spool"]["type"] == "PLA" and len(r["spools"]) == 1


def test_no_external_tray_is_not_loaded():
    r = _with_spools([])
    assert r == {"loaded": False, "spool": None, "spools": []}, r


def test_ams_spools_are_not_external():
    ams = BambuSpool(id=1, slot_id=1, ams_id=0, type="PLA", color="red")
    r = _with_spools([ams])
    assert r["loaded"] is False, r


def _post_print_3mf(error):
    class _Printer:
        printer_state = types.SimpleNamespace(gcode_state="IDLE")

        def print_3mf_file(self, *a, **k):
            raise error

    app = api_server._build_app()
    original = api_server._get_printer
    api_server._get_printer = lambda args=None: (_Printer(), "H2D")
    try:
        resp = app.test_client().post("/api/print_3mf?filename=a.3mf&plate=AUTO&use_ams=false")
        return resp.status_code, resp.get_json()
    finally:
        api_server._get_printer = original


def test_bpm_refusal_is_a_400_with_its_message():
    code, body = _post_print_3mf(ValueError("plate has no extruder map"))
    assert code == 400, (code, body)
    assert body == {"status": "error", "reason": "plate has no extruder map"}, body


def test_other_failures_stay_a_500():
    code, body = _post_print_3mf(RuntimeError("mqtt down"))
    assert code == 500, (code, body)


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {exc!r}")
    sys.exit(1 if failed else 0)
