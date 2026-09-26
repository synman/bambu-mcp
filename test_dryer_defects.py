"""The dryer start must refuse what the AMS cannot do, and the HTTP route must not report an
accepted start as an error.

Neither bpm nor the firmware checks the AMS model or the drying range, and bpm publishes before
it validates ams_id. So both the ``start_ams_dryer`` tool and ``POST /api/turn_on_ams_dryer``
refuse a unit without a dryer (AMS Lite, original AMS, unknown), a temperature outside 45-65°C
(AMS 2 Pro) or 45-85°C (AMS HT), and hours outside 1-999, before anything is published. There is
no 24 h cap: H2D firmware accepted 72 h and 999 h on 2026-09-26.

The route also used to stop polling at the first read of heater_state OFF. bpm rewrites
heater_state only when the next AMS info frame arrives, so right after the publish it still
reads the pre-command value, and an accepted command was answered with an error. The route now
uses the tool's snapshot-and-ignore-stale poll.

Real AMSUnitState / BambuState objects; only session_manager, the printer's publish and the
clock are stubbed. Nothing touches a printer, the network or the daemon.

Run directly (no pytest needed):  .venv/bin/python3 test_dryer_defects.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import api_server  # noqa: E402
import session_manager as _sm_mod  # noqa: E402
from bpm.bambustate import AMSUnitState, BambuState  # noqa: E402
from bpm.bambutools import AMSHeatingState as H  # noqa: E402
from bpm.bambutools import AMSModel  # noqa: E402
from tools import filament as filament_mod  # noqa: E402

_app = api_server._build_app()
assert _app is not None


class _Clock:
    """Each sleep is one poll; the telemetry scheduled for that poll lands on the unit."""

    def __init__(self, unit, schedule):
        self.now, self.polls, self._unit, self._schedule = 1000.0, 0, unit, schedule

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.polls += 1
        if self.polls in self._schedule:
            self._unit.heater_state = self._schedule[self.polls]


class _Printer:
    def __init__(self, state):
        self.printer_state = state
        self.calls = []

    def turn_on_ams_dryer(self, **kwargs):
        self.calls.append(kwargs)


class _Manager:
    def __init__(self, printer):
        self.printer = printer

    def get_printer(self, name):
        return self.printer

    def get_state(self, name):
        return self.printer.printer_state

    def list_connected(self):
        return ["H2D"]


def _setup(model, schedule=None, before=H.OFF):
    unit = AMSUnitState(ams_id=0, model=model, heater_state=before)
    printer = _Printer(BambuState(ams_units=[unit]))
    return unit, printer, _Clock(unit, schedule or {})


class _Patched:
    def __init__(self, printer, clock):
        self.printer, self.clock = printer, clock

    def __enter__(self):
        manager = _Manager(self.printer)
        self._saved = (time.time, time.sleep, filament_mod.session_manager, _sm_mod.session_manager.get_printer)
        time.time, time.sleep = self.clock.time, self.clock.sleep
        filament_mod.session_manager = manager
        _sm_mod.session_manager.get_printer = manager.get_printer
        return self

    def __exit__(self, *exc):
        time.time, time.sleep, filament_mod.session_manager, _sm_mod.session_manager.get_printer = self._saved


def _route(printer, clock, query):
    with _Patched(printer, clock):
        resp = _app.test_client().post(f"/api/turn_on_ams_dryer?printer=H2D&{query}")
    return resp.status_code, resp.get_json()


def _tool(printer, clock, **kwargs):
    with _Patched(printer, clock):
        return filament_mod.start_ams_dryer("H2D", 0, user_permission=True, **kwargs)


# ── the route no longer calls an accepted start an error ─────────────────────


def test_route_waits_out_a_stale_off_and_confirms_drying():
    _, printer, clock = _setup(AMSModel.AMS_2_PRO, {3: H.DRYING})
    status, body = _route(printer, clock, "ams_id=0&target_temp=55&duration_hours=8")
    assert status == 200 and body["heater_state"] == "DRYING", (status, body)
    assert clock.polls == 3 and len(printer.calls) == 1, (clock.polls, printer.calls)


def test_route_reports_a_real_error():
    _, printer, clock = _setup(AMSModel.AMS_HT, {1: H.CHECKING, 2: H.ERROR})
    status, body = _route(printer, clock, "ams_id=0&target_temp=80&duration_hours=8")
    assert status == 500 and "final: ERROR" in body["reason"], (status, body)


def test_route_allows_more_than_24_hours():
    _, printer, clock = _setup(AMSModel.AMS_2_PRO, {1: H.DRYING})
    status, body = _route(printer, clock, "ams_id=0&target_temp=45&duration_hours=72")
    assert status == 200 and printer.calls[0]["duration"] == 72, (status, body, printer.calls)


# ── refusals: nothing is published ───────────────────────────────────────────


def _route_refused(model, query, reason):
    _, printer, clock = _setup(model)
    status, body = _route(printer, clock, query)
    assert status == 400 and reason in body["reason"], (status, body)
    assert printer.calls == [] and clock.polls == 0, (printer.calls, clock.polls)


def test_route_refuses_units_without_a_dryer():
    for model in (AMSModel.AMS_LITE, AMSModel.AMS_1, AMSModel.UNKNOWN):
        _route_refused(model, "ams_id=0&target_temp=55&duration_hours=8", "has no dryer")


def test_route_refuses_out_of_range_values():
    _route_refused(AMSModel.AMS_2_PRO, "ams_id=0&target_temp=66&duration_hours=8", "outside 45-65")
    _route_refused(AMSModel.AMS_HT, "ams_id=0&target_temp=86&duration_hours=8", "outside 45-85")
    _route_refused(AMSModel.AMS_2_PRO, "ams_id=0&target_temp=44&duration_hours=8", "outside 45-65")
    _route_refused(AMSModel.AMS_2_PRO, "ams_id=0&target_temp=55&duration_hours=1000", "outside 1-999")
    _route_refused(AMSModel.AMS_2_PRO, "ams_id=0&target_temp=55&duration_hours=0", "outside 1-999")


def test_route_refuses_an_unknown_ams_id():
    _route_refused(AMSModel.AMS_2_PRO, "ams_id=7&target_temp=55&duration_hours=8", "no AMS unit")


def _tool_refused(model, reason, **kwargs):
    _, printer, clock = _setup(model)
    result = _tool(printer, clock, **kwargs)
    assert result.startswith("Error:") and reason in result and "Nothing was sent" in result, result
    assert printer.calls == [] and clock.polls == 0, (printer.calls, clock.polls)


def test_tool_refuses_units_without_a_dryer():
    for model in (AMSModel.AMS_LITE, AMSModel.AMS_1, AMSModel.UNKNOWN):
        _tool_refused(model, "has no dryer")


def test_tool_refuses_out_of_range_values():
    _tool_refused(AMSModel.AMS_2_PRO, "outside 45-65", target_temp=66)
    _tool_refused(AMSModel.AMS_HT, "outside 45-85", target_temp=86)
    _tool_refused(AMSModel.AMS_2_PRO, "outside 1-999", duration_hours=0)
    _tool_refused(AMSModel.AMS_2_PRO, "outside 1-999", duration_hours=1000)


def test_tool_accepts_the_edges_and_long_dries():
    for model, temp, hours in ((AMSModel.AMS_2_PRO, 65, 999), (AMSModel.AMS_HT, 85, 72), (AMSModel.AMS_HT, 45, 1)):
        _, printer, clock = _setup(model, {1: H.DRYING})
        result = _tool(printer, clock, target_temp=temp, duration_hours=hours)
        assert result.startswith("AMS dryer started"), (model, result)
        assert printer.calls[0]["target_temp"] == temp and printer.calls[0]["duration"] == hours


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
