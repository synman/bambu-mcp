"""The dryer start must refuse what the AMS cannot do, and the HTTP route must not report an
accepted start as an error.

Neither bpm nor the firmware checks the AMS model or the drying range, and bpm publishes before
it validates ams_id. So both the ``start_ams_dryer`` tool and ``POST /api/turn_on_ams_dryer``
refuse a unit without a dryer (AMS Lite, original AMS, unknown), a temperature outside 45-65°C
(AMS 2 Pro) or 45-85°C (AMS HT), and hours outside 1-999, before anything is published. There is
no 24 h cap: H2D firmware accepted 72 h and 999 h on 2026-09-26.

The route also used to stop polling at the first read of the dryer state OFF. bpm rewrites
it only when the next AMS info frame arrives, so right after the publish it still
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
from bpm.bambustate import AMSDryerState, AMSUnitState, BambuState  # noqa: E402
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
            step = self._schedule[self.polls]
            # a dict lands several dryer fields at once (a refused reply); otherwise a state
            for attr, value in (step.items() if isinstance(step, dict) else [("state", step)]):
                setattr(self._unit.dryer, attr, value)


class _Printer:
    def __init__(self, state):
        self.printer_state = state
        self.calls = []

    def turn_on_ams_dryer(self, **kwargs):
        self.calls.append(kwargs)

    def turn_off_ams_dryer(self, ams_id):
        # bpm 2.0 refuses a stop for a unit without a dryer before it publishes
        unit = next(u for u in self.printer_state.ams_units if u.ams_id == ams_id)
        if unit.dryer is None:
            raise ValueError(f"AMS unit {ams_id} ({unit.model.name}) has no dryer")
        self.calls.append({"stop": ams_id})


class _Manager:
    def __init__(self, printer):
        self.printer = printer

    def get_printer(self, name):
        return self.printer

    def get_state(self, name):
        return self.printer.printer_state

    def list_connected(self):
        return ["H2D"]


def _setup(model, schedule=None, before=H.OFF, dryer=None):
    """bpm 2.0 builds ``unit.dryer`` only for an AMS 2 Pro or AMS HT; ``dryer=False`` leaves it
    None on one of those (a unit bpm has not built a dryer for)."""
    has_dryer = model in (AMSModel.AMS_2_PRO, AMSModel.AMS_HT) if dryer is None else dryer
    unit = AMSUnitState(ams_id=0, model=model, dryer=AMSDryerState(state=before) if has_dryer else None)
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


# ── the printer's own refusals ──────────────────────────────────────────────
# Captured on an H2D 2026-09-26: filament fed past AMS A's outlet. Every AMS report carried
# dry_sf_reason [3], and a start was answered result=fail, err_code 83935307 (0500C04B).

OUTLET = "Filament in AMS outlet. The high drying temperature may cause AMS blockage, please unload first."
REFUSED_REPLY = {
    "fail_count": 1,
    "fail_code": "HMS_0500-C04B",
    "fail_message": "Filament in AMS outlet, the high drying temperature may cause AMS blockage. Drying cannot be started. Please unload the filament first.",
}


def test_route_refuses_while_the_ams_reports_a_reason():
    unit, printer, clock = _setup(AMSModel.AMS_2_PRO)
    unit.dryer.refusal_message = OUTLET
    status, body = _route(printer, clock, "ams_id=0&target_temp=55&duration_hours=8")
    assert status == 400 and "cannot start drying now: Filament in AMS outlet" in body["reason"], (status, body)
    assert printer.calls == [] and clock.polls == 0, (printer.calls, clock.polls)


def test_tool_refuses_while_the_ams_reports_a_reason():
    unit, printer, clock = _setup(AMSModel.AMS_2_PRO)
    unit.dryer.refusal_message = OUTLET
    result = _tool(printer, clock)
    assert result.startswith("Error:") and "Filament in AMS outlet" in result and "Nothing was sent" in result, result
    assert printer.calls == [] and clock.polls == 0, (printer.calls, clock.polls)


def test_route_returns_the_printers_refused_reply_at_once():
    _, printer, clock = _setup(AMSModel.AMS_2_PRO, {1: REFUSED_REPLY})
    status, body = _route(printer, clock, "ams_id=0&target_temp=55&duration_hours=8")
    assert status == 409 and "HMS_0500-C04B" in body["reason"] and "AMS outlet" in body["reason"], (status, body)
    assert clock.polls == 1, clock.polls


def test_tool_returns_the_printers_refused_reply_at_once():
    _, printer, clock = _setup(AMSModel.AMS_2_PRO, {1: REFUSED_REPLY})
    result = _tool(printer, clock)
    assert result.startswith("Error: 'H2D' refused the AMS dryer start") and "(HMS_0500-C04B)" in result, result
    assert clock.polls == 1, clock.polls


# ── refusals: nothing is published ───────────────────────────────────────────


def _route_refused(model, query, reason, dryer=None):
    _, printer, clock = _setup(model, dryer=dryer)
    status, body = _route(printer, clock, query)
    assert status == 400 and reason in body["reason"], (status, body)
    assert printer.calls == [] and clock.polls == 0, (printer.calls, clock.polls)


def test_route_refuses_units_without_a_dryer():
    for model in (AMSModel.AMS_LITE, AMSModel.AMS_1):
        _route_refused(model, "ams_id=0&target_temp=55&duration_hours=8", "has no dryer")


def test_route_reads_the_dryer_not_the_model():
    _route_refused(AMSModel.AMS_2_PRO, "ams_id=0&target_temp=55&duration_hours=8", "has no dryer", dryer=False)


def test_route_tells_a_unit_not_reported_yet_from_one_without_a_dryer():
    _route_refused(AMSModel.UNKNOWN, "ams_id=0&target_temp=55&duration_hours=8", "has not reported yet")


def test_route_refuses_out_of_range_values():
    _route_refused(AMSModel.AMS_2_PRO, "ams_id=0&target_temp=66&duration_hours=8", "outside 45-65")
    _route_refused(AMSModel.AMS_HT, "ams_id=0&target_temp=86&duration_hours=8", "outside 45-85")
    _route_refused(AMSModel.AMS_2_PRO, "ams_id=0&target_temp=44&duration_hours=8", "outside 45-65")
    _route_refused(AMSModel.AMS_2_PRO, "ams_id=0&target_temp=55&duration_hours=1000", "outside 1-999")
    _route_refused(AMSModel.AMS_2_PRO, "ams_id=0&target_temp=55&duration_hours=0", "outside 1-999")


def test_route_refuses_an_unknown_ams_id():
    _route_refused(AMSModel.AMS_2_PRO, "ams_id=7&target_temp=55&duration_hours=8", "no AMS unit")


def _tool_refused(model, reason, dryer=None, **kwargs):
    _, printer, clock = _setup(model, dryer=dryer)
    result = _tool(printer, clock, **kwargs)
    assert result.startswith("Error:") and reason in result and "Nothing was sent" in result, result
    assert printer.calls == [] and clock.polls == 0, (printer.calls, clock.polls)


def test_tool_refuses_units_without_a_dryer():
    for model in (AMSModel.AMS_LITE, AMSModel.AMS_1):
        _tool_refused(model, "has no dryer")


def test_tool_reads_the_dryer_not_the_model():
    _tool_refused(AMSModel.AMS_HT, "has no dryer", dryer=False)


def test_tool_tells_a_unit_not_reported_yet_from_one_without_a_dryer():
    _tool_refused(AMSModel.UNKNOWN, "has not reported yet")


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


# ── stop: bpm's own refusal is a refusal, not a server error ─────────────────


def test_stop_route_answers_bpms_refusal_with_400():
    _, printer, clock = _setup(AMSModel.AMS_LITE)
    with _Patched(printer, clock):
        resp = _app.test_client().post("/api/turn_off_ams_dryer?printer=H2D&ams_id=0")
    assert resp.status_code == 400 and "has no dryer" in resp.get_json()["reason"], (resp.status_code, resp.get_json())
    assert printer.calls == [], printer.calls


def test_stop_tool_says_nothing_was_sent_on_bpms_refusal():
    _, printer, clock = _setup(AMSModel.AMS_LITE)
    with _Patched(printer, clock):
        result = filament_mod.stop_ams_dryer("H2D", 0, user_permission=True)
    assert result.startswith("Error:") and "has no dryer" in result and "Nothing was sent" in result, result


def test_a_unit_rebuilt_without_its_dryer_mid_poll_is_waited_out():
    unit, printer, clock = _setup(AMSModel.AMS_2_PRO, {2: {}})
    rebuilt = AMSUnitState(ams_id=0, model=AMSModel.AMS_2_PRO)

    def swap(seconds, _sleep=clock.sleep):
        _sleep(seconds)
        if clock.polls == 2:
            printer.printer_state = BambuState(ams_units=[rebuilt])
    clock.sleep = swap
    result = _tool(printer, clock)
    assert "did not reach DRYING within 10s" in result, result


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"{len(tests)} passed")
