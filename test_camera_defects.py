"""Regression tests for the camera and charts defects in bambu-mcp.

Stage gate. The background monitor read ``getattr(state, "stage", 255)``, an attribute BambuState
does not have, so the stage was always 255 and the gate that holds camera analysis back during
heating, leveling, filament changes and pauses never held anything back. The stage lives on the job
record, ``ActiveJobInfo.stage_id`` (bpm copies the printer's ``stg_cur`` into it). The analyzer read
two different context keys, ``stage_id`` in one place and ``stage`` (which nothing supplies) in
another. The monitor also carried its own stage-name table numbered differently from bpm's
``parseStage``, and its gated-result builder called a function that does not exist.

Same defect class, fixed here: a name that does not exist read through ``getattr(x, name, default)``
or an ``import`` inside a ``try`` that swallows the failure. ``from bambu_printer_manager import
getPrinterSeriesByModel`` (the package is ``bpm``) left ``printer_series`` at "UNKNOWN" for every
printer, and ``nozzle_flow_type`` read ``flow_type`` where the nozzle record calls it ``flow``. The
charts AMS panel imported ``get_spool_info`` from ``tools.filament``; it lives in ``tools.state``.

analyze_active_job left ``_factors`` unassigned when the failure-probability computation raised and
then read it, so the tool raised UnboundLocalError. open_job_state read ``score`` from a cached
result that stores ``anomaly_score``.

The tests drive the real monitor, analyzer, tools and charts with real bpm objects (BambuState,
ActiveJobInfo, BambuSpool, NozzleCharacteristics). Only the camera frame source, session_manager and
the OS viewer launch are stubbed. Nothing here touches a printer, the network or the daemon.

Stage text, HUD and URLs (added with the review fixes). resume_print's docstring numbered the pause
stages with the deleted off-by-one table (user pause 17, M400 pause 6, filament runout 7); bpm's
parseStage says 16, 5 and 6. The stream HUD read ``e.description`` from HMS entries, which carry the
text under ``msg`` (so every tooltip was blank), and hid the stage row only for a stage name that
the deleted table used ("Printing normally"), so it showed "Printing" and "Completed" as if they were
activities. open_charts and view_stream interpolated the printer name, resolution and quality into
URLs without escaping, so a name containing "&", "#", "+" or a space reached the server truncated or
altered, and view_stream never checked the resolution. The HUD tests run the real served script under
node against a stub DOM (skipped with a printed SKIP line when node is not installed).

Run directly (no pytest needed):  .venv/bin/python3 test_camera_defects.py
"""

import ast
import contextlib
import http.client
import importlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import session_manager as sm_mod  # noqa: E402
from bpm.bambuproject import ActiveJobInfo  # noqa: E402
from bpm.bambuspool import BambuSpool  # noqa: E402
from bpm.bambustate import BambuState, NozzleCharacteristics  # noqa: E402
from bpm.bambutools import NozzleFlowType, PrinterModel, parseStage  # noqa: E402
from camera import job_analyzer, job_monitor, mjpeg_server  # noqa: E402
from tools import camera as camera_tool  # noqa: E402
from tools import charts as charts_tool  # noqa: E402
from tools import notifications as notifications_tool  # noqa: E402
from tools import print_control as print_control_tool  # noqa: E402
from tools import state as state_mod  # noqa: E402

REPO = Path(__file__).resolve().parent

# Stage codes that mean no activity is in progress: bpm's parseStage gives -1 and 0 an empty name,
# 100 "Printing" and 255 "Completed". While a job is RUNNING or PAUSE these are a normal print.
QUIET = (-1, 0, 100, 255)


def _jpeg(w=160, h=90, top=255):
    """Noise frame. A low ``top`` keeps the 10 s hot-pixel pre-check below its trigger, so a tick
    runs the full analysis only when the analysis interval says so."""
    rng = np.random.default_rng(7)
    buf = io.BytesIO()
    Image.fromarray(rng.integers(0, top, (h, w, 3), dtype=np.uint8)).save(buf, format="JPEG")
    return buf.getvalue()


class _Manager:
    def __init__(self, state, job, printer):
        self.state, self.job, self.printer = state, job, printer

    def get_state(self, name):
        return self.state

    def get_job(self, name):
        return self.job

    def get_config(self, name):
        return None

    def get_printer(self, name):
        return self.printer


def _printer(model=PrinterModel.H2D):
    cfg = SimpleNamespace(hostname="printer.invalid", printer_model=model, capabilities=None,
                          bpm_cache_path=None, serial_number=None)
    return SimpleNamespace(config=cfg, speed_level=0, light_state=False)


def _job(stage_id, **kw):
    return ActiveJobInfo(stage_id=stage_id, stage_name=parseStage(stage_id), **kw)


@contextlib.contextmanager
def _env(state, job, printer=None, jpeg=None):
    """Swap in a session manager holding real bpm objects and a frame source. Yields the list that
    records every frame capture, so a test can tell whether camera work ran."""
    tmp = Path(tempfile.mkdtemp(prefix="camdefects-"))
    captures = []

    def _capture(*a, **k):
        captures.append(1)
        return jpeg

    mgr = _Manager(state, job, printer or _printer())
    saved = (sm_mod.session_manager, camera_tool.session_manager, state_mod.session_manager,
             job_monitor._PERSIST_DIR, job_monitor._capture_one_frame, camera_tool._capture_jpeg,
             camera_tool.get_protocol, camera_tool._get_printer_checked)
    sm_mod.session_manager = camera_tool.session_manager = state_mod.session_manager = mgr
    job_monitor._PERSIST_DIR = tmp
    job_monitor._capture_one_frame = _capture
    camera_tool._capture_jpeg = _capture
    camera_tool.get_protocol = lambda printer: "tcp_tls"
    camera_tool._get_printer_checked = lambda name: (mgr.printer, None)
    try:
        yield captures
    finally:
        (sm_mod.session_manager, camera_tool.session_manager, state_mod.session_manager,
         job_monitor._PERSIST_DIR, job_monitor._capture_one_frame, camera_tool._capture_jpeg,
         camera_tool.get_protocol, camera_tool._get_printer_checked) = saved
        shutil.rmtree(tmp, ignore_errors=True)


def _tick(stage_id, gcode_state="RUNNING", jpeg=None, name="camdef-tick"):
    """One monitor loop tick against a printer whose job is at ``stage_id``."""
    state = BambuState(gcode_state=gcode_state)
    with _env(state, _job(stage_id), jpeg=jpeg) as captures:
        mon = job_monitor._PrinterMonitor(name)
        mon._tick()
        return mon.get_latest_result(), len(captures)


# --------------------------------------------------------------------------- defect 2: stage gate

def _bpm_gated_codes():
    named = {c for c in range(-1, 256) if not parseStage(c).startswith("Stage [")}
    return sorted(named - set(QUIET))


def test_gate_holds_back_camera_work_in_every_named_activity_stage():
    # One tick per stage code bpm names as an activity (bed leveling, preheat, filament change, pause,
    # calibration, ...). The expected set is read from bpm's own table, so a code bpm adds later fails
    # here instead of being silently analyzed or silently gated.
    gated = _bpm_gated_codes()
    assert len(gated) >= 60, len(gated)
    failures = []
    for code in gated:
        result, captures = _tick(code)
        ok = (captures == 0 and result is not None and result["stage_gated"] is True
              and result["stage"] == code and result["stage_name"] == parseStage(code))
        if not ok:
            failures.append((code, captures, result and (result["stage"], result["stage_gated"])))
    assert not failures, f"{len(failures)} of {len(gated)} stages not gated, e.g. {failures[:3]}"


def test_normal_print_is_not_gated():
    # The control for the gate: quiet codes and codes bpm does not name must still reach the camera.
    # Without this a gate keyed on the wrong value would hold analysis back for the whole print.
    for code in QUIET + (59, 60, 150, 200):
        result, captures = _tick(code)
        assert captures > 0, f"stage {code}: camera work was held back"
        assert result is None, f"stage {code}: stored a gated result {result}"


def test_gate_does_not_consume_the_analysis_interval():
    # Leveling for 30 s, then the print starts: the first real analysis must run on the next tick,
    # not up to ANALYZE_INTERVAL later because the gated result reset the timer. The result, not
    # the frame capture, is the evidence: the 10 s pre-check also captures a frame.
    state = BambuState(gcode_state="RUNNING")
    job = _job(1)
    with _env(state, job, jpeg=_jpeg(top=60)):
        mon = job_monitor._PrinterMonitor("camdef-resume")
        mon._tick()
        first = mon.get_latest_result()
        assert first is not None and first["stage_gated"] is True, f"no gated result stored: {first}"
        job.stage_id, job.stage_name = 0, parseStage(0)
        mon._tick()
        r = mon.get_latest_result()
        job_analyzer._references.pop("camdef-resume", None)
    assert r is not None and r["stage_gated"] is False and "job_state_composite_png" in r, \
        f"first tick after the prep stage ran no analysis: {r}"


def test_a_new_job_starts_from_clean_state():
    # on_update read a nonexistent self._fp_history before it cleared the cached result, so the
    # previous job's result, persisted copy and health records were never cleared at job start (a
    # broad except hid the AttributeError), and the gated timer was never reset either.
    state = BambuState(gcode_state="RUNNING")
    with _env(state, _job(1)):
        name = "camdef-newjob"
        mon = job_monitor._PrinterMonitor(name)
        mon._tick()                                            # leaves a gated result and a fresh gated timer
        assert mon.get_latest_result() is not None, "no gated result stored"
        mon._confidence_window.append("warning")
        mon._health_history.append({"ts": 1.0, "success_pct": 0.5})
        job_monitor._save_result(name, {"verdict": "warning", "stage_gated": False})
        assert job_monitor._persist_path(name).exists()
        mon._last_gcode_state = "FINISH"
        mon.on_update()                                        # FINISH -> RUNNING
        assert mon.get_latest_result() is None, "previous job's result survived the job start"
        assert not job_monitor._persist_path(name).exists(), "persisted result survived the job start"
        assert not mon._confidence_window and not mon.get_health_history()
        mon._tick()
        r = mon.get_latest_result()
        assert r is not None and r["stage_gated"] is True, "gated timer was not reset at job start"


def test_analysis_result_reports_stage_255_while_printing():
    # The HUD (camera/mjpeg_server.py) shows STANDBY for any result whose stage is not 255, and
    # api_server.py's no-result fallback uses 255 too. bpm reports a print in progress as 0 (or -1,
    # 100), so the monitor must not pass the raw code through into a result it stores as "printing".
    state = BambuState(gcode_state="RUNNING")
    for stage_id in QUIET:
        with _env(state, _job(stage_id), jpeg=_jpeg()):
            mon = job_monitor._PrinterMonitor("camdef-stage")
            mon._tick()
            r = mon.get_latest_result()
            ctx = job_monitor._build_context("camdef-stage", state)
            job_analyzer._references.pop("camdef-stage", None)
        assert r is not None and r["stage_gated"] is False, r
        assert (r["stage"], r["stage_name"]) == (255, "printing"), (stage_id, r["stage"], r["stage_name"])
        assert ctx["stage_id"] == stage_id, ctx["stage_id"]  # the analysis context keeps the raw code


def test_gated_result_reports_the_activity_stage():
    for code in (1, 5, 7, 16, 22):
        result, _ = _tick(code)
        assert result is not None, f"stage {code}: no gated result stored"
        assert (result["stage"], result["stage_name"]) == (code, parseStage(code)), result


def test_gated_result_scores_confidence_from_the_real_context():
    # _store_gated_result built its context with a function that does not exist; the NameError was
    # swallowed and confidence was computed from an empty context.
    state = BambuState(gcode_state="RUNNING")
    job = _job(1, print_percentage=60)
    with _env(state, job):
        mon = job_monitor._PrinterMonitor("camdef-conf")
        mon._tick()
        r = mon.get_latest_result()
        ctx = job_monitor._build_context("camdef-conf", state)
    assert r is not None and r["stage_gated"] is True, f"no gated result stored: {r}"
    expected = job_analyzer.compute_decision_confidence(0, True, ctx)
    assert ctx["progress_pct"] == 60
    assert r["decision_confidence"] == expected, (r["decision_confidence"], expected)


def test_yolo_is_gone_and_nothing_needs_onnxruntime():
    """The YOLO layer never ran: its model URL returned 404, so yolo_available was always False.
    It is removed together with the onnxruntime dependency it alone needed."""
    import dataclasses
    import importlib.util

    here = Path(__file__).resolve().parent
    assert importlib.util.find_spec("camera.yolo_detector") is None
    fields = {f.name for f in dataclasses.fields(job_analyzer.JobStateReport)}
    assert not {n for n in fields if "yolo" in n}, fields
    assert "yolo" not in (here / "camera" / "job_analyzer.py").read_text().lower()
    assert "onnxruntime" not in (here / "pyproject.toml").read_text().lower()


def test_analyzer_gate_reads_stage_id():
    # analyze() read printer_context["stage"], which no caller supplies, so stage_gated depended on
    # gcode_state alone.
    frame = _jpeg()
    base = {"gcode_state": "RUNNING", "progress_pct": 50}
    with _env(BambuState(), None):
        for code in _bpm_gated_codes():
            assert job_analyzer.analyze(frame, dict(base, stage_id=code)).stage_gated is True, code
        for code in QUIET:
            assert job_analyzer.analyze(frame, dict(base, stage_id=code)).stage_gated is False, code
        assert job_analyzer.analyze(frame, dict(base, stage_id=0, gcode_state="IDLE")).stage_gated is True


def test_diff_suppression_stages_are_the_stages_they_are_documented_as():
    # The set was numbered one off from bpm's table for homing and nozzle cleaning (14 and 15).
    assert {parseStage(c) for c in job_analyzer._DIFF_SUPPRESS_STAGES} == {
        "Auto bed leveling", "Changing filament", "Heating hotend", "Homing toolhead",
        "Cleaning nozzle tip", "Calibrating flow", "Filament unloading", "Filament loading",
        "Absolute accuracy pre-check"}

    def diff_weight(code):
        return job_analyzer._spaghetti_weights({"stage_id": code, "progress_pct": 50})[0]["diff"]

    assert diff_weight(13) == 0.0 and diff_weight(14) == 0.0
    assert diff_weight(15) > 0.0 and diff_weight(0) > 0.0 and diff_weight(255) > 0.0


# --------------------------------------------------------------------------- defect 4: bad lookups

def _module_imports(path):
    tree = ast.parse(path.read_text())
    return [(node.lineno, node.col_offset, node.module, [a.name for a in node.names])
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module]


def test_every_import_in_charts_and_camera_resolves():
    # ast.walk reaches imports inside function bodies, which is where these live.
    checked = nested = 0
    unresolved = []
    for rel in ("tools/charts.py", "tools/camera.py"):
        for lineno, col, module, names in _module_imports(REPO / rel):
            checked += 1
            nested += col > 0
            try:
                mod = importlib.import_module(module)
            except ImportError as exc:
                unresolved.append(f"{rel}:{lineno} from {module}: {exc}")
                continue
            for name in names:
                if name == "*" or hasattr(mod, name):
                    continue
                try:
                    importlib.import_module(f"{module}.{name}")
                except ImportError:
                    unresolved.append(f"{rel}:{lineno} from {module} import {name}")
    assert checked >= 20 and nested >= 5, (checked, nested)  # the scan is not vacuous
    assert not unresolved, unresolved


def _spool(slot, ams_id, type_, color, remaining):
    return BambuSpool(id=4 * ams_id + slot, slot_id=slot, ams_id=ams_id, type=type_, color=color,
                      remaining_percent=remaining)


def _ams_svg(spools):
    state = BambuState(spools=spools)
    with _env(state, None):
        return charts_tool._row_ams("camdef-ams")


def _ams_bar_widths(spools):
    """Bar lengths as drawn: the figure is inspected on its way into the SVG renderer."""
    seen = []
    real = charts_tool._svg

    def _spy(fig):
        seen.extend(p.get_width() for p in fig.axes[0].patches)
        return real(fig)

    charts_tool._svg = _spy
    try:
        _ams_svg(spools)
    finally:
        charts_tool._svg = real
    return seen


def test_ams_panel_lists_the_real_spools():
    svg = _ams_svg([_spool(0, 0, "PLA", "#00FF00FF", 80), _spool(1, 0, "PETG", "#0000FFFF", 35)])
    assert "No AMS spool data" not in svg, "panel fell back to the placeholder"
    assert "<!-- 80% -->" in svg and "<!-- 35% -->" in svg, "remaining percentages not drawn"
    assert "#00ff00" in svg and "#0000ff" in svg, "spool colours not drawn"


def test_ams_panel_colours_a_spool_whose_colour_is_a_css_name():
    # get_spool_info reports a colour as a CSS3 name when the RGB matches one exactly.
    svg = _ams_svg([_spool(0, 0, "PLA", "red", 50)])
    assert "<!-- 50% -->" in svg and "#ff0000" in svg, "named colour fell back to grey"
    assert "#454545" not in svg


def test_ams_panel_skips_empty_slots_and_marks_unknown_remaining():
    spools = [_spool(0, 0, "PLA", "#FF0000FF", -1),                 # remain unreported
              _spool(1, 0, "", "00000000", 0),                       # empty slot
              BambuSpool(id=254, slot_id=254, ams_id=-1, type="", color="00000000",
                         remaining_percent=-1)]                      # empty external holder
    svg = _ams_svg(spools)
    assert "No AMS spool data" not in svg, "panel fell back to the placeholder"
    assert "<!-- n/a -->" in svg and "<!-- -1% -->" not in svg, "unknown remaining not marked"
    assert svg.count("<!-- 0% -->") == 0, "an empty slot was drawn as a spool"


def test_ams_panel_never_draws_a_negative_bar():
    # remaining_percent is -1 when the tray reports no 'remain' value.
    widths = _ams_bar_widths([_spool(0, 0, "PLA", "#FF0000FF", -1), _spool(1, 0, "PETG", "#0000FFFF", 50)])
    assert widths == [0.0, 50.0], widths


def test_ams_panel_with_only_empty_slots_shows_the_placeholder():
    svg = _ams_svg([_spool(0, 0, "", "00000000", 0)])
    assert "No AMS spool data" in svg


# ------------------------------------------ same class: a wrong name read through a silent default

def _analysis_context_from_tool(state, job):
    seen = {}
    real = job_analyzer.analyze

    def _spy(frame, printer_context, **kw):
        seen.update(printer_context)
        return real(frame, printer_context, **kw)

    with _env(state, job, jpeg=_jpeg()):
        job_analyzer.analyze = _spy
        try:
            r = camera_tool.analyze_active_job("camdef-ctx")
        finally:
            job_analyzer.analyze = real
            job_analyzer._references.pop("camdef-ctx", None)
    assert "error" not in r, r
    return seen


def test_both_context_builders_carry_series_flow_and_stage_from_the_real_objects():
    nozzle = NozzleCharacteristics(diameter_mm=0.4, flow=NozzleFlowType.TPU_HIGH_FLOW)
    state = BambuState(gcode_state="RUNNING", active_nozzle=nozzle)
    job = _job(0, print_percentage=10)
    with _env(state, job):
        from_monitor = job_monitor._build_context("camdef-ctx", state)
    from_tool = _analysis_context_from_tool(state, job)
    for ctx in (from_monitor, from_tool):
        assert ctx["printer_series"] == "H2", ctx["printer_series"]
        assert ctx["nozzle_flow_type"] == "TPU_HIGH_FLOW", ctx["nozzle_flow_type"]
        assert ctx["stage_id"] == 0, ctx["stage_id"]
        assert ctx["nozzle_diameter_mm"] == 0.4


# --------------------------------------------------------------------------- defect 6: NameError

def test_failure_probability_error_returns_an_error_dict():
    real = job_analyzer.compute_failure_probability

    def _boom(*a, **k):
        raise RuntimeError("fp exploded")

    state = BambuState(gcode_state="RUNNING")
    with _env(state, _job(0), jpeg=_jpeg()):
        job_analyzer.compute_failure_probability = _boom
        try:
            r = camera_tool.analyze_active_job("camdef-fp")
        finally:
            job_analyzer.compute_failure_probability = real
            job_analyzer._references.pop("camdef-fp", None)
    assert isinstance(r, dict) and r.get("error") == "analysis_failed", r
    assert "fp exploded" in r.get("detail", ""), r


def test_analyze_active_job_success_path_still_reports_factors():
    state = BambuState(gcode_state="RUNNING")
    with _env(state, _job(0), jpeg=_jpeg()):
        r = camera_tool.analyze_active_job("camdef-ok")
        job_analyzer._references.pop("camdef-ok", None)
    assert "error" not in r, r
    assert isinstance(r["factor_contributions"], dict) and r["factor_contributions"], r
    assert isinstance(r["success_probability"], float) and isinstance(r["decision_confidence"], float)


def test_decision_confidence_failure_alone_still_degrades_to_null():
    # Documented behaviour that the fix must keep: only the confidence figure is lost.
    real = job_analyzer.compute_decision_confidence
    calls = []

    def _second_call_raises(*a, **k):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("confidence exploded")
        return real(*a, **k)

    state = BambuState(gcode_state="RUNNING")
    with _env(state, _job(0), jpeg=_jpeg()):
        job_analyzer.compute_decision_confidence = _second_call_raises
        try:
            r = camera_tool.analyze_active_job("camdef-dc")
        finally:
            job_analyzer.compute_decision_confidence = real
            job_analyzer._references.pop("camdef-dc", None)
    assert len(calls) == 2, calls
    assert "error" not in r and r["decision_confidence"] is None, r
    assert isinstance(r["factor_contributions"], dict), r
    assert isinstance(r["success_probability"], float), r


# --------------------------------------------------------------------------- defect 7: null score

def test_open_job_state_reports_the_monitors_anomaly_score():
    name = "camdef-open"
    state = BambuState(gcode_state="RUNNING")
    paths = []
    with _env(state, _job(0), jpeg=_jpeg()):
        mon = job_monitor._PrinterMonitor(name)
        mon._tick()
        cached = mon.get_latest_result()
        job_analyzer._references.pop(name, None)
        assert cached is not None and cached["stage_gated"] is False, cached
        job_monitor._monitors[name] = mon
        try:
            with mock.patch("subprocess.Popen") as popen:
                r = camera_tool.open_job_state(name)
            paths = list(r.get("paths", []))
        finally:
            job_monitor._monitors.pop(name, None)
            for label in ("composite", "annotated", "health", "raw"):
                Path(f"/tmp/bambu_job_state_{name}_{label}.png").unlink(missing_ok=True)
    assert "error" not in r, r
    assert popen.called and paths, r
    assert cached["anomaly_score"] is not None
    assert r["score"] == cached["anomaly_score"], (r["score"], cached["anomaly_score"])


# --------------------------------------------------------------------------- stage text (resume_print)

def _stage_code(name):
    codes = [c for c in range(-1, 256) if parseStage(c) == name]
    assert len(codes) == 1, (name, codes)
    return codes[0]


_STAGE_PHRASES = {"user": "Paused by user", "M400": "M400 pause", "runout": "Filament runout pause"}


def _stage_claims(doc):
    """Every ``stg_cur=N`` in ``doc`` paired with the stage phrase closest before it. A number with
    no recognisable phrase ahead of it is returned under None so the caller fails on it."""
    text = " ".join(doc.split())
    claims = []
    for m in re.finditer(r"stg_cur=(\d+)", text):
        ctx = text[max(0, m.start() - 45):m.start()].lower()
        key = max(_STAGE_PHRASES, key=lambda k: ctx.rfind(k.lower()))
        claims.append((key if key.lower() in ctx else None, int(m.group(1))))
    return claims


def test_resume_print_stage_codes_are_the_ones_parseStage_names():
    claims = _stage_claims(print_control_tool.resume_print.__doc__)
    assert claims, "resume_print documents no stg_cur codes"
    wrong = [(key, n) for key, n in claims
             if key is None or parseStage(n) != _STAGE_PHRASES[key]]
    assert not wrong, (
        f"resume_print numbers pause stages differently from bpm parseStage: {wrong}; "
        f"parseStage says {[(k, _stage_code(v)) for k, v in _STAGE_PHRASES.items()]}")
    assert {k for k, _ in claims} == set(_STAGE_PHRASES), claims


def test_no_tool_docstring_in_print_control_or_notifications_carries_a_wrong_stage_number():
    bad = []
    for mod in (print_control_tool, notifications_tool):
        for name, fn in vars(mod).items():
            doc = getattr(fn, "__doc__", None) if callable(fn) else None
            if not doc or getattr(fn, "__module__", None) != mod.__name__:
                continue
            for key, n in _stage_claims(doc):
                if key is None or parseStage(n) != _STAGE_PHRASES[key]:
                    bad.append((mod.__name__, name, key, n))
    assert not bad, bad


def test_resume_print_points_at_a_stage_table_that_exists():
    doc = print_control_tool.resume_print.__doc__
    assert "get_job_info" in doc, "resume_print no longer says where the full stage table lives"
    table = state_mod.get_job_info.__doc__
    for code in (_stage_code(v) for v in _STAGE_PHRASES.values()):
        assert f"{code}={parseStage(code)}" in table, (code, parseStage(code))


# --------------------------------------------------------------------------- stream HUD (mjpeg_server)

_HUD_HARNESS = r"""
const vm = require('vm'), fs = require('fs');
const script = fs.readFileSync(process.argv[2], 'utf8');
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
const els = {};
function noop() { return undefined; }
function deep() {   // canvas context: any property is callable and returns another stub
  return new Proxy(function () {}, { get: (t, p) => p === 'then' ? undefined : deep(),
                                     apply: () => deep(), set: () => true });
}
function makeEl(id) {
  const classes = new Set(['hidden']);
  const store = { id, textContent: '', innerHTML: '', className: '', style: {}, children: [] };
  store.classList = { add: c => classes.add(c), remove: c => classes.delete(c),
                      contains: c => classes.has(c), toggle: c => classes.has(c) ? classes.delete(c) : classes.add(c) };
  store._classes = classes;
  store.getContext = () => deep();
  return new Proxy(store, { get(t, p) { return p in t ? t[p] : (p === 'then' ? undefined : noop); },
                            set(t, p, v) { t[p] = v; return true; } });
}
const document = {
  getElementById: id => els[id] || (els[id] = makeEl(id)),
  querySelector: () => makeEl('q'), querySelectorAll: () => [], createElement: () => makeEl('c'),
  addEventListener: noop, body: makeEl('body'), documentElement: makeEl('html'),
};
const ctx = vm.createContext({
  document, window: { open: noop, addEventListener: noop, innerWidth: 800, innerHeight: 600 },
  fetch: () => new Promise(() => {}), setInterval: noop, setTimeout: noop, clearTimeout: noop,
  clearInterval: noop, requestAnimationFrame: noop,
  localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
  location: { search: '', origin: 'http://x' }, console, Date, Math, JSON, parseInt, parseFloat, isNaN,
  URLSearchParams, AbortController, TextDecoder, Uint8Array, Promise, navigator: { userAgent: 'test' },
  Image: function () {}, performance: { now: () => 0 },
});
vm.runInContext(script, ctx);
const out = [];
for (const c of cases) {
  for (const k of Object.keys(els)) delete els[k];
  vm.runInContext(c.fn + '(' + JSON.stringify(c.arg) + ')', ctx);
  const snap = {};
  for (const id of c.watch) {
    const e = document.getElementById(id);
    snap[id] = { text: e.textContent, html: e.innerHTML, hidden: e._classes.has('hidden') };
  }
  out.push(snap);
}
console.log(JSON.stringify(out));
"""


def _hud_script():
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", mjpeg_server._HTML_PAGE, re.S)
    assert len(scripts) == 1, len(scripts)
    return scripts[0]


def _run_hud(cases):
    """Run the real served HUD script under node with a stub DOM; call ``fn(arg)`` per case and return
    the watched elements' text, innerHTML and hidden-class state. None when node is not installed."""
    node = shutil.which("node")
    if node is None:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="hud-"))
    try:
        (tmp / "hud.js").write_text(_hud_script(), encoding="utf-8")
        (tmp / "harness.js").write_text(_HUD_HARNESS, encoding="utf-8")
        proc = subprocess.run([node, str(tmp / "harness.js"), str(tmp / "hud.js")],
                              input=json.dumps(cases), capture_output=True, text=True, timeout=60)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert proc.returncode == 0, proc.stderr[-800:]
    return json.loads(proc.stdout)


def _skip_without_node(name):
    if shutil.which("node") is None:
        print(f"SKIP {name}: node is not installed, the HUD script was not executed")
        return True
    return False


def _status(stage_name, hms=None):
    return {"fn": "update", "watch": ["stage-row", "errors"],
            "arg": {"gcode_state": "RUNNING", "stage_name": stage_name, "hms_errors": hms or []}}


def test_hud_hms_tooltip_reads_the_msg_field_in_both_branches_text():
    # bpm HMS entries (and tools.camera._build_status, which feeds /status) carry {code, msg, url}.
    js = _hud_script()
    assert js.count("""title="'+escAttr(e.msg)+'\"""") == 2, js.count("e.msg")
    assert "e.description" not in js


def test_hud_hms_tooltip_shows_the_message_for_linked_and_unlinked_entries():
    if _skip_without_node("test_hud_hms_tooltip_shows_the_message_for_linked_and_unlinked_entries"):
        return
    hms = [{"code": "0700_1", "msg": "AMS filament may be tangled", "url": "http://wiki.invalid/x"},
           {"code": "0300_2", "msg": "Nozzle temperature abnormal", "url": ""}]
    (snap,) = _run_hud([_status("", hms)])
    html = snap["errors"]["html"]
    assert 'title="AMS filament may be tangled"' in html, html
    assert 'title="Nozzle temperature abnormal"' in html, html
    assert not snap["errors"]["hidden"], snap


def _catalogue_message_with_a_double_quote():
    from bpm.bambutools import HMS_STATUS
    for kind in ("device_error", "device_hms"):
        for entry in HMS_STATUS["data"][kind]["en"]:
            if '"' in (entry.get("intro") or ""):
                return entry["intro"]
    raise AssertionError("bpm's HMS catalogue has no message containing a double quote")


def test_hud_hms_tooltip_survives_catalogue_text_with_quotes_and_markup():
    # 135 of bpm's 6204 HMS messages contain a double quote ('please select "Resume" to retry'), which
    # would close an unescaped title="..." attribute at the first quote.
    if _skip_without_node("test_hud_hms_tooltip_survives_catalogue_text_with_quotes_and_markup"):
        return
    real = _catalogue_message_with_a_double_quote()
    hard = 'a "quoted" <b>&</b> word'
    hms = [{"code": "C1", "msg": real, "url": "http://wiki.invalid/x"}, {"code": "C2", "msg": hard, "url": ""}]
    (snap,) = _run_hud([_status("", hms)])
    html = snap["errors"]["html"]
    esc = lambda t: t.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")  # noqa: E731
    assert f'title="{esc(real)}"' in html, html
    assert f'title="{esc(hard)}"' in html, html
    assert html.count("<a ") == 1 and html.count("<span ") == 1 and "<b>" not in html, html


def test_hud_stage_row_literals_are_bpms_names_text():
    js = _hud_script()
    assert f"sn!=='{parseStage(100)}'&&sn!=='{parseStage(255)}'" in js, "stage row is not keyed on bpm's names"
    assert "Printing normally" not in js


def test_hud_stage_row_hides_for_no_stage_printing_and_completed_and_shows_every_activity():
    if _skip_without_node("test_hud_stage_row_hides_for_no_stage_printing_and_completed_and_shows_every_activity"):
        return
    names = sorted({parseStage(c) for c in range(-1, 256)})
    snaps = _run_hud([_status(n) for n in names])
    quiet = {parseStage(c) for c in QUIET}
    assert quiet == {"", "Printing", "Completed"}, quiet
    wrong_shown = [n for n, s in zip(names, snaps) if n in quiet and not s["stage-row"]["hidden"]]
    wrong_hidden = [n for n, s in zip(names, snaps)
                    if n not in quiet and not n.startswith("Stage [") and s["stage-row"]["hidden"]]
    assert not wrong_shown, f"stage row shown for a stage that is not an activity: {wrong_shown}"
    assert not wrong_hidden, f"stage row hidden for an activity: {wrong_hidden}"
    assert len(names) > 60, len(names)


def test_hud_standby_verdict_follows_the_monitors_stage_gate():
    # /job_state stores stage 255 while printing and the activity code while the gate holds analysis
    # back, with stage_gated saying which. The HUD's own test (stage !== 255) must agree with the flag
    # for every result the monitor can store, so drive it with the monitor's own results.
    if _skip_without_node("test_hud_standby_verdict_follows_the_monitors_stage_gate"):
        return
    printing, _ = _tick(0, jpeg=_jpeg())
    assert printing["stage_gated"] is False and printing["stage"] == 255, printing
    results = [printing]
    for code in (1, 5, 16, 17, 70, 77):
        gated, _n = _tick(code)
        assert gated["stage_gated"] is True and gated["stage"] == code, gated
        results.append(gated)
    cases = [{"fn": "hpUpdateFromResult", "watch": ["hp-verdict"],
              "arg": {"stage": r["stage"], "success_probability": 0.95, "decision_confidence": 0.95}}
             for r in results]
    snaps = _run_hud(cases)
    for r, snap in zip(results, snaps):
        standby = snap["hp-verdict"]["text"].lower() == "standby"
        assert standby == r["stage_gated"], (r["stage"], r["stage_gated"], snap)


# --------------------------------------------------------------------------- URL builders

_AWKWARD = "Bay A&B #1+x y%z"        # "&", "#", "+", a space and a literal "%"; no "/" (a path here)
_STREAM = {"url": "http://localhost:1234/", "port": 1234, "protocol": "tcp_tls"}


@contextlib.contextmanager
def _view_stream_env():
    """Stub the server start, the tab lookup and the browser launch; yield (opened_urls, start_calls)."""
    opened, starts = [], []

    def _start(n):
        starts.append(n)
        return dict(_STREAM)

    with mock.patch.object(camera_tool, "start_stream", _start), \
            mock.patch.object(camera_tool, "_focus_existing_tab", lambda u: False), \
            mock.patch("webbrowser.open", lambda u: opened.append(u) or True):
        yield opened, starts


def _query(url):
    parts = urllib.parse.urlsplit(url)
    return parts, urllib.parse.parse_qs(parts.query, keep_blank_values=True)


def test_view_stream_urls_for_an_ordinary_name_are_unchanged():
    with _view_stream_env() as (opened, _):
        default = camera_tool.view_stream("h2d")
        custom = camera_tool.view_stream("h2d-1.a_b~c", "720p", 75)
    assert default["url"] == "http://localhost:1234/", default
    assert opened[0] == "http://localhost:1234/open?name=bambu-h2d", opened
    assert custom["url"] == "http://localhost:1234/?resolution=720p&quality=75", custom
    assert opened[1] == "http://localhost:1234/open?name=bambu-h2d-1.a_b~c&resolution=720p&quality=75", opened


def test_view_stream_portal_url_round_trips_an_awkward_printer_name():
    with _view_stream_env() as (opened, _):
        camera_tool.view_stream(_AWKWARD, "480p", 60)
    parts, q = _query(opened[0])
    assert parts.path == "/open" and parts.fragment == "", opened[0]
    assert q == {"name": [f"bambu-{_AWKWARD}"], "resolution": ["480p"], "quality": ["60"]}, (opened[0], q)


def test_view_stream_rejects_an_unknown_resolution_before_starting_anything():
    bad = ["720p&quality=1#x", "4k", "", "NATIVE", "720p "]
    with _view_stream_env() as (opened, starts):
        for value in bad:
            r = camera_tool.view_stream("h2d", value, 75)
            assert r.get("error") == "invalid_resolution", (value, r)
            assert all(k in r["detail"] for k in camera_tool._RESOLUTION_MAP), r
    assert not starts and not opened, (starts, opened)


def test_view_stream_accepts_every_documented_resolution():
    with _view_stream_env() as (opened, _):
        for res in camera_tool._RESOLUTION_MAP:
            r = camera_tool.view_stream("h2d", res, 70)
            assert "error" not in r, (res, r)
            assert _query(r["url"])[1]["resolution"] == [res], r
    assert len(opened) == len(camera_tool._RESOLUTION_MAP), opened


def test_view_stream_escapes_quality_in_the_client_and_portal_urls():
    with _view_stream_env() as (opened, _):
        r = camera_tool.view_stream("h2d", "720p", "50&name=evil#x")
    for url in (r["url"], opened[0]):
        parts, q = _query(url)
        assert parts.fragment == "" and q["quality"] == ["50&name=evil#x"], (url, q)
        assert q["resolution"] == ["720p"] and set(q) <= {"resolution", "quality", "name"}, q
    assert _query(opened[0])[1]["name"] == ["bambu-h2d"], opened


def test_view_stream_escapes_resolution_independently_of_the_validation():
    # The validation makes every reachable resolution URL-safe, so the encoding is only observable by
    # widening the accepted set: a value the resizer honours that needs escaping must still round-trip.
    awkward = "a&b#c d"
    with mock.patch.dict(camera_tool._RESOLUTION_MAP, {awkward: None}), _view_stream_env() as (opened, _):
        r = camera_tool.view_stream("h2d", awkward, 70)
    for url in (r["url"], opened[0]):
        parts, q = _query(url)
        assert parts.fragment == "" and q["resolution"] == [awkward] and q["quality"] == ["70"], (url, q)


@contextlib.contextmanager
def _open_charts_env(api_port):
    opened = []
    with mock.patch.object(charts_tool, "render_charts_html", lambda n: "<html>charts</html>"), \
            mock.patch("tools.system.get_server_info", lambda: {"api_port": api_port}), \
            mock.patch("tools.camera._focus_existing_tab", lambda u: False), \
            mock.patch("webbrowser.open", lambda u: opened.append(u) or True):
        yield opened


def _open_charts(name, api_port):
    out = Path(f"/tmp/bambu-charts-{name}.html")
    existed = out.exists()
    try:
        with _open_charts_env(api_port) as opened:
            r = charts_tool.open_charts(name)
        return r, opened
    finally:
        if not existed:
            out.unlink(missing_ok=True)


def test_open_charts_url_for_an_ordinary_name_is_unchanged():
    r, opened = _open_charts("camdef-h2d", 4321)
    assert opened == ["http://localhost:4321/api/charts?printer=camdef-h2d"], opened
    assert r["output_path"] == "/tmp/bambu-charts-camdef-h2d.html" and r["opened"] is True, r


def test_open_charts_url_round_trips_an_awkward_printer_name():
    name = "camdef " + _AWKWARD
    r, opened = _open_charts(name, 4321)
    parts, q = _query(opened[0])
    assert (parts.scheme, parts.netloc, parts.path) == ("http", "localhost:4321", "/api/charts"), opened[0]
    assert parts.fragment == "" and q == {"printer": [name]}, (opened[0], q)


def test_open_charts_file_fallback_url_reaches_the_written_file_for_an_awkward_name():
    name = "camdef " + _AWKWARD
    r, opened = _open_charts(name, 0)
    parts = urllib.parse.urlsplit(opened[0])
    assert parts.scheme == "file" and parts.fragment == "" and parts.query == "", opened[0]
    assert urllib.parse.unquote(parts.path) == r["output_path"], (opened[0], r)
    ordinary, ordinary_opened = _open_charts("camdef-h2d", 0)
    assert ordinary_opened == ["file:///tmp/bambu-charts-camdef-h2d.html"], ordinary_opened


# --------------------------------------------------------------------------- stream server handlers

class _RecordingServer(mjpeg_server._MJPEGHTTPServer):
    """The real stream server, plus a list of the exceptions its handler threads raised (socketserver
    would print them and drop the connection, which a test cannot otherwise tell from a slow reply)."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.handler_errors = []

    def handle_error(self, request, client_address):
        self.handler_errors.append(sys.exc_info()[1])


def _one_frame():
    yield b"\xff\xd8camdef-frame"


@contextlib.contextmanager
def _stream_server(printer_name=""):
    """A real MJPEG server on an ephemeral loopback port, served by a real thread."""
    srv = _RecordingServer(("127.0.0.1", 0), mjpeg_server._StreamHandler, frame_factory=_one_frame,
                           printer_name=printer_name)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(5)


def _http_get(srv, path):
    """(status, content_type, body). status is None when the handler dropped the connection."""
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.getheader("Content-Type"), resp.read()
    except (http.client.HTTPException, ConnectionError) as exc:
        return None, None, repr(exc).encode()
    finally:
        conn.close()


def _qs(**params):
    return urllib.parse.urlencode(params, quote_via=urllib.parse.quote)


_STREAM_PATHS = ("/open", "/", "/stream")

_CRAFTED_RESOLUTIONS = ["720p');alert(1);//", "x'+alert(document.domain)+'", "</script><script>alert(1)</script>",
                        "720p&quality=1", "720p\\", "NATIVE", "4k", "720p "]

_BAD_QUALITIES = ["abc", "12.5", "0", "101", "-5", "1e2", "5_0", "9" * 5000, "٥٠"]


def test_stream_pages_reject_a_crafted_resolution_and_never_serve_it():
    # _serve_open put the request's resolution inside a single-quoted JS string (window.open and
    # location.replace), so a value carrying a quote ran as script in the opened page.
    failures = []
    with _stream_server() as srv:
        for path in _STREAM_PATHS:
            for value in _CRAFTED_RESOLUTIONS:
                status, ctype, body = _http_get(srv, f"{path}?{_qs(resolution=value)}")
                text = body.decode("utf-8", "replace")
                ok = (status == 400 and ctype == "application/json"
                      and json.loads(text).get("error") == "invalid_resolution"
                      and "alert" not in text and "detail" in json.loads(text))
                if not ok:
                    failures.append((path, value, status, text[:120]))
        assert not srv.handler_errors, srv.handler_errors
    assert not failures, f"{len(failures)} crafted resolutions not rejected with a 400 body, e.g. {failures[:2]}"


def test_stream_pages_accept_every_documented_resolution():
    # The control for the rejection above: the accepted set is tools.camera._RESOLUTION_MAP itself.
    assert len(camera_tool._RESOLUTION_MAP) >= 6
    with _stream_server() as srv:
        for res in camera_tool._RESOLUTION_MAP:
            for path in _STREAM_PATHS:
                status, _c, body = _http_get(srv, f"{path}?{_qs(resolution=res, quality=50)}")
                assert status == 200, (path, res, status, body[:120])
        assert not srv.handler_errors, srv.handler_errors


def test_stream_pages_answer_a_bad_quality_with_400_not_an_exception():
    # int(params['quality'][0]) raised ValueError in the handler thread, which dropped the connection
    # with no response. 5_0 and non-ASCII digits are integers to int() but not to a URL parameter.
    failures = []
    with _stream_server() as srv:
        for path in _STREAM_PATHS:
            for value in _BAD_QUALITIES:
                status, ctype, body = _http_get(srv, f"{path}?{_qs(quality=value)}")
                ok = (status == 400 and ctype == "application/json"
                      and json.loads(body).get("error") == "invalid_quality")
                if not ok:
                    failures.append((path, value[:12], status, body[:80]))
        assert not srv.handler_errors, f"handler thread raised: {srv.handler_errors[:1]}"
    assert not failures, f"{len(failures)} bad qualities not answered with a 400 body, e.g. {failures[:3]}"


def test_stream_pages_accept_the_quality_range_edges():
    with _stream_server() as srv:
        for value in ("1", "50", "050", "85", "100"):
            for path in _STREAM_PATHS:
                status, _c, body = _http_get(srv, f"{path}?{_qs(quality=value)}")
                assert status == 200, (path, value, status, body[:120])
        status, _c, body = _http_get(srv, "/stream")
        assert status == 200 and b"--frame" in body and b"camdef-frame" in body, (status, body[:80])


def test_served_page_escapes_the_printer_name_in_the_title():
    # A name like '<b>&"x' went into <title> raw. The byte comparison with _HTML_PAGE is what the
    # browser receives: nothing but the title differs, so every JS backslash escape is intact.
    with _stream_server("<b>&\"x") as srv:
        status, ctype, body = _http_get(srv, "/")
    expected = "<title>Bambu Cam — &lt;b&gt;&amp;&quot;x</title>"
    assert status == 200 and ctype == "text/html; charset=utf-8", (status, ctype)
    assert expected.encode() in body, body[:400]
    assert body == mjpeg_server._HTML_PAGE.replace("<title>Bambu Cam</title>", expected, 1).encode(), \
        "served page differs from the template by more than the title"
    hostile = "</title><script>alert(1)</script>it's"
    with _stream_server(hostile) as srv:
        _s, _c, body = _http_get(srv, "/")
    page = body.decode()
    head = page.split("<style>", 1)[0]
    assert ("<title>Bambu Cam \u2014 &lt;/title&gt;&lt;script&gt;alert(1)&lt;/script&gt;it&#x27;s</title>" in head
            and page.count("<script") == 1), head
    with _stream_server("") as srv:
        _s, _c, body = _http_get(srv, "/")
    assert body == mjpeg_server._HTML_PAGE.encode(), "unnamed printer: page must be served unchanged"


_OPEN_HARNESS = r"""
const vm = require('vm'), fs = require('fs');
const script = fs.readFileSync(process.argv[2], 'utf8');
const calls = { open: [], replace: [] };
const ctx = vm.createContext({
  URLSearchParams, setTimeout: () => {},
  window: { open: (u, n) => { calls.open.push([u, n]); return process.argv[4] === 'popup-blocked' ? null : { focus() {} }; },
            close() {} },
  location: { origin: 'http://h:1', search: process.argv[3], replace: u => calls.replace.push(u) },
});
vm.runInContext(script, ctx);
console.log(JSON.stringify(calls));
"""


def _run_open_page(body, search, mode="popup-ok"):
    """Execute the served /open page's script under node; None when node is not installed."""
    node = shutil.which("node")
    if node is None:
        return None
    scripts = re.findall(r"<script>(.*?)</script>", body.decode(), re.S)
    assert len(scripts) == 1, len(scripts)
    tmp = Path(tempfile.mkdtemp(prefix="open-"))
    try:
        (tmp / "page.js").write_text(scripts[0], encoding="utf-8")
        (tmp / "harness.js").write_text(_OPEN_HARNESS, encoding="utf-8")
        proc = subprocess.run([node, str(tmp / "harness.js"), str(tmp / "page.js"), search, mode],
                              capture_output=True, text=True, timeout=60)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert proc.returncode == 0, proc.stderr[-800:]
    return json.loads(proc.stdout)


def test_open_portal_forwards_a_valid_resolution_and_quality_to_the_stream_page():
    with _stream_server() as srv:
        _s, _c, plain = _http_get(srv, "/open?name=bambu-h2d")
        _s, _c, custom = _http_get(srv, f"/open?{_qs(name='bambu-h2d', resolution='720p', quality=50)}")
    assert b"location.origin+'/'," in plain and b"location.replace('/')" in plain, plain
    assert b"location.origin+'/?resolution=720p&quality=50'," in custom, custom
    assert b"location.replace('/?resolution=720p&quality=50')" in custom, custom
    if _skip_without_node("test_open_portal_forwards_a_valid_resolution_and_quality_to_the_stream_page"):
        return
    calls = _run_open_page(custom, "?name=bambu-h2d")
    assert calls == {"open": [["http://h:1/?resolution=720p&quality=50", "bambu-h2d"]], "replace": []}, calls
    blocked = _run_open_page(custom, "?name=bambu-h2d", "popup-blocked")
    assert blocked["replace"] == ["/?resolution=720p&quality=50"], blocked
    assert _run_open_page(plain, "?name=bambu-h2d")["open"] == [["http://h:1/", "bambu-h2d"]]


def test_open_portal_escapes_whatever_it_interpolates_independently_of_the_validation():
    # The validation makes every reachable resolution safe, so the escaping is only observable by
    # widening the accepted set with a value that would break out of a JS string and out of <script>.
    awkward = "a'b\"c</script><!--d\\e f"
    with mock.patch.dict(camera_tool._RESOLUTION_MAP, {awkward: None}), _stream_server() as srv:
        status, _c, body = _http_get(srv, f"/open?{_qs(name='bambu-h2d', resolution=awkward, quality=50)}")
    assert status == 200, status
    text = body.decode()
    assert text.count("</script>") == 1 and text.count("<script>") == 1 and "<!--" not in text, text
    assert " " not in text, "a raw U+2028 ends a JS string literal in older engines"
    if _skip_without_node("test_open_portal_escapes_whatever_it_interpolates_independently_of_the_validation"):
        return
    want = "/?" + urllib.parse.urlencode({"resolution": awkward, "quality": 50})
    calls = _run_open_page(body, "?name=bambu-h2d")
    assert calls == {"open": [["http://h:1" + want, "bambu-h2d"]], "replace": []}, calls
    assert _run_open_page(body, "?name=bambu-h2d", "popup-blocked")["replace"] == [want]


_JS_STR_CASES = ["plain", "a'b", 'a"b', "a\\b", "</script>", "<!--x", "a\nb\rc", "\u2028\u2029", "\x00\x1f\x7f",
                 "caf\u00e9 \u2713", "'; alert(1); //", "\\'", "\\\\'"]


def test_js_str_makes_a_literal_that_round_trips_and_cannot_leave_the_script_block():
    # /open builds a URL-encoded path, so no reachable value needs this second layer today; it is
    # tested on its own so the literal stays correct for whatever is embedded next.
    for value in _JS_STR_CASES:
        lit = mjpeg_server._js_str(value)
        inner = re.sub(r"\\.", "", lit[1:-1], flags=re.S)          # drop each escape pair
        assert lit[0] == lit[-1] == "'" and "'" not in inner, (value, lit)
        assert "<" not in lit and "\n" not in lit and "\r" not in lit and "\u2028" not in lit and "\u2029" not in lit, (value, lit)
        assert all(ch >= " " for ch in lit), (value, lit)
    node = shutil.which("node")
    if node is None:
        print("SKIP test_js_str_makes_a_literal_that_round_trips_and_cannot_leave_the_script_block: "
              "node is not installed, the literals were not evaluated")
        return
    lits = [mjpeg_server._js_str(v) for v in _JS_STR_CASES]
    proc = subprocess.run([node, "-e", "const vm=require('vm');const l=JSON.parse(require('fs').readFileSync(0,'utf8'));"
                                       "console.log(JSON.stringify(l.map(x=>vm.runInNewContext(x))))"],
                          input=json.dumps(lits), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-800:]
    assert json.loads(proc.stdout) == _JS_STR_CASES, (lits, proc.stdout)


def test_hud_escapes_the_hms_link_and_the_code_label():
    # e.url went into href="..." and e.code into element content unescaped, the way e.msg was before
    # it went through escAttr.
    js = _hud_script()
    assert js.count("""href="'+escAttr(e.url)+'\"""") == 1, "hms link href is not escaped"
    assert js.count("escAttr(e.code||'error')") == 1, "hms code label is not escaped"
    if _skip_without_node("test_hud_hms_link_and_code_label_are_escaped"):
        return
    hms = [{"code": "<b>x", "msg": "m", "url": 'http://h/?a=1&b=" onmouseover="alert(1)'},
           {"code": "<i>y&z", "msg": "n", "url": ""}]
    (snap,) = _run_hud([_status("", hms)])
    html = snap["errors"]["html"]
    assert 'href="http://h/?a=1&amp;b=&quot; onmouseover=&quot;alert(1)"' in html, html
    assert "&lt;b&gt;x</a>" in html and "&lt;i&gt;y&amp;z</span>" in html, html
    assert "<b>" not in html and "<i>" not in html and 'onmouseover="' not in html, html
    assert html.count("<a ") == 1 and html.count("<span ") == 1, html


# Telemetry text reaches the HUD through /status and /job_state: filament type and colour, gcode_state,
# layer, nozzle temperatures. bpm falls back to "#" + the raw tray_color when it cannot name a colour, so
# printer-supplied text arrives with a leading "#". Every value that goes into innerHTML or an attribute
# is escaped (escAttr), forced to a number (Number) or checked against an allow-list. The tests feed the
# HUD script directly, so they hold even when build_active_filament has already cleaned the payload.

def _esc(t):
    return t.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _swatch(colour):
    return ('<span style="display:inline-block;width:10px;height:10px;background:' + colour +
            ';border-radius:2px;vertical-align:middle;margin-right:4px;border:1px solid rgba(255,255,255,.2)"></span>')


def _filament_htmls(filaments):
    cases = [{"fn": "update", "watch": ["filament-row"],
              "arg": {"gcode_state": "RUNNING", "active_filament": f}} for f in filaments]
    return [snap["filament-row"]["html"] for snap in _run_hud(cases)]


_XSS_COLOURS = ['" onmouseover="alert(2)', '"><img src=x onerror=alert(1)>', "#1a2b3c\"><img src=x onerror=alert(1)>",
                "#1a2b3c;background:url(javascript:alert(1))", "red;background:url(javascript:alert(1))",
                "#12345", "#1234567", "notacolor"]
_XSS_TYPES = ["<img src=x onerror=alert(1)>", '"><script>alert(1)</script>', "PLA</span><img src=x onerror=alert(1)>",
              'a "quoted" <b>&</b> word']


def test_hud_filament_row_renders_ordinary_values_unchanged():
    if _skip_without_node("test_hud_filament_row_renders_ordinary_values_unchanged"):
        return
    got = _filament_htmls([{"type": "PLA", "color": "#1a2b3c", "remaining_pct": 54},
                           {"type": "ABS", "color": "1a2b3c", "remaining_pct": 0},
                           {"type": "PETG", "color": "#1a2b3cff", "remaining_pct": 7},
                           {"type": "TPU", "color": "", "remaining_pct": 100},
                           {"type": "", "color": "#FF0000", "remaining_pct": 0}])
    want = [_swatch("#1a2b3c") + '<span class="val">PLA</span> <span class="dim">54%</span>',
            _swatch("#1a2b3c") + '<span class="val">ABS</span>',
            _swatch("#1a2b3cff") + '<span class="val">PETG</span> <span class="dim">7%</span>',
            _swatch("#888") + '<span class="val">TPU</span> <span class="dim">100%</span>',
            _swatch("#FF0000") + '<span class="val">\u2014</span>']
    assert got == want, got


def test_hud_filament_colour_cannot_leave_the_style_attribute():
    if _skip_without_node("test_hud_filament_colour_cannot_leave_the_style_attribute"):
        return
    got = _filament_htmls([{"type": "PLA", "color": c, "remaining_pct": 0} for c in _XSS_COLOURS])
    want = _swatch("#888") + '<span class="val">PLA</span>'
    bad = [(c, h) for c, h in zip(_XSS_COLOURS, got) if h != want]
    assert not bad, f"a hostile colour changed the swatch markup: {bad}"


def test_hud_filament_type_is_escaped_into_the_row():
    if _skip_without_node("test_hud_filament_type_is_escaped_into_the_row"):
        return
    got = _filament_htmls([{"type": t, "color": "#1a2b3c", "remaining_pct": 0} for t in _XSS_TYPES])
    for t, h in zip(_XSS_TYPES, got):
        assert h == _swatch("#1a2b3c") + '<span class="val">' + _esc(t) + '</span>', (t, h)
        assert "<img" not in h and "<script" not in h and "<b>" not in h, (t, h)


def test_hud_nozzle_temperature_is_numeric_and_ordinary_values_are_unchanged():
    if _skip_without_node("test_hud_nozzle_temperature_is_numeric_and_ordinary_values_are_unchanged"):
        return
    hostile = "<img src=x onerror=alert(1)>"
    cases = [{"fn": "update", "watch": ["nozzles", "fans", "humidity-row"],
              "arg": {"gcode_state": "RUNNING",
                      "nozzles": [{"id": 0, "temp": 215.3, "target": 220}],
                      "part_cooling_pct": 100, "aux_pct": 0, "ams_humidity_index": 2}},
             {"fn": "update", "watch": ["nozzles", "fans"],
              "arg": {"gcode_state": "RUNNING",
                      "nozzles": [{"id": 0, "temp": hostile, "target": 0}, {"id": 1, "temp": 1, "target": hostile}],
                      "part_cooling_pct": hostile, "aux_pct": hostile}}]
    ordinary, hostile_snap = _run_hud(cases)
    assert ordinary["nozzles"]["html"] == ('<div class="row"><span class="lbl">Nozzle</span>'
                                           '<span class="ok">215.3\u00b0C / 220\u00b0C</span></div>'), ordinary
    assert ordinary["fans"]["html"] == '<div class="row"><span class="lbl">Part</span><span class="val">100%</span></div>', ordinary
    assert ordinary["humidity-row"]["html"] == '<span style="color:#ffcc40">&#x1F4A7; Humid 2/5</span>', ordinary
    for el in ("nozzles", "fans"):
        assert "<img" not in hostile_snap[el]["html"], hostile_snap[el]


def test_hud_trend_status_renders_ordinary_values_unchanged():
    if _skip_without_node("test_hud_trend_status_renders_ordinary_values_unchanged"):
        return
    base = {"stage": 255, "success_probability": 0.9, "decision_confidence": 0.9}
    cases = [{"fn": "hpUpdateFromResult", "watch": ["hp-trend-status"], "arg": dict(base, **extra)}
             for extra in ({"gcode_state": "RUNNING", "layer": 54, "total_layers": 300},
                           {"gcode_state": "FAILED"},
                           {"gcode_state": "PAUSE", "layer": 3, "total_layers": 9, "ams_humidity": 4})]
    got = [s["hp-trend-status"]["html"] for s in _run_hud(cases)]
    assert got == ['<span style="color:#40d0c0">RUNNING</span> &nbsp;Layer: 54/300',
                   '<span style="color:#ff5050">FAILED</span>',
                   '<span style="color:#f0c040">PAUSE</span> &nbsp;Layer: 3/9 &nbsp;AMS: 40% (Dry)'], got


def test_hud_trend_status_escapes_gcode_state_and_drops_a_non_numeric_layer():
    if _skip_without_node("test_hud_trend_status_escapes_gcode_state_and_drops_a_non_numeric_layer"):
        return
    base = {"stage": 255, "success_probability": 0.9, "decision_confidence": 0.9}
    hostile = ["<img src=x onerror=alert(3)>", '"><script>alert(1)</script>', "RUNNING</span><b>x</b>"]
    cases = [{"fn": "hpUpdateFromResult", "watch": ["hp-trend-status"],
              "arg": dict(base, gcode_state=g, layer="<b>1</b>", total_layers="2</span><img src=x onerror=alert(4)>",
                          ams_humidity="3<i>x</i>")} for g in hostile]
    for g, snap in zip(hostile, _run_hud(cases)):
        h = snap["hp-trend-status"]["html"]
        assert h == '<span style="color:#888">' + _esc(g) + '</span>', (g, h)
    layer_only = [dict(base, gcode_state="RUNNING", layer=layer, total_layers=total)
                  for layer, total in (("<b>1</b>", 2), (1, "<b>2</b>"), ("1</span><img src=x onerror=1>", "2"))]
    for arg, snap in zip(layer_only, _run_hud([{"fn": "hpUpdateFromResult", "watch": ["hp-trend-status"], "arg": a}
                                               for a in layer_only])):
        h = snap["hp-trend-status"]["html"]
        assert h == '<span style="color:#40d0c0">RUNNING</span>', (arg, h)


# --------------------------------------------------------------------------- job monitor: job start

def _ast_of(rel):
    return ast.parse((REPO / rel).read_text())


def test_job_monitor_defines_its_idle_states_once():
    # Two module-level assignments, the later one silently winning, is how the reset ended up keyed on
    # a set nobody reading the top of the file would believe.
    tree = _ast_of("camera/job_monitor.py")
    defs = [n.lineno for n in ast.walk(tree)
            if (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_IDLE_STATES" for t in n.targets))
            or (isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.target.id == "_IDLE_STATES")]
    assert len(defs) == 1, f"_IDLE_STATES is defined {len(defs)} times, at lines {defs}"


def _seed_job(mon, name):
    """Leave the monitor and the analyzer holding a previous job's per-job state."""
    mon._last_analyze_time = mon._last_gated_time = 12345.0
    mon._confidence_window.append("warning")
    mon._health_history.append({"ts": 1.0, "success_pct": 0.5})
    with mon._lock:
        mon._latest_result = {"verdict": "warning", "stage_gated": False}
    job_monitor._save_result(name, mon._latest_result)
    job_analyzer.store_reference(name, _jpeg())


def _survivors(mon, name):
    """What of the previous job's monitor state is still there (the reference frame is reported apart)."""
    left = []
    if mon.get_latest_result() is not None:
        left.append("cached result")
    if job_monitor._persist_path(name).exists():
        left.append("persisted result")
    if mon._confidence_window:
        left.append("confidence window")
    if mon.get_health_history():
        left.append("health history")
    if mon._last_analyze_time or mon._last_gated_time:
        left.append("timers")
    return left


_SEEDED = ["cached result", "persisted result", "confidence window", "health history", "timers"]
_JOB_STARTS = [(prev, new) for prev in (None, "IDLE", "FINISH", "FAILED", "SLICING", "INIT", "", "PREPARE")
               for new in ("RUNNING", "PAUSE")]
_NOT_JOB_STARTS = [("PAUSE", "RUNNING"), ("RUNNING", "PAUSE"), ("RUNNING", "FINISH"), ("RUNNING", "FAILED"),
                   ("PAUSE", "FINISH"), ("PAUSE", "FAILED"), ("IDLE", "PREPARE"), ("PREPARE", "FAILED"),
                   ("FINISH", "IDLE")]


def _transition(prev, new, name="camdef-trans"):
    """Seed a monitor with a finished job's state, then deliver gcode_state prev -> new. Returns the
    monitor state that survived and whether the analyzer still holds the printer's reference frame."""
    with _env(BambuState(gcode_state=new), _job(0)):
        mon = job_monitor._PrinterMonitor(name)
        try:
            _seed_job(mon, name)
            mon._last_gcode_state = prev
            mon.on_update()
            return _survivors(mon, name), job_analyzer.get_reference(name)[0] is not None
        finally:
            job_analyzer._references.pop(name, None)


def test_a_job_start_from_any_state_a_job_can_start_from_resets_the_per_job_state():
    # bpm's gcode_state is a plain string ("IDLE", "PREPARE", "RUNNING", "PAUSE", "FINISH", "FAILED",
    # "SLICING", "INIT"; there is no GcodeState enum). A print normally goes PREPARE -> RUNNING, and
    # the reset never fired for that transition.
    failures = [(prev, new, left) for prev, new in _JOB_STARTS
                for left in [_transition(prev, new)[0]] if left]
    assert not failures, f"{len(failures)} job starts left the previous job's state behind, e.g. {failures[:3]}"


def test_a_job_start_drops_the_previous_jobs_reference_frame():
    # The reference lives in the analyzer with a 10 minute TTL and on_update never cleared it.
    failures = [(prev, new) for prev, new in _JOB_STARTS if _transition(prev, new)[1]]
    assert not failures, f"{len(failures)} job starts kept the previous job's reference frame, e.g. {failures[:3]}"


def test_pause_and_resume_within_a_job_do_not_reset_the_per_job_state():
    # The controls for the two resets above: every transition inside a job, or into a state that is
    # not a start, leaves the monitor state and the reference frame in place.
    failures = []
    for prev, new in _NOT_JOB_STARTS:
        left, ref = _transition(prev, new)
        if left != _SEEDED or not ref:
            failures.append((prev, new, sorted(set(_SEEDED) - set(left)), "reference kept" if ref else "reference dropped"))
    assert not failures, f"transitions that must not reset cleared state: {failures}"


def test_a_new_job_does_not_diff_against_the_previous_jobs_reference_frame():
    # Job 1's frame is still inside the 10 minute TTL when job 2 starts. The first analysis of job 2
    # must take its own frame as the reference, not compare with job 1's.
    name = "camdef-ref"
    old, new = _jpeg(top=255), _jpeg(top=200)
    assert old != new
    with _env(BambuState(gcode_state="RUNNING"), _job(0), jpeg=new):
        mon = job_monitor._PrinterMonitor(name)
        try:
            job_analyzer.store_reference(name, old)
            mon._last_gcode_state = "PREPARE"
            mon.on_update()
            mon._tick()
            ref, _age = job_analyzer.get_reference(name)
            result = mon.get_latest_result()
        finally:
            job_analyzer._references.pop(name, None)
    assert result is not None and result["stage_gated"] is False, result
    assert ref == new, "the new job diffed against the previous job's reference frame"


def test_a_reference_stored_during_the_job_survives_a_pause_and_resume():
    # store_as_reference keeps working: the reference lands after the job-start reset and stays.
    name = "camdef-refjob"
    frame = _jpeg(top=180)
    with _env(BambuState(gcode_state="RUNNING"), _job(0), jpeg=frame):
        mon = job_monitor._PrinterMonitor(name)
        try:
            mon._last_gcode_state = "PREPARE"
            mon.on_update()
            assert job_analyzer.get_reference(name)[0] is None
            r = camera_tool.analyze_active_job(name, store_as_reference=True)
            assert "error" not in r, r
            assert job_analyzer.get_reference(name)[0] == frame, "store_as_reference did not store"
            mon._last_gcode_state = "PAUSE"
            mon.on_update()                                    # PAUSE -> RUNNING: the same job
            assert job_analyzer.get_reference(name)[0] == frame, "a resume dropped the job's reference"
        finally:
            job_analyzer._references.pop(name, None)


# --------------------------------------------------------------------------- analyze_active_job: dead fetch

def _function_node(rel, name):
    return next(n for n in ast.walk(_ast_of(rel)) if isinstance(n, ast.FunctionDef) and n.name == name)


def test_analyze_active_job_has_no_monitor_cache_fetch():
    # It fetched job_monitor.get_latest_result into _monitor_result and never read it, while its own
    # docstring says the tool does not read the monitor's cache.
    fn = _function_node("tools/camera.py", "analyze_active_job")
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(fn) if isinstance(n, ast.Attribute)}
    imported = {a.name for n in ast.walk(fn) if isinstance(n, ast.ImportFrom) for a in n.names}
    hits = {"_monitor_result", "job_monitor"} & (names | imported)
    assert not hits, f"analyze_active_job still names {sorted(hits)}"
    assert "get_latest_result" not in attrs, "analyze_active_job still calls get_latest_result"
    assert "this tool does NOT read the monitor's cache" in " ".join(ast.get_docstring(fn).split()), \
        "the docstring no longer says the tool ignores the monitor cache"


def test_analyze_active_job_never_calls_the_monitor_cache():
    calls = []
    with _env(BambuState(gcode_state="RUNNING"), _job(0), jpeg=_jpeg()):
        with mock.patch.object(job_monitor, "get_latest_result", lambda n: calls.append(n) or {"verdict": "x"}):
            r = camera_tool.analyze_active_job("camdef-nomon")
        job_analyzer._references.pop("camdef-nomon", None)
    assert "error" not in r, r
    assert not calls, f"get_latest_result was called {len(calls)} time(s): {calls}"


# --------------------------------------------------------------------------- job monitor: reset vs in-flight analysis

def _run_analysis_across_a_job_start(name, block, reset):
    """Start an analysis of the previous job on a thread, hold it inside its slow step (``block`` is
    "capture", "analyze", "verdict" or "trend": the four points in _run_analyze where the monitor's
    lock is not held and time passes; "verdict" is after the confidence sample is stored, "trend" after
    the health record is), optionally deliver the next job's start (FINISH -> RUNNING) while it is
    held, release it and let it finish. Returns what of the analysis is left in the monitor, on disk
    and in the analyzer afterwards (measured before the temp environment goes away), the exceptions the
    analysis thread raised, and whether the reference frame the analyzer holds is the previous job's."""
    old, new = _jpeg(top=255), _jpeg(top=120)
    assert old != new
    state = BambuState(gcode_state="RUNNING")
    entered, gate, seen = threading.Event(), threading.Event(), []

    def held_capture(_name):
        entered.set()
        assert gate.wait(30), "the test never released the capture"
        return old

    def held(real):
        def call(*a, **k):
            entered.set()
            assert gate.wait(30), "the test never released the analysis"
            return real(*a, **k)
        return call

    with _env(state, _job(0), jpeg=old):
        if block == "capture":
            job_monitor._capture_one_frame = held_capture       # _env puts the original back
        mon = job_monitor._PrinterMonitor(name)
        mon._last_gcode_state = "RUNNING"
        job_analyzer.store_reference(name, old)                  # the previous job's reference frame

        def body():
            try:
                mon._run_analyze(state)
            except BaseException as exc:                         # noqa: BLE001 - reported to the caller
                seen.append(exc)

        holds = {"analyze": (job_analyzer, "analyze"),
                 "verdict": (job_monitor, "_stable_verdict"),
                 "trend": (job_monitor, "_fp_trend")}
        with contextlib.ExitStack() as stack:
            if block in holds:
                target, attr = holds[block]
                stack.enter_context(mock.patch.object(target, attr, held(getattr(target, attr))))
            th = threading.Thread(target=body, name="camdef-race")
            th.start()
            try:
                assert entered.wait(30), "the analysis never reached its slow step"
                if reset:
                    mon._last_gcode_state = "FINISH"
                    state.gcode_state = "RUNNING"
                    mon.on_update()                              # the next job starts: the reset runs
                    assert _survivors(mon, name) == [], _survivors(mon, name)
                    assert job_analyzer.get_reference(name)[0] is None, "the reset left the reference"
            finally:
                gate.set()
                th.join(60)
        assert not th.is_alive(), "the analysis thread did not finish"
        left = [x for x in _survivors(mon, name) if x != "timers"]
        ref = job_analyzer.get_reference(name)[0]
        if ref is not None:
            left.append("reference frame" + (" (the previous job's)" if ref == old else ""))
        job_analyzer._references.pop(name, None)
        return left, seen, {"result": mon.get_latest_result(), "history": len(mon.get_health_history()),
                            "window": len(mon._confidence_window), "ref_kept": ref == old}


def test_an_analysis_with_no_job_start_still_stores_everything():
    # The control for the two race tests below: with no reset in between, the same analysis stores its
    # result, persisted copy, health record, confidence sample and reference, so the guard is not what
    # makes them pass.
    for block in ("capture", "analyze", "verdict", "trend"):
        left, seen, kept = _run_analysis_across_a_job_start(f"camdef-race-control-{block}", block, reset=False)
        assert not seen, seen
        assert kept["result"] is not None and kept["result"]["stage_gated"] is False, (block, kept)
        assert kept["history"] == 1 and kept["window"] == 1 and kept["ref_kept"], (block, kept)
        assert "persisted result" in left and "cached result" in left, (block, left)


def test_a_job_start_during_the_previous_jobs_capture_leaves_nothing_of_the_old_job():
    left, seen, _kept = _run_analysis_across_a_job_start("camdef-race-capture", "capture", reset=True)
    assert not seen, seen
    assert not left, f"the previous job's analysis wrote into the new job: {left}"


def test_a_job_start_during_the_previous_jobs_analysis_leaves_nothing_of_the_old_job():
    left, seen, _kept = _run_analysis_across_a_job_start("camdef-race-analyze", "analyze", reset=True)
    assert not seen, seen
    assert not left, f"the previous job's analysis wrote into the new job: {left}"


def test_a_job_start_after_the_confidence_sample_is_stored_leaves_nothing_of_the_old_job():
    # The confidence sample is already in the window at this point; the health record is not yet.
    left, seen, _kept = _run_analysis_across_a_job_start("camdef-race-verdict", "verdict", reset=True)
    assert not seen, seen
    assert not left, f"the previous job's analysis wrote into the new job: {left}"


def test_a_job_start_just_before_the_result_is_stored_leaves_nothing_of_the_old_job():
    # The window sample and the health record are in; only the cached and persisted result are still to come.
    left, seen, _kept = _run_analysis_across_a_job_start("camdef-race-trend", "trend", reset=True)
    assert not seen, seen
    assert not left, f"the previous job's analysis wrote into the new job: {left}"


def test_the_persisted_copy_cannot_land_after_the_reset_deleted_it():
    # The reset deletes the persisted file under the monitor's lock, so the analysis must write that
    # file under the same lock. Deliver the next job's start from inside the write: with the write
    # inside the lock the reset waits and runs after it (nothing survives); with the write outside the
    # lock the reset runs first and the stale file lands after it.
    name = "camdef-race-persist"
    state = BambuState(gcode_state="RUNNING")
    real_save = job_monitor._save_result
    resets = []

    with _env(state, _job(0), jpeg=_jpeg()):
        mon = job_monitor._PrinterMonitor(name)
        mon._last_gcode_state = "RUNNING"
        reset_done = threading.Event()

        def reset():
            mon._last_gcode_state = "FINISH"
            mon.on_update()
            reset_done.set()

        def save_during_a_job_start(n, result):
            th = threading.Thread(target=reset, name="camdef-reset")
            resets.append(th)
            th.start()
            reset_done.wait(1.0)          # cannot complete while the analysis holds the lock; then it times out
            real_save(n, result)

        try:
            with mock.patch.object(job_monitor, "_save_result", save_during_a_job_start):
                mon._run_analyze(state)
            for th in resets:
                th.join(30)
            assert reset_done.is_set(), "the job start never completed"
            left = [x for x in _survivors(mon, name) if x != "timers"]
        finally:
            job_analyzer._references.pop(name, None)
    assert not left, f"a stale persisted result landed after the job-start reset: {left}"


def test_the_next_jobs_own_analysis_after_a_race_is_stored_normally():
    # After the reset swallowed the stale analysis, the next tick (the new job's) must work: it takes its
    # own frame as the reference and stores its own result.
    name = "camdef-race-next"
    new = _jpeg(top=120)
    state = BambuState(gcode_state="RUNNING")
    with _env(state, _job(0), jpeg=new):
        mon = job_monitor._PrinterMonitor(name)
        try:
            mon._last_gcode_state = "FINISH"
            mon.on_update()
            mon._tick()
            assert mon.get_latest_result() is not None, "the new job's analysis was discarded"
            assert job_analyzer.get_reference(name)[0] == new
            assert len(mon.get_health_history()) == 1
        finally:
            job_analyzer._references.pop(name, None)


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {exc!r}"[:600])
    sys.exit(1 if failed else 0)
