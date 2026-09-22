"""Regression tests for the alert detector (notifications.py) and get_pending_alerts.

Why it exists: two defects in the alert detector made three alert families silently wrong.

1. Stage alerts never fired. The detector read `getattr(state, "stg_cur", None)` off the
   BambuState, which has no such attribute: bpm keeps the stage on the job record
   (ActiveJobInfo.stage_id, written in BambuPrinter._on_message from the raw stg_cur).
   So `stage_change` was never emitted and every `job_paused` carried stage_id None and
   stage_name "unknown". The detector's own stage-name table was also numbered differently
   from bpm's parseStage (codes 5-21 off by one), so a fix that only re-pointed the read
   would have named a "Paused by user" pause as a filament-runout pause.
2. HMS alert text was blank. The detector read e.get("description") or e.get("desc"), but
   every entry bpm produces (decodeHMS and decodeError) carries the text under "msg".
3. HMS alerts stormed. The detector alerted on every entry in the raw BambuState.hms_errors,
   but bpm's raw list carries no active/historical distinction (decodeHMS labels an entry
   "Fatal" from the mask byte alone): "Historical" is applied at read time by the server
   (tools/state.py _apply_hms_historical), never seen by the detector. With print_error 0 the
   telemetry re-sends and drops a stale entry (HMS_0C00-0100-0002-001B on the H2D, 2026-09-21)
   about once a second, and each flap queued an hms_error_new / hms_error_cleared pair.

5. A false `job_started` after every daemon restart. bpm's BambuPrinter.service_state setter
   notifies on EVERY connection change, before any telemetry frame has arrived, so the detector
   was fed the never-populated default BambuState (gcode_state "IDLE"), recorded it as the
   baseline, and read the first real RUNNING frame as an IDLE to RUNNING job start (seen live
   on the H2D, 2026-09-21, restart during a running print). bpm's `recent_update` is NOT a
   "frame arrived" signal: it is set only by the get_version reply, which arrives BEFORE the
   first status frame, so a gate on it evaluates that reply's default IDLE too. The gate is
   the state's own marker: `wifi_signal_strength` is written only from a print status frame.
6. The health alert's `score` was always null: the detector read `composite_score`, a key the
   job monitor never stores (it stores `anomaly_score`).

The tests drive the REAL SessionManager accessors (get_state / get_job) with a fake printer
holding a REAL BambuState and a REAL ActiveJobInfo, and build HMS entries with the real
bpm decoders. Only the printer I/O, job_project persistence and the camera monitor are
stubbed. Nothing touches a printer, the network or the daemon.

Run directly (no pytest needed):  .venv/bin/python3 test_alerts_defects.py
"""

import inspect
import json
import sys
import tempfile
import types
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import notifications  # noqa: E402
import session_manager as _sm_module  # noqa: E402
from bpm.bambuconfig import BambuConfig  # noqa: E402
from bpm.bambuprinter import BambuPrinter, ServiceState  # noqa: E402
from bpm.bambuproject import ActiveJobInfo  # noqa: E402
from bpm.bambustate import BambuState  # noqa: E402
from bpm.bambutools import HMS_STATUS, decodeError, decodeHMS, parseStage  # noqa: E402

_NAME = "unit-test-printer"


class _FakePrinter:
    """The two attributes SessionManager.get_state / get_job read off a BambuPrinter."""

    def __init__(self):
        # A state that has come from a telemetry frame: bpm sets wifi_signal_strength from every
        # push_status, and the detector does not evaluate a state without it (defect 5).
        self.printer_state = BambuState(wifi_signal_strength="-43dBm")
        self.active_job_info = ActiveJobInfo()


@contextmanager
def _rig(printer=None, real_camera=False):
    """A fresh NotificationManager fed by the real SessionManager singleton.

    Stubs only what would touch disk or heavy deps: job_project (persistence under
    ~/.bambu-mcp) and camera (av and numpy, imported by the health check; `real_camera=True`
    leaves the real package in place). The clock is a mutable cell so debounce windows can be
    crossed without sleeping. Pass a real BambuPrinter to drive the detector through bpm's own
    on_update callback instead of the fake's step().
    """
    printer = printer or _FakePrinter()
    clock = [1000.0]
    saved = {k: sys.modules.get(k) for k in ("job_project", "camera")}
    sys.modules["job_project"] = types.SimpleNamespace(
        remember=lambda *a, **k: False, forget=lambda *a, **k: None)
    if not real_camera:
        sys.modules["camera"] = types.SimpleNamespace(
            job_monitor=types.SimpleNamespace(get_latest_result=lambda n: None))
    real_time = notifications.time
    notifications.time = types.SimpleNamespace(monotonic=lambda: clock[0])
    _sm_module.session_manager._printers[_NAME] = printer
    mgr = notifications.NotificationManager()
    if isinstance(printer, BambuPrinter):
        # what SessionManager wires (printer.on_update -> on_update -> check_and_emit), aimed at
        # this rig's manager instead of the process singleton
        printer.on_update = lambda _p: mgr.check_and_emit(_NAME)

    def step(gcode_state=None, stage_id=None, hms=None, dt=1.0):
        """Apply one printer update the way bpm does, then run the detector."""
        clock[0] += dt
        if gcode_state is not None:
            printer.printer_state.gcode_state = gcode_state
        if stage_id is not None:
            printer.active_job_info.stage_id = stage_id
            printer.active_job_info.stage_name = parseStage(stage_id)
        if hms is not None:
            printer.printer_state.hms_errors = hms
        mgr.check_and_emit(_NAME)

    def alerts(kind=None):
        found = list(mgr._get_state(_NAME).alerts)
        return [a for a in found if kind is None or a["type"] == kind]

    alerts.mgr = mgr
    try:
        yield printer, step, alerts
    finally:
        _sm_module.session_manager._printers.pop(_NAME, None)
        notifications.time = real_time
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


# --- defect 1: stage alerts ------------------------------------------------

def test_stage_change_fires_from_job_stage_id():
    with _rig() as (_, step, alerts):
        step(stage_id=0)
        step(stage_id=1)
        got = alerts("stage_change")
        assert len(got) == 1, alerts()
        assert got[0]["payload"] == {
            "stage_id": 1, "stage_name": "Auto bed leveling",
            "prev_stage_id": 0, "prev_stage_name": "unknown",
        }, got[0]["payload"]
        assert got[0]["severity"] == "medium", got[0]


def test_stage_names_follow_bpm_numbering():
    """bpm's parseStage is the producer's numbering: 6 is a runout pause, 16 a user pause."""
    with _rig() as (_, step, alerts):
        step(stage_id=0)
        step(stage_id=6)
        step(stage_id=16)
        names = {a["payload"]["stage_id"]: a["payload"]["stage_name"]
                 for a in alerts("stage_change")}
        assert names == {6: "Filament runout pause", 16: "Paused by user"}, (names, alerts())


def test_stages_beyond_the_old_22_code_table_alert():
    """bpm defines stages up to 77; the old table stopped at 21 and silenced the rest."""
    with _rig() as (_, step, alerts):
        step(stage_id=0)
        step(stage_id=35)
        got = alerts("stage_change")
        assert [a["payload"]["stage_name"] for a in got] == ["Nozzle clog pause"], alerts()


def test_quiet_stages_do_not_alert():
    with _rig() as (_, step, alerts):
        for sid in (-1, 0, 100, 255, 0):
            step(stage_id=sid)
        assert not alerts("stage_change"), alerts()


def test_first_observation_does_not_alert():
    with _rig() as (_, step, alerts):
        step(stage_id=8)
        assert not alerts(), alerts()


def test_same_stage_is_not_a_change_however_long_it_lasts():
    """A long stage (heating) must not re-alert as 'stage 8 -> stage 8' every window."""
    with _rig() as (_, step, alerts):
        step(stage_id=0)
        step(stage_id=8)
        for _ in range(20):
            step(dt=15.0)  # 300 s of updates in the same stage, well past the debounce
        got = alerts("stage_change")
        assert len(got) == 1, [(a["payload"]["stage_id"], a["payload"]["prev_stage_id"])
                               for a in got]


def test_stage_flapping_is_debounced_per_stage():
    with _rig() as (_, step, alerts):
        step(stage_id=0)
        step(stage_id=1)                   # alert: stage 1
        step(stage_id=2)                   # alert: stage 2
        step(stage_id=1)                   # stage 1 alerted 2 s ago: suppressed
        step(stage_id=2)                   # stage 2 alerted 2 s ago: suppressed
        assert [a["payload"]["stage_id"] for a in alerts("stage_change")] == [1, 2], alerts()
        step(stage_id=1, dt=100.0)         # window expired: real transition alerts again
        assert [a["payload"]["stage_id"] for a in alerts("stage_change")] == [1, 2, 1], alerts()
        assert alerts("stage_change")[-1]["payload"]["prev_stage_id"] == 2


def test_job_paused_carries_the_real_stage():
    with _rig() as (_, step, alerts):
        step(gcode_state="IDLE", stage_id=0)
        step(gcode_state="RUNNING", stage_id=255)
        step(gcode_state="PAUSE", stage_id=16)
        got = alerts("job_paused")
        assert len(got) == 1, alerts()
        assert got[0]["payload"]["stage_id"] == 16, got[0]["payload"]
        assert got[0]["payload"]["stage_name"] == "Paused by user", got[0]["payload"]
        assert got[0]["severity"] == "medium", got[0]


def test_job_paused_stage_unknown_only_when_no_stage_is_known():
    with _rig() as (printer, step, alerts):
        printer.active_job_info = None  # SessionManager.get_job -> None
        step(gcode_state="IDLE")
        step(gcode_state="RUNNING")
        step(gcode_state="PAUSE")
        got = alerts("job_paused")
        assert len(got) == 1, alerts()
        assert got[0]["payload"]["stage_id"] is None, got[0]["payload"]
        assert got[0]["payload"]["stage_name"] == "unknown", got[0]["payload"]
        assert not alerts("stage_change"), alerts()


# --- defect 3: HMS text ----------------------------------------------------

def _real_hms_entry():
    """One entry produced by bpm's decodeHMS for a code that has catalogue text."""
    row = next(r for r in HMS_STATUS["data"]["device_hms"]["en"] if r.get("intro"))
    ecode = row["ecode"]
    (entry,) = decodeHMS([{"attr": int(ecode[:8], 16), "code": int(ecode[8:], 16)}])
    return entry, row["intro"]


# An ACTIVE fault is a device_error plus the first device_hms (tools/state.py). The lone
# device_hms entries the printer keeps re-sending are Historical, so the tests that need an
# alert build the active context with a real decodeError entry at the head.
_ACTIVE_ERROR = decodeError(0x07038012)
_BLANK_CODE = "HMS_0C00-0100-0002-001B"


def _blank_entry():
    """The real entry from the live storm: catalogued with EMPTY text, raw severity Fatal."""
    (entry,) = decodeHMS([{"attr": 0x0C000100, "code": 0x0002001B}])
    return entry


def _codes(alert):
    return [e["code"] for e in alert["payload"]["errors"]]


def test_hms_error_new_carries_the_msg_text():
    entry, intro = _real_hms_entry()
    assert entry["msg"] == intro and "description" not in entry, entry  # producer's shape
    with _rig() as (_, step, alerts):
        step(hms=[])
        step(hms=[_ACTIVE_ERROR, entry])
        got = alerts("hms_error_new")
        assert len(got) == 1, alerts()
        assert got[0]["payload"]["errors"] == [
            {"code": _ACTIVE_ERROR["code"], "description": _ACTIVE_ERROR["msg"]},
            {"code": entry["code"], "description": intro},
        ], got[0]["payload"]


def test_hms_error_new_never_carries_a_blank_description():
    """bpm's catalogue lists HMS_0C00-0100-0002-001B with EMPTY text (seen live on the H2D on
    2026-09-21), so its msg is "" and the alert used to say nothing about the fault."""
    entry = _blank_entry()
    assert entry["code"] == _BLANK_CODE and entry["msg"] == "", entry  # producer
    with _rig() as (_, step, alerts):
        step(hms=[])
        step(hms=[_ACTIVE_ERROR, entry])  # active: the first device_hms beside a device_error
        errors = {e["code"]: e for e in alerts("hms_error_new")[0]["payload"]["errors"]}
        assert set(errors) == {_ACTIVE_ERROR["code"], _BLANK_CODE}, errors
        assert errors[_BLANK_CODE]["description"].strip(), errors
        assert errors[_BLANK_CODE]["description"] == "No description in the HMS catalogue", errors


def test_hms_error_new_carries_decoded_print_error_text():
    """decodeError entries (inserted at the head of hms_errors) carry msg too."""
    entry = decodeError(0x07038012)
    assert entry["msg"].startswith("Failed to get AMS mapping table"), entry
    with _rig() as (_, step, alerts):
        step(hms=[])
        step(hms=[entry])
        errors = alerts("hms_error_new")[0]["payload"]["errors"]
        assert errors == [{"code": entry["code"], "description": entry["msg"]}], errors


def test_hms_alert_only_lists_newly_appeared_codes_with_their_own_text():
    first, first_text = _real_hms_entry()
    second = _ACTIVE_ERROR
    with _rig() as (_, step, alerts):
        step(hms=[])
        step(hms=[second])               # a device_error alone is active
        step(hms=[second, first])        # the first device_hms beside it becomes active
        got = alerts("hms_error_new")
        assert len(got) == 2, alerts()
        assert got[0]["payload"]["errors"] == [
            {"code": second["code"], "description": second["msg"]}], got[0]["payload"]
        assert got[1]["payload"]["errors"] == [
            {"code": first["code"], "description": first_text}], got[1]["payload"]


# --- defect 4: HMS flapping ------------------------------------------------

def test_a_historical_entry_flapping_in_and_out_raises_no_alerts():
    """print_error is 0, so the lone device_hms entry is Historical however often the
    telemetry re-sends it. The raw bpm entry is NOT labelled Historical (it reads Fatal), so
    the detector has to apply the server's rule itself."""
    entry = _blank_entry()
    assert entry["severity"] == "Fatal" and entry["is_critical"] is True, entry  # producer
    with _rig() as (_, step, alerts):
        step(hms=[])
        for _ in range(10):
            step(hms=[entry])
            step(hms=[])
        assert alerts() == [], alerts()


def test_a_historical_entry_appearing_alone_and_clearing_alerts_nothing():
    entry = _blank_entry()
    with _rig() as (_, step, alerts):
        step(hms=[])
        step(hms=[entry])
        step(hms=[])
        assert not alerts("hms_error_new"), alerts()
        assert not alerts("hms_error_cleared"), alerts()


def test_an_active_entry_alerts_once_and_its_clear_follows_it():
    entry = _blank_entry()
    with _rig() as (_, step, alerts):
        step(hms=[])
        step(hms=[_ACTIVE_ERROR, entry])
        step(hms=[_ACTIVE_ERROR, entry])  # still active: not a new alert
        assert [_codes(a) for a in alerts("hms_error_new")] == [
            [_ACTIVE_ERROR["code"], _BLANK_CODE]], alerts()
        assert not alerts("hms_error_cleared"), alerts()
        step(hms=[])
        (cleared,) = alerts("hms_error_cleared")
        assert cleared["payload"] == {"prev_error_count": 2}, cleared
        assert len(alerts()) == 2, alerts()


def test_a_held_active_fault_is_not_a_change_however_long_it_lasts():
    """The debounce gates a TRANSITION, not presence: a fault held across many windows must
    not re-alert every 30 s (the HMS analogue of the same-stage test above)."""
    entry = _blank_entry()
    with _rig() as (_, step, alerts):
        step(hms=[])
        step(hms=[_ACTIVE_ERROR, entry])
        for _ in range(20):
            step(hms=[_ACTIVE_ERROR, entry], dt=15.0)  # 300 s held, well past the debounce
        assert len(alerts("hms_error_new")) == 1, alerts()
        assert not alerts("hms_error_cleared"), alerts()


def test_the_same_active_code_flapping_within_30s_alerts_once():
    entry = _blank_entry()
    with _rig() as (_, step, alerts):
        step(hms=[])
        step(hms=[_ACTIVE_ERROR, entry])   # alert
        for _ in range(10):
            step(hms=[])                   # 1 s later: flaps out ...
            step(hms=[_ACTIVE_ERROR, entry])  # ... and back in, well inside 30 s
        assert len(alerts("hms_error_new")) == 1, alerts()
        # the one clear that followed the raised alert; the later clears followed nothing
        assert len(alerts("hms_error_cleared")) == 1, alerts()


def test_active_flapping_after_30s_alerts_again():
    entry = _blank_entry()
    with _rig() as (_, step, alerts):
        step(hms=[])
        step(hms=[_ACTIVE_ERROR, entry])           # alert at t
        step(hms=[])                               # t+1
        step(hms=[_ACTIVE_ERROR, entry], dt=28.0)  # t+29: inside the window, suppressed
        assert len(alerts("hms_error_new")) == 1, alerts()
        step(hms=[])                               # t+30
        step(hms=[_ACTIVE_ERROR, entry], dt=1.0)   # t+31: window expired, alerts again
        got = alerts("hms_error_new")
        assert [_codes(a) for a in got] == [[_ACTIVE_ERROR["code"], _BLANK_CODE]] * 2, alerts()


def test_cleared_is_only_emitted_for_an_alert_that_was_raised():
    """An active code whose re-alert was suppressed never had an alert raised for THIS
    appearance, so its clear is silent; a clear that follows a raised alert is not."""
    entry = _blank_entry()
    with _rig() as (_, step, alerts):
        step(hms=[])
        step(hms=[_ACTIVE_ERROR, entry])   # alert raised
        step(hms=[])                       # cleared, follows it
        step(hms=[_ACTIVE_ERROR, entry])   # suppressed re-alert
        step(hms=[])                       # nothing raised for this appearance
        assert [a["type"] for a in alerts()] == ["hms_error_new", "hms_error_cleared"], alerts()


def test_historical_entries_beside_the_active_one_are_not_alerted():
    """With a device_error present only the FIRST device_hms is active; a later one is
    Historical (tools/state.py), so it neither alerts nor keeps the error 'active'."""
    first, _ = _real_hms_entry()
    stale = _blank_entry()
    assert first["code"] != stale["code"]
    with _rig() as (_, step, alerts):
        step(hms=[])
        step(hms=[_ACTIVE_ERROR, first, stale])
        (new,) = alerts("hms_error_new")
        assert _codes(new) == [_ACTIVE_ERROR["code"], first["code"]], new
        step(hms=[_ACTIVE_ERROR, first])       # the historical one dropping out is silent
        step(hms=[])
        assert len(alerts("hms_error_cleared")) == 1, alerts()


# --- defect 5: a state no telemetry frame has populated is not evaluated ---

def _status(gcode_state):
    """A push_status frame as the printer sends it: the keys bpm reads, real value shapes."""
    return {"print": {
        "command": "push_status", "gcode_state": gcode_state, "wifi_signal": "-43dBm",
        "bed_temper": 60.0, "nozzle_temper": 220.0, "print_error": 0, "mc_percent": 12,
    }}


# The get_version reply (a real one, from an H2D: `info.module`, no `print`) that the printer
# sends BEFORE its first status frame. bpm builds a state from it with gcode_state defaulted.
_VERSION_REPLY = {"info": {"command": "get_version", "module": [
    {"name": "ota", "sw_ver": "01.01.00.00"},
    {"name": "ams/0", "sn": "19C06A5A1100000"},
]}}


@contextmanager
def _real_rig(**kw):
    """A real, never-connected BambuPrinter (no network: start_session is never called, its
    frames are fed through the real _on_message) wired to a fresh detector. The bpm cache is a
    temp dir, and the 3mf fetch a RUNNING frame would start over FTPS is marked already tried."""
    with tempfile.TemporaryDirectory(prefix="alertdef-bpm-") as tmp:
        printer = BambuPrinter(BambuConfig("printer.invalid", "code", "SERIAL",
                                           bpm_cache_path=Path(tmp)))
        printer.active_job_info.project_info_fetch_attempted = True
        with _rig(printer=printer, **kw) as (_, step, alerts):
            yield printer, alerts


def _feed(printer, frame):
    printer._on_message(json.dumps(frame))


def test_a_restart_during_a_print_raises_no_job_started():
    """The connect notification carries the default IDLE state, then the first status frame
    says RUNNING: that is a restart, not a job start."""
    with _real_rig() as (printer, alerts):
        printer.service_state = ServiceState.CONNECTED  # the setter notifies, before any frame
        _feed(printer, _status("RUNNING"))
        assert not alerts("job_started"), alerts()


def test_the_version_reply_ahead_of_the_first_frame_raises_no_job_started():
    """The ordering a gate on bpm's recent_update misses: the get_version reply sets that flag
    and notifies with the still-default state, and only then does the RUNNING frame arrive."""
    with _real_rig() as (printer, alerts):
        printer.service_state = ServiceState.CONNECTED
        _feed(printer, _VERSION_REPLY)
        assert printer.recent_update is True, "precondition: the reply set bpm's recent_update"
        assert printer.printer_state.gcode_state == "IDLE"  # ... yet no status frame has landed
        _feed(printer, _status("RUNNING"))
        assert not alerts("job_started"), alerts()


def test_the_first_frame_ahead_of_the_version_reply_raises_no_job_started():
    with _real_rig() as (printer, alerts):
        printer.service_state = ServiceState.CONNECTED
        _feed(printer, _status("RUNNING"))
        _feed(printer, _VERSION_REPLY)
        assert not alerts("job_started"), alerts()


def test_the_running_baseline_taken_after_a_restart_makes_the_finish_alert():
    with _real_rig() as (printer, alerts):
        printer.service_state = ServiceState.CONNECTED
        _feed(printer, _VERSION_REPLY)
        _feed(printer, _status("RUNNING"))
        _feed(printer, _status("FINISH"))
        assert [a["type"] for a in alerts()] == ["job_finished"], alerts()


def test_a_normal_stream_is_evaluated_as_before():
    """Frames that show the job start (IDLE, then RUNNING) still alert, exactly once."""
    with _real_rig() as (printer, alerts):
        printer.service_state = ServiceState.CONNECTED
        _feed(printer, _VERSION_REPLY)
        _feed(printer, _status("IDLE"))
        _feed(printer, _status("RUNNING"))
        _feed(printer, _status("RUNNING"))
        assert [a["type"] for a in alerts()] == ["job_started"], alerts()


def test_frames_are_still_evaluated_after_the_watchdog_clears_recent_update():
    """bpm's watchdog resets recent_update on a stall (bambuprinter.py, watchdog_thread) while
    status frames keep arriving; the detector must keep alerting on them."""
    with _real_rig() as (printer, alerts):
        printer.service_state = ServiceState.CONNECTED
        _feed(printer, _VERSION_REPLY)
        _feed(printer, _status("IDLE"))
        _feed(printer, _status("RUNNING"))
        printer._recent_update = False  # what the watchdog does
        assert printer.recent_update is False
        _feed(printer, _status("FINISH"))
        assert [a["type"] for a in alerts()] == ["job_started", "job_finished"], alerts()


def test_a_replaced_printer_session_mid_print_raises_no_job_started():
    """The alert store outlives a printer session (add_printer / a credentials update starts a
    new BambuPrinter under the same name), so the new session's default state must not read as
    a fall to IDLE followed by a job start."""
    with _real_rig() as (old, alerts):
        old.service_state = ServiceState.CONNECTED
        _feed(old, _status("RUNNING"))
        with tempfile.TemporaryDirectory(prefix="alertdef-bpm2-") as tmp:
            new = BambuPrinter(BambuConfig("printer.invalid", "code", "SERIAL",
                                           bpm_cache_path=Path(tmp)))
            new.active_job_info.project_info_fetch_attempted = True
            new.on_update = lambda _p: alerts.mgr.check_and_emit(_NAME)
            _sm_module.session_manager._printers[_NAME] = new
            new.service_state = ServiceState.CONNECTED
            _feed(new, _version_reply_copy())
            _feed(new, _status("RUNNING"))
        assert not alerts("job_started"), alerts()


def _version_reply_copy():
    return json.loads(json.dumps(_VERSION_REPLY))


def test_the_gate_never_raises_in_the_update_path():
    """No printer registered, and a state whose marker cannot even be read: nothing raises and
    nothing is queued."""
    class _Unreadable:
        @property
        def wifi_signal_strength(self):
            raise RuntimeError("unreadable")

        gcode_state = "RUNNING"

    with _rig() as (printer, step, alerts):
        step(gcode_state="IDLE")
        printer.printer_state = _Unreadable()
        step()
        _sm_module.session_manager._printers.pop(_NAME, None)
        step()
        assert alerts() == [], alerts()


# --- defect 6: the health alert's score --------------------------------------

def _monitor_result(verdict, anomaly_score):
    """The shape camera/job_monitor.py stores for an analysed frame (its result dict)."""
    return {
        "verdict": verdict, "anomaly_score": anomaly_score, "stage_gated": False,
        "stable_verdict": verdict, "success_probability": 0.91, "decision_confidence": 0.5,
    }


@contextmanager
def _real_monitor():
    """The real camera.job_monitor with a real _PrinterMonitor registered for the printer, its
    persist dir a temp dir. Yields a setter that stores a result the way the monitor does."""
    from camera import job_monitor
    with tempfile.TemporaryDirectory(prefix="alertdef-jm-") as tmp:
        saved_dir = job_monitor._PERSIST_DIR
        job_monitor._PERSIST_DIR = Path(tmp)
        mon = job_monitor._PrinterMonitor(_NAME)
        job_monitor._monitors[_NAME] = mon
        try:
            def store(result):
                with mon._lock:
                    mon._latest_result = result
            yield store
        finally:
            job_monitor._monitors.pop(_NAME, None)
            job_monitor._PERSIST_DIR = saved_dir


def test_health_escalated_carries_the_monitors_anomaly_score():
    with _rig(real_camera=True) as (_, step, alerts), _real_monitor() as store:
        store(_monitor_result("clean", 0.0512))
        step()                                    # first verdict seen: no alert
        store(_monitor_result("warning", 0.4321))
        step(dt=100.0)
        (got,) = alerts("health_escalated")
        assert got["payload"] == {
            "from_verdict": "clean", "to_verdict": "warning", "score": 0.4321}, got["payload"]


def test_health_recovered_carries_the_monitors_anomaly_score():
    with _rig(real_camera=True) as (_, step, alerts), _real_monitor() as store:
        store(_monitor_result("critical", 0.9))
        step()
        store(_monitor_result("clean", 0.0731))
        step(dt=100.0)
        (got,) = alerts("health_recovered")
        assert got["payload"]["score"] == 0.0731, got["payload"]


def test_the_seeded_result_has_the_shape_the_monitor_stores():
    """The two tests above seed a result; this keeps that seed honest against the producer."""
    from camera import job_monitor
    src = inspect.getsource(job_monitor._PrinterMonitor)
    assert '"anomaly_score":' in src and '"stable_verdict":' in src, "producer keys moved"
    assert "composite_score" not in src, "the monitor now stores the key the detector once read"
    assert {"anomaly_score", "stable_verdict"} <= set(_monitor_result("clean", 0.1))


if __name__ == "__main__":
    failed = 0
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            try:
                _f()
                print(f"ok   {_n}")
            except Exception as _e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {_n}: {type(_e).__name__}: {_e}")
    sys.exit(1 if failed else 0)
