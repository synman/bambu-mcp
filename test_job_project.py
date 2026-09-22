"""Tests for job_project.py — the per-printer active-project record.

Why it exists: a print started at the printer's screen reports no subtask_name,
so after a daemon restart bpm's ActiveJobInfo.project_info is empty and the
stream HUD's plate panels showed a cached thumbnail from a previous job (seen
2026-09-16: July 9 PNGs for a job started at 00:36). The record ties the
resolved .3mf path to the job's wall_start_time, which bpm restores across
restarts, so it is only recalled for the same job.

Isolation: job_project keeps its records in the module constant ``_DIR`` (default
~/.bambu-mcp), read at call time by every function. Each test below runs with ``_DIR`` pointed at a
fresh temporary directory, and asserts it did so before doing anything, so nothing here reads,
writes or deletes anything under the real ~/.bambu-mcp (an earlier version did, including a
corrupt-record test that failed outright on a machine with no such directory). The last test
proves it: it runs every other test and compares the real directory's job_project files (name,
size, mtime) before and after. Only those files are compared, not the whole directory, because
the running daemon writes its own files there at any moment.

Run directly (no pytest needed):  .venv/bin/python3 test_job_project.py
"""

import functools
import json
import logging
import shutil
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import job_project  # noqa: E402

_NAME = "unit-test-printer"
_REAL_DIR = Path.home() / ".bambu-mcp"


def _isolated(fn):
    """Run a test with job_project's record directory in a fresh temporary directory."""
    @functools.wraps(fn)
    def wrapper():
        tmp = Path(tempfile.mkdtemp(prefix="job-project-test-"))
        real, job_project._DIR = job_project._DIR, tmp
        try:
            record = job_project._path(_NAME)
            assert record.parent == tmp and _REAL_DIR not in record.parents, (
                f"job_project would persist to {record}, outside the test's temporary directory")
            fn()
        finally:
            job_project._DIR = real
            shutil.rmtree(tmp, ignore_errors=True)
    return wrapper


def _job(pid="/_jobs/x.gcode.3mf", plate=2, ws=1789533376.53, subtask=""):
    pi = types.SimpleNamespace(id=pid, plate_num=plate) if pid is not None else None
    return types.SimpleNamespace(project_info=pi, plate_num=-1, wall_start_time=ws,
                                 gcode_file=f"/data/Metadata/plate_{plate}.gcode", subtask_name=subtask)


def _cleanup():
    job_project.forget(_NAME)


@_isolated
def test_remember_then_recall_same_job():
    _cleanup()
    assert job_project.remember(_NAME, _job()) is True
    assert job_project.recall(_NAME, _job(pid=None)) == ("/_jobs/x.gcode.3mf", 2)
    _cleanup()


@_isolated
def test_recall_rejects_a_different_job():
    _cleanup()
    job_project.remember(_NAME, _job(ws=1000.0))
    assert job_project.recall(_NAME, _job(pid=None, ws=2000.0)) is None
    assert job_project.recall(_NAME, _job(pid=None, ws=-1.0)) is None
    _cleanup()


@_isolated
def test_recall_tolerates_float_jitter_only():
    _cleanup()
    job_project.remember(_NAME, _job(ws=1000.0))
    assert job_project.recall(_NAME, _job(pid=None, ws=1000.4)) is not None
    assert job_project.recall(_NAME, _job(pid=None, ws=1002.0)) is None
    _cleanup()


@_isolated
def test_remember_without_project_writes_nothing():
    _cleanup()
    assert job_project.remember(_NAME, _job(pid=None)) is False
    assert job_project.remember(_NAME, _job(pid="")) is False
    assert job_project.recall(_NAME, _job(pid=None)) is None


@_isolated
def test_remember_accepts_dict_project_info():
    _cleanup()
    job = types.SimpleNamespace(project_info={"id": "/a.3mf", "plate_num": 3}, plate_num=-1,
                                wall_start_time=5.0, gcode_file="", subtask_name="")
    assert job_project.remember(_NAME, job) is True
    assert job_project.recall(_NAME, _job(pid=None, ws=5.0)) == ("/a.3mf", 3)
    _cleanup()


@_isolated
def test_identical_record_is_not_rewritten():
    _cleanup()
    job_project.remember(_NAME, _job())
    path = job_project._path(_NAME)
    before = path.stat().st_mtime_ns
    assert job_project.remember(_NAME, _job()) is True
    assert path.stat().st_mtime_ns == before
    _cleanup()


@_isolated
def test_forget_removes_and_is_idempotent():
    _cleanup()
    job_project.remember(_NAME, _job())
    job_project.forget(_NAME)
    assert not job_project._path(_NAME).exists()
    job_project.forget(_NAME)


@_isolated
def test_corrupt_record_is_ignored():
    _cleanup()
    job_project._path(_NAME).write_text("{not json")
    seen = []

    class _Capture(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    handler, level, propagate = _Capture(), job_project.log.level, job_project.log.propagate
    job_project.log.addHandler(handler)
    job_project.log.setLevel(logging.WARNING)
    job_project.log.propagate = False
    try:
        assert job_project.recall(_NAME, _job(pid=None)) is None
    finally:
        job_project.log.removeHandler(handler)
        job_project.log.setLevel(level)
        job_project.log.propagate = propagate
    assert len(seen) == 1 and "unreadable" in seen[0], seen
    _cleanup()


@_isolated
def test_record_shape_on_disk():
    _cleanup()
    job_project.remember(_NAME, _job())
    rec = json.loads(job_project._path(_NAME).read_text())
    assert rec == {"id": "/_jobs/x.gcode.3mf", "plate_num": 2, "wall_start_time": 1789533376.53,
                   "gcode_file": "/data/Metadata/plate_2.gcode"}
    _cleanup()


def _real_dir_snapshot():
    """(size, mtime_ns) of every job_project file (active_project_*.json and its temp files) in the
    real directory. Files that vanish mid-listing are skipped: the daemon may be replacing one."""
    snap = {}
    if _REAL_DIR.is_dir():
        for entry in _REAL_DIR.iterdir():
            if "active_project" in entry.name:
                try:
                    st = entry.stat()
                except OSError:
                    continue
                snap[entry.name] = (st.st_size, st.st_mtime_ns)
    return snap


def test_suite_never_touches_the_real_directory():
    """Runs every other test, then compares the real directory's job_project files before and
    after, and checks the test printer's own record was never created there."""
    others = [fn for tname, fn in sorted(globals().items())
              if tname.startswith("test_") and callable(fn)
              and tname != "test_suite_never_touches_the_real_directory"]
    assert len(others) == 9, len(others)
    before = _real_dir_snapshot()
    for fn in others:
        fn()
    after = _real_dir_snapshot()
    assert after == before, {"before": before, "after": after}
    leaked = _REAL_DIR / f"active_project_{_NAME}.json"
    assert not leaked.exists(), f"{leaked} exists"


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
