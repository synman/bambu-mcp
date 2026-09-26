"""Regression tests for two spool/dryer defects in bambu-mcp.

start_ams_dryer used to report an ACCEPTED command as an error. bpm's turn_on_ams_dryer
publishes the command and returns; the dryer state is only rewritten when the printer's next
telemetry frame carries the AMS ``info`` word, so for the first seconds it still reads the
value from BEFORE the command, whatever it was. The poll loop treated any OFF as "rejected" and
stopped at the first poll, and once that was tolerated a stale ERROR, COOLING or DRYING still
misreported the command. The tool now snapshots the dryer state just before publishing and decides
only on reads that differ from it; the tests cover a stale OFF, ERROR, COOLING and DRYING.

get_spool_info picked the active spool with ``s.ams_id == active_ams_id and s.slot_id ==
active_tray_id``. On a single-extruder printer bpm's active_tray_id is the raw ``tray_now``, an
ABSOLUTE tray id (unit n slot s -> 4n+s, the same number as the spool's ``id``), while slot_id is
only the 0-3 slot inside a unit, so an active tray on the second AMS unit or later never matched.
On a dual-extruder printer active_tray_id is the low byte of the extruder's own report, the SLOT
inside the active unit (the H2D debug log carries snow=32768, AMS HT slot 0, which bpm reads as
active_ams_id 128 with active_tray_id 0), and the old rule was already right there. So the fix
matches inside the active unit by slot or by id; matching the id on its own or first would name
AMS unit 0 slot 0 (id 0) as the H2D's active spool when the AMS HT is feeding. These tests use
real BambuState / BambuSpool / AMSUnitState objects and stub only session_manager and the
printer's publish. The dryer tests replace the clock and sleep, so nothing sleeps for real, and
the fake sleep is what advances the fake telemetry.

Nothing here touches a printer, the network or the daemon.

Run directly (no pytest needed):  .venv/bin/python3 test_spool_defects.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bpm.bambustate import AMSDryerState, AMSUnitState, BambuState  # noqa: E402
from bpm.bambuspool import BambuSpool  # noqa: E402
from bpm.bambutools import AMSHeatingState as H  # noqa: E402
from bpm.bambutools import AMSModel  # noqa: E402
from tools import filament as filament_mod  # noqa: E402
from tools import state as state_mod  # noqa: E402


# --------------------------------------------------------------------------- dryer

class _FakeClock:
    """Replaces time.time and time.sleep. Each sleep is one poll interval: it advances the
    clock and applies the telemetry scheduled for that poll number, the way a real frame
    would land while start_ams_dryer waits."""

    def __init__(self, unit, schedule):
        self.now = 1000.0
        self.polls = 0
        self._unit = unit
        self._schedule = schedule

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.polls += 1
        if self.polls in self._schedule:
            self._unit.dryer.state = self._schedule[self.polls]


class _FakePrinter:
    def __init__(self, on_publish=None):
        self.calls = []
        self._on_publish = on_publish

    def turn_on_ams_dryer(self, **kwargs):
        self.calls.append(kwargs)
        if self._on_publish is not None:
            self._on_publish()


def _run_dryer(schedule, before=H.OFF, on_publish=None, ams_units=None):
    """``before`` is the dryer state the unit holds when the command goes out (the value bpm
    keeps until the next AMS info frame arrives); ``schedule`` maps a poll number to the value
    a frame landing at that poll writes; ``on_publish`` runs inside the publish call."""
    # an AMS 2 Pro: start_ams_dryer refuses a unit without a dryer before publishing
    unit = AMSUnitState(ams_id=0, model=AMSModel.AMS_2_PRO, dryer=AMSDryerState(state=before))
    state = BambuState(ams_units=[unit] if ams_units is None else ams_units)
    printer = _FakePrinter(on_publish=(lambda: on_publish(unit)) if on_publish else None)
    clock = _FakeClock(unit, schedule)

    class _Manager:
        def get_printer(self, name):
            return printer

        def get_state(self, name):
            return state

    real = (time.time, time.sleep, filament_mod.session_manager)
    time.time, time.sleep, filament_mod.session_manager = clock.time, clock.sleep, _Manager()
    try:
        result = filament_mod.start_ams_dryer("H2D", 0, user_permission=True)
    finally:
        time.time, time.sleep, filament_mod.session_manager = real
    return result, clock, printer


def test_stale_off_before_telemetry_moves_is_tolerated():
    result, clock, printer = _run_dryer({4: H.CHECKING, 6: H.DRYING})
    assert result.startswith("AMS dryer started on unit 0"), result
    assert "heater_state=DRYING" in result, result
    assert clock.polls == 6, clock.polls
    assert len(printer.calls) == 1, printer.calls


def test_drying_on_the_first_poll_returns_at_once():
    result, clock, _ = _run_dryer({1: H.DRYING})
    assert result.startswith("AMS dryer started"), result
    assert clock.polls == 1, clock.polls


def test_a_real_error_state_is_reported_immediately():
    result, clock, _ = _run_dryer({1: H.CHECKING, 2: H.ERROR})
    assert result.startswith("Error: AMS dryer command sent"), result
    assert "final state: ERROR" in result, result
    assert clock.polls == 2, clock.polls


def test_off_after_checking_is_a_rejection_not_a_stale_read():
    result, clock, _ = _run_dryer({1: H.CHECKING, 2: H.CHECKING, 3: H.OFF})
    assert result.startswith("Error: AMS dryer command sent"), result
    assert "final state: OFF" in result, result
    assert clock.polls == 3, clock.polls


def test_no_telemetry_at_all_times_out_within_the_budget():
    result, clock, _ = _run_dryer({})
    assert result.startswith("Error: AMS dryer command sent"), result
    assert "did not reach DRYING within 10s" in result, result
    assert "final state: OFF" in result, result
    assert clock.polls == 10, clock.polls
    assert clock.now - 1000.0 <= 10.0, clock.now


def test_stale_error_before_telemetry_moves_does_not_misreport_an_accepted_command():
    # The unit still holds a previous run's ERROR when the command goes out; the first frames
    # after it repeat that value until the AMS info word arrives, and DRYING lands at poll 3.
    result, clock, _ = _run_dryer({3: H.DRYING}, before=H.ERROR)
    assert result.startswith("AMS dryer started on unit 0"), result
    assert clock.polls == 3, clock.polls


def test_stale_cooling_then_off_then_drying_is_an_accepted_command():
    # Pre-command COOLING (a finished run winding down); the first frame after the command reads
    # OFF at poll 2 and DRYING lands at poll 4. OFF here is the first post-command read, so it is
    # not a fall-back from a post-command state.
    result, clock, _ = _run_dryer({2: H.OFF, 4: H.DRYING}, before=H.COOLING)
    assert result.startswith("AMS dryer started on unit 0"), result
    assert clock.polls == 4, clock.polls


def test_stale_drying_is_not_taken_as_confirmation():
    # The unit was already DRYING when the command went out and never reports anything else, so
    # the reads cannot say whether the command was accepted. The answer must not claim a start,
    # and must not be an error either: drying is in progress whichever way the command went.
    result, clock, printer = _run_dryer({}, before=H.DRYING)
    assert not result.startswith("AMS dryer started"), result
    assert not result.startswith("Error"), result
    assert result.startswith("AMS dryer command sent"), result
    assert "already DRYING" in result, result
    assert clock.polls == 10, clock.polls
    assert len(printer.calls) == 1, printer.calls


def test_stale_drying_then_a_fresh_cycle_back_to_drying_is_confirmed():
    result, clock, _ = _run_dryer({3: H.CHECKING, 5: H.DRYING}, before=H.DRYING)
    assert result.startswith("AMS dryer started on unit 0"), result
    assert clock.polls == 5, clock.polls


def test_stale_drying_then_a_real_error_is_a_rejection():
    result, clock, _ = _run_dryer({2: H.ERROR}, before=H.DRYING)
    assert result.startswith("Error: AMS dryer command sent"), result
    assert "final state: ERROR" in result, result
    assert clock.polls == 2, clock.polls


def test_off_after_a_post_command_state_is_a_rejection_even_from_a_stale_cooling():
    result, clock, _ = _run_dryer({2: H.CHECKING, 3: H.OFF}, before=H.COOLING)
    assert result.startswith("Error: AMS dryer command sent"), result
    assert "final state: OFF" in result, result
    assert clock.polls == 3, clock.polls


def test_first_post_command_off_alone_is_waited_out_not_rejected():
    result, clock, _ = _run_dryer({2: H.OFF}, before=H.COOLING)
    assert result.startswith("Error: AMS dryer command sent"), result
    assert "did not reach DRYING within 10s" in result, result
    assert clock.polls == 10, clock.polls


def test_the_snapshot_is_taken_before_the_command_is_published():
    # A frame that lands while the publish call runs is post-command telemetry. Snapshotting
    # after the publish would read that DRYING as "already drying" and refuse to confirm it.
    result, clock, _ = _run_dryer({}, before=H.OFF,
                                  on_publish=lambda unit: setattr(unit.dryer, "state", H.DRYING))
    assert result.startswith("AMS dryer started on unit 0"), result
    assert clock.polls == 1, clock.polls


def test_unit_never_reported_is_unknown_after_the_budget():
    other = AMSUnitState(ams_id=128)
    unit = AMSUnitState(ams_id=0, model=AMSModel.AMS_2_PRO, dryer=AMSDryerState())
    state = BambuState(ams_units=[unit])
    clock2 = _FakeClock(unit, {})

    class _Manager:
        def get_printer(self, name):
            return _FakePrinter()

        def get_state(self, name):
            # the unit disappears once the command is out: the resolve step sees it, polls do not
            _Manager.calls += 1
            return state if _Manager.calls <= 2 else BambuState(ams_units=[other])
    _Manager.calls = 0

    real = (time.time, time.sleep, filament_mod.session_manager)
    time.time, time.sleep, filament_mod.session_manager = clock2.time, clock2.sleep, _Manager()
    try:
        result = filament_mod.start_ams_dryer("H2D", 0, user_permission=True)
    finally:
        time.time, time.sleep, filament_mod.session_manager = real
    assert "final state: unknown" in result, result
    assert clock2.polls == 10, clock2.polls


# --------------------------------------------------------------------------- active spool

def _spool(tray_id, slot_id, ams_id, type_="PLA"):
    return BambuSpool(id=tray_id, slot_id=slot_id, ams_id=ams_id, type=type_, color="#FF0000")


def _active(spools, active_ams_id, active_tray_id):
    state = BambuState(spools=spools, active_ams_id=active_ams_id, active_tray_id=active_tray_id)

    class _Manager:
        def get_state(self, name):
            return state

    real = state_mod.session_manager
    state_mod.session_manager = _Manager()
    try:
        return state_mod.get_spool_info("H2D")
    finally:
        state_mod.session_manager = real


def _four_units():
    return [_spool(4 * u + s, s, u) for u in range(3) for s in range(4)]


def test_active_tray_on_the_second_unit_is_found():
    r = _active(_four_units(), active_ams_id=1, active_tray_id=6)
    assert r["active_spool"] is not None, r["active_spool"]
    assert (r["active_spool"]["id"], r["active_spool"]["ams_id"], r["active_spool"]["slot_id"]) == (6, 1, 2)


def test_active_tray_on_the_third_unit_is_found():
    r = _active(_four_units(), active_ams_id=2, active_tray_id=11)
    assert r["active_spool"] is not None, r["active_spool"]
    assert r["active_spool"]["id"] == 11, r["active_spool"]


def test_first_unit_still_matches():
    r = _active(_four_units(), active_ams_id=0, active_tray_id=2)
    assert r["active_spool"]["id"] == 2, r["active_spool"]


def test_h2d_ams_ht_reads_unit_128_tray_0_and_is_not_confused_with_unit_0_slot_0():
    # Observed in the H2D debug log: snow=32768 (AMS HT, slot 0). bpm turns that into
    # active_ams_id 128 and active_tray_id 0 (slot & 0xFF). The AMS 2 Pro slot 0 spool has id 0,
    # so an id match on its own, or before the unit, returns the wrong spool.
    spools = _four_units()[:4] + [_spool(16, 0, 128, "ABS")]
    r = _active(spools, active_ams_id=128, active_tray_id=0)
    assert r["active_spool"] is not None, r["active_spool"]
    assert (r["active_spool"]["id"], r["active_spool"]["type"]) == (16, "ABS"), r["active_spool"]


def test_dual_extruder_slot_report_on_a_later_unit_still_matches():
    # Dual-extruder shape: unit id in active_ams_id, the slot alone in active_tray_id.
    r = _active(_four_units(), active_ams_id=1, active_tray_id=2)
    assert (r["active_spool"]["id"], r["active_spool"]["ams_id"]) == (6, 1), r["active_spool"]


def test_ams_ht_absolute_id_is_matched_but_unverified_on_hardware():
    # HYPOTHETICAL shape, never observed: the AMS HT reported by its absolute spool id 16 together
    # with its own unit id 128. It is matched inside the unit like any other id, and nothing in the
    # debug log confirms a printer ever reports it, so this pins the rule and not the hardware.
    spools = _four_units() + [_spool(16, 0, 128, "ABS")]
    r = _active(spools, active_ams_id=128, active_tray_id=16)
    assert r["active_spool"] is not None and r["active_spool"]["id"] == 16, r["active_spool"]


def test_an_id_in_another_unit_never_hijacks_the_unit_match():
    # The decoy sits in unit 0 and has id 4; the wanted spool is in unit 1 and holds slot 4. The
    # unit decides, so the id 4 in the wrong unit must not be chosen ahead of the unit-1 match.
    decoy = _spool(4, 3, 0)
    in_unit = _spool(99, 4, 1)
    r = _active([decoy, in_unit], active_ams_id=1, active_tray_id=4)
    assert r["active_spool"]["id"] == 99, r["active_spool"]


def test_no_active_tray_yields_none():
    assert _active(_four_units(), active_ams_id=-1, active_tray_id=-1)["active_spool"] is None


def test_no_active_tray_yields_none_even_with_an_empty_external_placeholder():
    # bpm appends BambuSpool(254) (slot_id -1, ams_id -1) for a holder that reported no tray. With
    # active_tray_id -1 and active_ams_id -1 its slot_id and ams_id both equal the active values,
    # so a bare predicate returns it although no tray is active.
    spools = _four_units() + [BambuSpool(254)]
    assert _active(spools, active_ams_id=-1, active_tray_id=-1)["active_spool"] is None
    # a stale unit id with no tray still selects nothing
    assert _active(spools, active_ams_id=0, active_tray_id=-1)["active_spool"] is None


def test_external_holder_handling_is_unchanged():
    ext = _spool(254, 254, -1, "TPU")
    r = _active(_four_units() + [ext], active_ams_id=-1, active_tray_id=254)
    assert r["active_spool"]["id"] == 254, r["active_spool"]
    # a stale unit id keeps the old outcome: no id match is attempted for a holder
    r = _active(_four_units() + [ext], active_ams_id=0, active_tray_id=254)
    assert r["active_spool"] is None, r["active_spool"]


def test_an_empty_external_placeholder_is_not_promoted_to_active_by_its_id():
    # bpm appends BambuSpool(254) (slot_id -1, ams_id -1) for a holder that reported no tray. The
    # slot rule never matched it and the id alternative must not start to.
    r = _active(_four_units() + [BambuSpool(254)], active_ams_id=-1, active_tray_id=254)
    assert r["active_spool"] is None, r["active_spool"]


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
