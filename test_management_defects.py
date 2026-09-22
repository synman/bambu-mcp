"""Regression tests for two printer-lifecycle defects.

Defect 5  - tools.management.remove_printer returned its success string for a name that was
            never configured (and stopped a session / deleted credentials for it regardless).
Defect 13 - SessionManager._start_printer rebound the registry entry for a name without
            stopping the BambuPrinter already bound to it, so an add_printer (or any start) on
            an existing name left the old MQTT client connected and its threads running.

Real code paths, real BambuConfig from bpm. Only the BambuPrinter class (a fake that counts
quit() calls and records call order), the credential store (auth) and the camera stream server
are stubbed. Nothing here touches a printer, the network or the running daemon.

Run directly (no pytest needed):  .venv/bin/python3 test_management_defects.py
"""

import sys
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import session_manager as sm_module  # noqa: E402
from tools import management  # noqa: E402

EVENTS: list = []
CONFIGURED: list = []
CALLS: list = []
FAKES: list = []


class FakePrinter:
    """Stands in for bpm BambuPrinter. Counts stop calls and records ordering."""

    quit_raises = False
    # start_session() raises AFTER the client came up (a thread was spawned, then something
    # else failed), so a start failure leaves a live client behind unless somebody quits it.
    start_raises = False
    start_error = RuntimeError
    # Optional per-call pause points, set by the concurrency tests: callable(fake) or None.
    start_hook = None
    quit_hook = None

    def __init__(self, config):
        self.config = config
        self.on_update = None
        self.quit_calls = 0
        self.start_calls = 0
        # True once a start_session() has completed and no quit() has run since. A start that
        # finishes after a quit leaves the client connected, as a real MQTT client would.
        self.live = False
        self.index = len(FAKES)
        FAKES.append(self)

    def start_session(self):
        self.start_calls += 1
        EVENTS.append(("start", self.index))
        if FakePrinter.start_hook:
            FakePrinter.start_hook(self)
        self.live = True
        if FakePrinter.start_raises:
            raise FakePrinter.start_error("start blew up")

    def quit(self):
        self.quit_calls += 1
        EVENTS.append(("quit", self.index))
        if FakePrinter.quit_hook:
            FakePrinter.quit_hook(self)
        self.live = False
        if FakePrinter.quit_raises:
            raise RuntimeError("quit blew up")


class _Env:
    """Swap in the fakes, restore everything on exit."""

    def __enter__(self):
        sm_module._ensure_imports()
        EVENTS.clear()
        CONFIGURED[:] = ["p1"]
        CALLS.clear()
        FAKES.clear()
        FakePrinter.quit_raises = False
        FakePrinter.start_raises = False
        FakePrinter.start_error = RuntimeError
        FakePrinter.start_hook = None
        FakePrinter.quit_hook = None
        self.real_printer = sm_module._BambuPrinter
        self.real_creds = sm_module.auth.get_printer_credentials
        self.real_names = sm_module.auth.get_configured_printer_names
        self.real_save = sm_module.auth.save_printer_credentials
        self.real_delete = sm_module.auth.delete_printer_credentials
        self.real_mgr = management.session_manager
        sm_module._BambuPrinter = FakePrinter
        sm_module.auth.get_printer_credentials = self._creds
        sm_module.auth.get_configured_printer_names = lambda: list(CONFIGURED)
        sm_module.auth.save_printer_credentials = self._save
        sm_module.auth.delete_printer_credentials = self._delete
        self.mgr = sm_module.SessionManager()
        management.session_manager = self.mgr
        self.saved = {}
        return self

    def __exit__(self, *exc):
        FakePrinter.start_raises = False
        FakePrinter.start_error = RuntimeError
        FakePrinter.start_hook = None
        FakePrinter.quit_hook = None
        sm_module._BambuPrinter = self.real_printer
        sm_module.auth.get_printer_credentials = self.real_creds
        sm_module.auth.get_configured_printer_names = self.real_names
        sm_module.auth.save_printer_credentials = self.real_save
        sm_module.auth.delete_printer_credentials = self.real_delete
        management.session_manager = self.real_mgr
        sys.modules.pop("camera.mjpeg_server", None)
        return False

    def _creds(self, name):
        if name not in self.saved and name not in CONFIGURED:
            raise KeyError(f"Printer '{name}' not fully configured.")
        return self.saved.get(name, {"ip": "10.0.0.1", "access_code": "code", "serial": "SER1"})

    def _save(self, name, ip, access_code, serial):
        CALLS.append(("save", name))
        self.saved[name] = {"ip": ip, "access_code": access_code, "serial": serial}
        if name not in CONFIGURED:
            CONFIGURED.append(name)

    def _delete(self, name):
        CALLS.append(("delete", name))
        if name in CONFIGURED:
            CONFIGURED.remove(name)


# ── Defect 13: session_manager ────────────────────────────────────────────────

def test_restarting_a_name_quits_the_old_printer_exactly_once():
    with _Env() as env:
        env.mgr._start_printer("p1")
        first = FAKES[0]
        env.mgr._start_printer("p1")
        second = FAKES[1]
        assert first.quit_calls == 1, f"old printer quit {first.quit_calls}x, want 1"
        assert second.quit_calls == 0, second.quit_calls
        assert env.mgr.get_printer("p1") is second
        assert second.start_calls == 1 and first.start_calls == 1


def test_old_printer_is_quit_before_the_new_one_starts():
    """Both clients share the default MQTT client id: the old one must be gone first."""
    with _Env() as env:
        env.mgr._start_printer("p1")
        env.mgr._start_printer("p1")
        assert EVENTS == [("start", 0), ("quit", 0), ("start", 1)], EVENTS


def test_public_start_printer_on_a_live_name_quits_the_old_printer():
    with _Env() as env:
        env.mgr.start_printer("p1")
        env.mgr.start_printer("p1")
        assert FAKES[0].quit_calls == 1 and FAKES[1].quit_calls == 0


def test_first_start_quits_nothing():
    with _Env() as env:
        env.mgr._start_printer("p1")
        assert FAKES[0].quit_calls == 0
        assert EVENTS == [("start", 0)], EVENTS


def test_a_different_name_is_left_alone():
    with _Env() as env:
        CONFIGURED.append("p2")
        env.mgr._start_printer("p1")
        env.mgr._start_printer("p2")
        assert [f.quit_calls for f in FAKES] == [0, 0]
        assert sorted(env.mgr.list_connected()) == ["p1", "p2"]


def test_a_failing_old_quit_does_not_block_the_rebind():
    with _Env() as env:
        env.mgr._start_printer("p1")
        FakePrinter.quit_raises = True
        env.mgr._start_printer("p1")
        assert FAKES[0].quit_calls == 1
        assert env.mgr.get_printer("p1") is FAKES[1]
        assert FAKES[1].start_calls == 1


def test_a_failed_credential_lookup_leaves_the_live_session_untouched():
    with _Env() as env:
        env.mgr._start_printer("p1")
        CONFIGURED.remove("p1")
        try:
            env.mgr._start_printer("p1")
        except KeyError:
            pass
        else:
            raise AssertionError("expected the credential lookup to raise")
        assert FAKES[0].quit_calls == 0
        assert env.mgr.get_printer("p1") is FAKES[0]


def test_stop_after_restart_quits_only_the_current_printer():
    with _Env() as env:
        env.mgr._start_printer("p1")
        env.mgr._start_printer("p1")
        env.mgr.stop_printer("p1")
        assert [f.quit_calls for f in FAKES] == [1, 1]
        env.mgr.stop_printer("p1")
        assert [f.quit_calls for f in FAKES] == [1, 1]


def test_add_printer_on_an_existing_name_stops_the_old_client():
    with _Env() as env:
        env.mgr._start_printer("p1")
        out = management.add_printer("p1", "10.0.0.9", "SER9", "newcode", user_permission=True)
        assert out == "Printer 'p1' added and session started.", out
        assert FAKES[0].quit_calls == 1, FAKES[0].quit_calls
        assert FAKES[1].quit_calls == 0
        assert env.mgr.get_printer("p1") is FAKES[1]
        assert FAKES[1].config.hostname == "10.0.0.9"


def test_no_caller_stops_a_printer_twice():
    """start_printer / update_printer_credentials stop first, then start: still one quit each."""
    with _Env() as env:
        env.mgr._start_printer("p1")
        assert management.start_printer("p1", user_permission=True) == "Printer 'p1' session restarted."
        assert [f.quit_calls for f in FAKES] == [1, 0], [f.quit_calls for f in FAKES]
        out = management.update_printer_credentials("p1", ip="10.0.0.7", user_permission=True)
        assert out == "Credentials updated and session restarted for 'p1'.", out
        assert [f.quit_calls for f in FAKES] == [1, 1, 0], [f.quit_calls for f in FAKES]
        assert env.mgr.get_printer("p1") is FAKES[2]


class _ContendedLock:
    """threading.Lock stand-in that reports when an acquire has to wait for another holder."""

    on_contend = None  # callable(), set per race by the concurrency tests

    def __init__(self):
        self._lock = threading.Lock()

    def acquire(self, *args, **kwargs):
        if self._lock.acquire(False):
            return True
        _ContendedLock.on_contend()
        return self._lock.acquire(*args, **kwargs)

    def release(self):
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


def _stub_mjpeg():
    """Install a fake camera.mjpeg_server whose stop() records the names it was asked to stop."""
    stopped: list = []
    fake_module = types.ModuleType("camera.mjpeg_server")
    fake_module.mjpeg_server = types.SimpleNamespace(stop=lambda n: stopped.append(n) or True)
    sys.modules["camera.mjpeg_server"] = fake_module
    return stopped


def _race_two_restarts(pause_in, b_action=None):
    """Run a lifecycle call B against a restart A of the same name, through a forced interleaving.

    No sleeps. Thread A (a _start_printer("p1") restart) is parked at a chosen point, then B runs.
    A is released only once B has either finished (nothing serialised the two calls) or blocked on
    a lock A holds (they are serialised). Which of the two happened is observed, not assumed, so
    the same test drives both broken and fixed code without a timing guess.

    pause_in="start": A parks inside its printer's start_session(), after A registered it.
    pause_in="quit":  p1 already has a live printer; A parks inside quit() of that old printer,
                      i.e. after A took it out of the registry and before A starts its own.
    b_action: callable(mgr) run on thread B; default is a second restart of "p1".
    Must run inside an _Env. Returns (mgr, observed) where observed is (b_blocked, b_finished) as
    seen at the moment A was released; callers assert it with _assert_b_waited_for_a.
    """
    b_is_restart = b_action is None
    if b_is_restart:
        def b_action(mgr):
            mgr._start_printer("p1")
    gate_open = threading.Event()   # A may continue
    parked = threading.Event()      # A reached its pause point
    b_built = threading.Event()     # B has built its printer and is about to contend (restarts)
    b_blocked = threading.Event()   # B blocked on a lock A holds
    b_finished = threading.Event()  # B returned
    proceed = threading.Event()     # B finished or is blocked on a lock: release A
    errors: list = []

    # session_manager only calls threading.Lock(), and creates per-name locks lazily during the
    # race, so the stand-in stays installed until the race ends.
    sm_module.threading = types.SimpleNamespace(Lock=_ContendedLock)

    def on_contend():
        b_blocked.set()
        proceed.set()

    _ContendedLock.on_contend = on_contend
    mgr = sm_module.SessionManager()
    real_mgr = management.session_manager
    management.session_manager = mgr

    if pause_in == "quit":
        mgr._start_printer("p1")
        assert [f.live for f in FAKES] == [True]
        pause_index, a_index = 0, 1
    else:
        pause_index, a_index = 0, 0

    def hook(fake):
        if fake.index == pause_index and not parked.is_set():
            parked.set()
            assert gate_open.wait(10), "A was never released"

    FakePrinter.start_hook = hook if pause_in == "start" else None
    FakePrinter.quit_hook = hook if pause_in == "quit" else None

    real_init = FakePrinter.__init__

    def init_with_signal(self, config):
        real_init(self, config)
        if self.index == a_index + 1:
            b_built.set()

    def run_a():
        try:
            mgr._start_printer("p1")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def run_b():
        try:
            b_action(mgr)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            b_finished.set()
            proceed.set()

    FakePrinter.__init__ = init_with_signal
    ta = tb = None
    try:
        ta = threading.Thread(target=run_a)
        ta.start()
        assert parked.wait(10), "A never reached its pause point"
        tb = threading.Thread(target=run_b)
        tb.start()
        if b_is_restart:
            assert b_built.wait(10), "B never built its printer"
        assert proceed.wait(10), "B neither finished nor blocked on a lock"
        observed = (b_blocked.is_set(), b_finished.is_set())
        gate_open.set()
        ta.join(10)
        tb.join(10)
        assert not ta.is_alive() and not tb.is_alive(), "a racing call never returned"
    finally:
        gate_open.set()
        for t in (ta, tb):
            if t is not None:
                t.join(10)
        FakePrinter.__init__ = real_init
        sm_module.threading = threading
        management.session_manager = real_mgr
    assert not errors, errors
    return mgr, observed


def _assert_b_waited_for_a(observed, what):
    b_blocked, b_finished = observed
    assert b_blocked and not b_finished, (
        f"{what} did not wait for the restart of the same name that was still in flight "
        f"(blocked on the per-name lock={b_blocked}, already finished={b_finished})"
    )


def _assert_exactly_one_live_client(mgr):
    registered = mgr.get_printer("p1")
    assert registered is not None, "no printer registered after the race"
    live = [f.index for f in FAKES if f.live]
    assert live == [registered.index], (
        f"live clients {live}, registered {registered.index}: a client was left connected "
        f"that is not the registered one"
    )
    for f in FAKES:
        want = 0 if f is registered else 1
        assert f.quit_calls == want, f"printer {f.index} quit {f.quit_calls}x, want {want}"
    assert registered.start_calls == 1, registered.start_calls


def _assert_everything_stopped(mgr):
    """After a stop won or lost a race with a restart: nothing registered, nothing left running."""
    assert mgr.get_printer("p1") is None, "a printer is still registered after the stop"
    live = [f.index for f in FAKES if f.live]
    assert live == [], (
        f"clients {live} are still connected but not registered: the stop quit a printer "
        f"whose start_session() was still in flight"
    )
    for f in FAKES:
        assert f.quit_calls == 1, f"printer {f.index} quit {f.quit_calls}x, want 1"


def test_concurrent_restarts_both_stopping_before_either_rebinds_leave_one_live_client():
    """Reviewer interleaving: both callers take the old printer out before either registers."""
    with _Env():
        mgr, observed = _race_two_restarts("quit")
        _assert_exactly_one_live_client(mgr)
        _assert_b_waited_for_a(observed, "the second restart")


def test_concurrent_restart_during_start_does_not_orphan_the_first_client():
    """B replaces A while A's start_session() is still in flight; A must not stay connected."""
    with _Env():
        mgr, observed = _race_two_restarts("start")
        _assert_exactly_one_live_client(mgr)
        _assert_b_waited_for_a(observed, "the second restart")


def test_stop_racing_a_restart_mid_start_does_not_orphan_the_client():
    """stop_printer lands while a restart's start_session() is in flight: that client must not
    stay connected and unregistered."""
    with _Env():
        mgr, observed = _race_two_restarts("start", lambda m: m.stop_printer("p1"))
        _assert_everything_stopped(mgr)
        _assert_b_waited_for_a(observed, "stop_printer")


def test_stop_racing_a_restart_mid_quit_does_not_orphan_the_new_client():
    """stop_printer lands while a restart is quitting the old printer, before it starts its own."""
    with _Env():
        mgr, observed = _race_two_restarts("quit", lambda m: m.stop_printer("p1"))
        _assert_everything_stopped(mgr)
        _assert_b_waited_for_a(observed, "stop_printer")


def test_remove_printer_racing_a_restart_does_not_orphan_the_client():
    with _Env():
        def remove(m):
            out = management.remove_printer("p1", user_permission=True)
            assert out == "Printer 'p1' removed and credentials deleted.", out

        mgr, observed = _race_two_restarts("start", remove)
        _assert_everything_stopped(mgr)
        _assert_b_waited_for_a(observed, "remove_printer")
        assert CALLS == [("delete", "p1")], CALLS


def test_disconnect_printer_racing_a_restart_does_not_orphan_the_client():
    with _Env():
        stopped = _stub_mjpeg()

        def disconnect(m):
            out = management.disconnect_printer("p1", user_permission=True)
            assert out.startswith("Printer 'p1' disconnected."), out

        mgr, observed = _race_two_restarts("start", disconnect)
        _assert_everything_stopped(mgr)
        _assert_b_waited_for_a(observed, "disconnect_printer")
        assert stopped == ["p1"], stopped


def test_registry_lock_is_never_held_across_quit_or_start_session():
    """quit() and start_session() can block; lookups (get_printer) must not wait behind them."""
    with _Env() as env:
        held = []
        FakePrinter.start_hook = lambda fake: held.append(("start", env.mgr._lock.locked()))
        FakePrinter.quit_hook = lambda fake: held.append(("quit", env.mgr._lock.locked()))
        env.mgr._start_printer("p1")
        env.mgr._start_printer("p1")
        assert held == [("start", False), ("quit", False), ("start", False)], held


def test_registry_lock_is_not_held_across_a_stop_or_a_failed_start_quit():
    """The same rule for the two other quit() call sites: a stop and a failed start."""
    with _Env() as env:
        held = []
        FakePrinter.quit_hook = lambda fake: held.append(("quit", env.mgr._lock.locked()))
        env.mgr._start_printer("p1")
        env.mgr.stop_printer("p1")
        FakePrinter.start_raises = True
        try:
            env.mgr._start_printer("p1")
        except RuntimeError:
            pass
        assert held == [("quit", False), ("quit", False)], held


# ── A failed start must not stay registered ───────────────────────────────────

def test_failed_start_leaves_no_registered_printer_and_quits_the_client():
    with _Env() as env:
        FakePrinter.start_raises = True
        try:
            env.mgr._start_printer("p1")
        except RuntimeError as exc:
            assert str(exc) == "start blew up", exc
        else:
            raise AssertionError("expected start_session() to raise")
        assert env.mgr.get_printer("p1") is None, (
            "a printer whose start_session() raised is still registered"
        )
        assert env.mgr.list_connected() == [], env.mgr.list_connected()
        assert env.mgr.is_connected("p1") is False
        assert FAKES[0].quit_calls == 1, f"failed printer quit {FAKES[0].quit_calls}x, want 1"
        assert not FAKES[0].live, "the half-started client was left connected"


def test_failed_restart_unregisters_the_new_printer_and_does_not_resurrect_the_old_one():
    with _Env() as env:
        env.mgr._start_printer("p1")
        FakePrinter.start_raises = True
        try:
            env.mgr._start_printer("p1")
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected start_session() to raise")
        assert env.mgr.get_printer("p1") is None, "a failed restart left a printer registered"
        assert [f.quit_calls for f in FAKES] == [1, 1], [f.quit_calls for f in FAKES]
        assert not any(f.live for f in FAKES), [f.index for f in FAKES if f.live]
        FakePrinter.start_raises = False
        env.mgr._start_printer("p1")
        assert env.mgr.get_printer("p1") is FAKES[2]
        assert FAKES[2].live and FAKES[2].quit_calls == 0, (FAKES[2].live, FAKES[2].quit_calls)
        assert [f.quit_calls for f in FAKES] == [1, 1, 0], "a later start re-quit a dead printer"


def test_failed_start_still_raises_the_start_error_when_the_cleanup_quit_fails():
    with _Env() as env:
        FakePrinter.start_raises = True
        FakePrinter.quit_raises = True
        try:
            env.mgr._start_printer("p1")
        except RuntimeError as exc:
            assert str(exc) == "start blew up", f"the start error was masked by {exc!r}"
        else:
            raise AssertionError("expected start_session() to raise")
        assert env.mgr.get_printer("p1") is None, "a printer whose start_session() raised is registered"
        assert FAKES[0].quit_calls == 1, f"failed printer quit {FAKES[0].quit_calls}x, want 1"


def test_an_interrupt_during_start_also_leaves_nothing_registered():
    """KeyboardInterrupt is not an Exception: the cleanup must not be limited to Exception."""
    with _Env() as env:
        FakePrinter.start_raises = True
        FakePrinter.start_error = KeyboardInterrupt
        try:
            env.mgr._start_printer("p1")
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("expected the interrupt to propagate")
        assert env.mgr.get_printer("p1") is None, "an interrupted start left its printer registered"
        assert FAKES[0].quit_calls == 1 and not FAKES[0].live, (FAKES[0].quit_calls, FAKES[0].live)
        assert env.mgr._restart_locks == {}, sorted(env.mgr._restart_locks)


def test_failed_start_unregisters_only_its_own_entry():
    """Defensive identity check: if the name was taken over meanwhile, leave that entry alone."""
    with _Env() as env:
        other = object()
        FakePrinter.start_hook = lambda fake: env.mgr._printers.__setitem__("p1", other)
        FakePrinter.start_raises = True
        try:
            env.mgr._start_printer("p1")
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected start_session() to raise")
        assert env.mgr._printers.get("p1") is other, "the failure path removed someone else's entry"
        assert FAKES[0].quit_calls == 1, "its own failed client must still be quit"


def test_add_printer_with_a_failing_start_leaves_no_session():
    with _Env():
        FakePrinter.start_raises = True
        out = management.add_printer("p1", "10.0.0.9", "SER9", "code", user_permission=True)
        assert out == "Credentials saved for 'p1' but session failed to start: start blew up", out
        status = management.get_printer_connection_status("p1")
        assert status["configured"] is True and status["session_active"] is False, status
        assert management.get_configured_printers()["printers"] == [
            {"name": "p1", "connected": False, "session_active": False}
        ]


def test_start_printer_tool_with_a_failing_restart_leaves_no_session():
    with _Env() as env:
        env.mgr._start_printer("p1")
        FakePrinter.start_raises = True
        out = management.start_printer("p1", user_permission=True)
        assert out == "Error starting session for 'p1': start blew up", out
        assert management.get_printer_connection_status("p1")["session_active"] is False, (
            "a session object is still registered after the restart failed"
        )
        assert not any(f.live for f in FAKES), [f.index for f in FAKES if f.live]


# ── The per-name lock table ───────────────────────────────────────────────────

def test_restart_lock_table_does_not_grow_with_names_ever_started():
    with _Env() as env:
        for n in ("p1", "p2", "p3"):
            if n not in CONFIGURED:
                CONFIGURED.append(n)
            env.mgr._start_printer(n)
        env.mgr.stop_printer("p1")
        env.mgr.stop_printer("p2")
        out = management.remove_printer("p3", user_permission=True)
        assert out == "Printer 'p3' removed and credentials deleted.", out
        env.mgr.stop_printer("never-started")
        assert env.mgr._restart_locks == {}, (
            f"per-name lock table still holds {sorted(env.mgr._restart_locks)} after every "
            f"name was stopped or removed"
        )


def test_a_failed_start_does_not_leave_a_lock_table_entry():
    with _Env() as env:
        FakePrinter.start_raises = True
        try:
            env.mgr._start_printer("p1")
        except RuntimeError:
            pass
        assert env.mgr._restart_locks == {}, (
            f"per-name lock table still holds {sorted(env.mgr._restart_locks)} after a failed start"
        )


def test_pruning_never_gives_two_threads_different_locks_for_one_name():
    """T1 stops p1, parked in quit() while holding the name's lock. T2 restarts p1 and waits on
    that lock. T1 leaves, and T2 takes the lock and parks in start_session(). T3 then stops p1
    and must wait for T2. A table that dropped the name's entry when T1 left (T2 still needed
    it) would hand T3 a fresh lock: T3 would quit T2's printer mid-start and leave it connected.
    Which thread wakes first is fixed by construction: T2 is the only waiter."""
    with _Env():
        sm_module.threading = types.SimpleNamespace(Lock=_ContendedLock)
        mgr = sm_module.SessionManager()
        mgr._start_printer("p1")                       # FAKES[0], live and registered
        assert [f.live for f in FAKES] == [True]

        gate1, gate2 = threading.Event(), threading.Event()
        t1_parked, t2_parked = threading.Event(), threading.Event()
        t2_progress = threading.Event()   # T2 blocked on a lock, or got through without waiting
        t2_blocked, t3_blocked = threading.Event(), threading.Event()
        t3_finished, t3_settled = threading.Event(), threading.Event()
        seen: set = set()
        errors: list = []

        def on_contend():
            # Only a thread's FIRST contention is the name lock: the registry lock is never held
            # while a thread waits (T1 is parked outside it, T2 parked outside it), so nothing
            # else can make T2 or T3 wait before that point.
            who = threading.current_thread().name
            if who in seen:
                return
            seen.add(who)
            if who == "t2":
                t2_blocked.set()
                t2_progress.set()
            elif who == "t3":
                t3_blocked.set()
                t3_settled.set()

        _ContendedLock.on_contend = on_contend

        def quit_hook(fake):
            if fake.index == 0:
                t1_parked.set()
                gate1.wait(10)

        def start_hook(fake):
            if fake.index == 1:
                t2_parked.set()
                t2_progress.set()
                gate2.wait(10)

        FakePrinter.quit_hook = quit_hook
        FakePrinter.start_hook = start_hook

        def guarded(fn, done=None):
            def run():
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001
                    errors.append(exc)
                finally:
                    if done:
                        done.set()
                        t3_settled.set()
            return run

        t1 = threading.Thread(target=guarded(lambda: mgr.stop_printer("p1")), name="t1")
        t2 = threading.Thread(target=guarded(lambda: mgr._start_printer("p1")), name="t2")
        t3 = threading.Thread(
            target=guarded(lambda: mgr.stop_printer("p1"), t3_finished), name="t3"
        )
        started = []
        try:
            t1.start(); started.append(t1)
            assert t1_parked.wait(10), "T1 never reached quit()"
            t2.start(); started.append(t2)
            assert t2_progress.wait(10), "T2 neither waited nor started"
            assert t2_blocked.is_set(), (
                "T2 did not wait for the stop of the same name that was still in flight"
            )
            gate1.set()
            t1.join(10)
            assert t2_parked.wait(10), "T2 never reached start_session()"
            t3.start(); started.append(t3)
            assert t3_settled.wait(10), "T3 neither waited nor finished"
            observed = (t3_blocked.is_set(), t3_finished.is_set())
        finally:
            gate1.set()
            gate2.set()
            for t in started:
                t.join(10)
            sm_module.threading = threading
        assert not errors, errors
        assert observed == (True, False), (
            f"T3 (stop) did not wait for T2's restart of the same name: blocked={observed[0]} "
            f"finished={observed[1]}. It ran under a different lock than the one T2 held"
        )
        _assert_everything_stopped(mgr)
        assert mgr._restart_locks == {}, (
            f"per-name lock table still holds {sorted(mgr._restart_locks)}"
        )


# ── Defect 5: management.remove_printer ───────────────────────────────────────

def test_remove_printer_unknown_name_returns_an_error_and_changes_nothing():
    with _Env() as env:
        env.mgr._start_printer("p1")
        out = management.remove_printer("ghost", user_permission=True)
        assert out == "Error: Printer 'ghost' is not configured.", out
        assert CALLS == [], CALLS
        assert FAKES[0].quit_calls == 0
        assert env.mgr.get_printer("p1") is FAKES[0]


def test_remove_printer_permission_refusal_comes_before_the_configured_check():
    with _Env():
        out = management.remove_printer("ghost", user_permission=False)
        assert out.startswith("Error: user_permission must be True"), out
        assert "not configured" not in out, out


def test_remove_printer_configured_name_without_permission_changes_nothing():
    with _Env() as env:
        env.mgr._start_printer("p1")
        out = management.remove_printer("p1")
        assert out.startswith("Error: user_permission must be True"), out
        assert CALLS == [] and FAKES[0].quit_calls == 0


def test_remove_printer_configured_name_still_succeeds():
    with _Env() as env:
        env.mgr._start_printer("p1")
        out = management.remove_printer("p1", user_permission=True)
        assert out == "Printer 'p1' removed and credentials deleted.", out
        assert CALLS == [("delete", "p1")], CALLS
        assert FAKES[0].quit_calls == 1
        assert env.mgr.get_printer("p1") is None


def test_remove_printer_twice_second_call_is_an_error():
    with _Env() as env:
        env.mgr._start_printer("p1")
        management.remove_printer("p1", user_permission=True)
        out = management.remove_printer("p1", user_permission=True)
        assert out == "Error: Printer 'p1' is not configured.", out
        assert CALLS == [("delete", "p1")], CALLS


def test_disconnect_printer_unknown_name_is_an_error_not_a_success():
    """Same defect class as remove_printer: a success string for a never-configured name."""
    with _Env():
        stopped = []
        fake_module = types.ModuleType("camera.mjpeg_server")
        fake_module.mjpeg_server = types.SimpleNamespace(stop=lambda n: stopped.append(n) or True)
        sys.modules["camera.mjpeg_server"] = fake_module
        out = management.disconnect_printer("ghost", user_permission=True)
        assert out == "Error: Printer 'ghost' is not configured.", out
        assert stopped == [], stopped


def test_disconnect_printer_configured_name_still_succeeds():
    with _Env() as env:
        stopped = []
        fake_module = types.ModuleType("camera.mjpeg_server")
        fake_module.mjpeg_server = types.SimpleNamespace(stop=lambda n: stopped.append(n) or True)
        sys.modules["camera.mjpeg_server"] = fake_module
        env.mgr._start_printer("p1")
        out = management.disconnect_printer("p1", user_permission=True)
        assert out.startswith("Printer 'p1' disconnected."), out
        assert stopped == ["p1"] and FAKES[0].quit_calls == 1


# ── Replaced client must not speak for the name (restart via add_printer) ─────
#
# bpm's real quit() sets service_state = QUIT and calls _notify_update(), which invokes the
# printer's on_update callback. On an add_printer over a live name the registry already holds the
# NEW printer by then, so the old client's callback read the new printer's blank state and every
# consumer saw a bogus RUNNING -> IDLE -> RUNNING on the same job. FakePrinter.quit() is silent,
# which is why the rest of this file never saw it.


class NotifyingFakePrinter(FakePrinter):
    """FakePrinter whose quit() notifies the way bpm's does: set QUIT, then call on_update."""

    def __init__(self, config):
        super().__init__(config)
        self.service_state = "CONNECTED"
        # What a freshly built printer reports before its first MQTT frame.
        self.printer_state = f"blank-state-of-fake-{self.index}"

    def quit(self):
        self.service_state = "QUIT"
        if self.on_update:
            self.on_update(self)
        super().quit()


def test_replaced_client_quit_notification_does_not_reach_consumers():
    with _Env() as env:
        sm_module._BambuPrinter = NotifyingFakePrinter
        seen = []
        env.mgr.register_update_callback(lambda n: seen.append((n, env.mgr.get_state(n))))
        env.mgr._start_printer("p1")
        env.mgr._start_printer("p1")  # add_printer's path: no stop first
        old, new = FAKES[0], FAKES[1]
        assert old.quit_calls == 1 and old.service_state == "QUIT"
        assert seen == [], f"old client's quit spoke for the name: {seen}"
        assert env.mgr.get_printer("p1") is new
        # The new client stays wired: detaching must not touch it.
        new.on_update(new)
        assert seen == [("p1", new.printer_state)], seen


def test_registry_never_has_a_gap_while_the_replaced_client_quits():
    with _Env() as env:
        sm_module._BambuPrinter = NotifyingFakePrinter
        during = []
        env.mgr._start_printer("p1")
        FakePrinter.quit_hook = lambda fake: during.append(env.mgr.get_printer("p1"))
        env.mgr._start_printer("p1")
        assert during == [FAKES[1]], during


def test_stop_printer_quit_notification_reports_no_state():
    """stop_printer pops first: the quitting client's callback finds no printer, never a foreign one."""
    with _Env() as env:
        sm_module._BambuPrinter = NotifyingFakePrinter
        seen = []
        env.mgr.register_update_callback(lambda n: seen.append((n, env.mgr.get_state(n))))
        env.mgr._start_printer("p1")
        env.mgr.stop_printer("p1")
        assert FAKES[0].quit_calls == 1
        assert [state for _name, state in seen] == [None] * len(seen), seen


def test_add_printer_over_a_live_name_raises_no_false_transition_end_to_end():
    """Real BambuPrinter/BambuState/quit(), real NotificationManager and job monitor; only
    start_session is stubbed (never connects) and job_project is stubbed so nothing is written to
    ~/.bambu-mcp. A RUNNING print with an active HMS fault is replaced by a fresh client whose first
    frame reports the same RUNNING job: nothing may look like a job boundary."""
    import tempfile
    import notifications
    from bpm.bambuprinter import BambuPrinter
    from bpm.bambustate import BambuState
    from bpm.bambuproject import ActiveJobInfo
    from bpm.bambutools import decodeError, decodeHMS
    from camera import job_analyzer, job_monitor

    class NoNetPrinter(BambuPrinter):
        def start_session(self):  # never touch the network
            self._started = True

    name = "e2e-restart"
    fault = [decodeError(0x07038012)] + decodeHMS([{"attr": 0x0C000100, "code": 0x0002001B}])
    with _Env() as env:
        sm_module._BambuPrinter = NoNetPrinter
        sm_module.auth.get_printer_credentials = lambda n: {"ip": "10.0.0.1", "access_code": "x", "serial": "SER"}
        real_singleton = sm_module.session_manager
        real_notifications = notifications.notifications
        real_persist = job_monitor._PERSIST_DIR
        real_job_project = sys.modules.get("job_project")
        mgr = sm_module.session_manager = sm_module.SessionManager()  # notifications/job_monitor import this lazily
        sys.modules["job_project"] = types.SimpleNamespace(remember=lambda *a, **k: False, forget=lambda *a, **k: None)
        with tempfile.TemporaryDirectory(prefix="e2e-restart-jm-") as tmp:
            job_monitor._PERSIST_DIR = Path(tmp)
            notif = notifications.notifications = notifications.NotificationManager()
            mon = job_monitor._PrinterMonitor(name)
            job_monitor._monitors[name] = mon
            try:
                mgr.register_update_callback(lambda n: job_monitor.on_update(n))
                mgr.start_printer(name)
                old = mgr.get_printer(name)
                old._printer_state = BambuState(gcode_state="RUNNING", hms_errors=fault, wifi_signal_strength="-50dBm")
                old._active_job_info = ActiveJobInfo(stage_id=0, subtask_name="job-A")

                # Prime: first observation, then the running print with its fault.
                notif.check_and_emit(name)
                mon.on_update()
                notif.get_pending(name, clear=True)
                mon._last_gcode_state = "RUNNING"
                mon._confidence_window.append("clean")
                mon._health_history.append({"ts": 1.0, "success_pct": 0.9})
                with mon._lock:
                    mon._latest_result = {"verdict": "clean", "stage_gated": False}
                job_analyzer.store_reference(name, b"\xff\xd8ref")

                def monitor_intact():
                    return (
                        len(mon._confidence_window) == 1
                        and len(mon.get_health_history()) == 1
                        and mon.get_latest_result() is not None
                        and job_analyzer.get_reference(name)[0] is not None
                        and mon._last_gcode_state == "RUNNING"
                    )

                assert monitor_intact()

                mgr.start_printer(name)  # add_printer's path: replace a live name, no stop first
                new = mgr.get_printer(name)
                assert new is not old
                during = [a["type"] for a in notif.get_pending(name, clear=True)]
                assert during == [], f"alerts raised by the old client's quit: {during}"
                assert monitor_intact(), (
                    f"job monitor wiped by the old client's quit: window={len(mon._confidence_window)} "
                    f"history={len(mon.get_health_history())} result={mon.get_latest_result() is not None} "
                    f"ref={job_analyzer.get_reference(name)[0] is not None} last={mon._last_gcode_state!r}"
                )

                # The new session's first frame: the same job, still RUNNING, same fault.
                new._printer_state = BambuState(gcode_state="RUNNING", hms_errors=list(fault), wifi_signal_strength="-50dBm")
                new._active_job_info = ActiveJobInfo(stage_id=0, subtask_name="job-A")
                mgr.on_update(name)
                after = [a["type"] for a in notif.get_pending(name, clear=True)]
                assert after == [], f"false alerts on the new session's first frame: {after}"
                assert monitor_intact(), "job monitor wiped by the new session's first frame"
            finally:
                job_monitor._monitors.pop(name, None)
                job_analyzer.clear_reference(name)
                job_monitor._PERSIST_DIR = real_persist
                notifications.notifications = real_notifications
                sm_module.session_manager = real_singleton
                if real_job_project is None:
                    sys.modules.pop("job_project", None)
                else:
                    sys.modules["job_project"] = real_job_project


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
