"""
camera/job_monitor.py — Background per-printer job health monitor.

Responsibilities:
  1. Detect job start: when gcode_state transitions into RUNNING/PAUSE from
     IDLE/FINISH/FAILED (or on MCP startup when a job is already active), capture
     the first available camera frame and store it as the diff reference.
  2. Every 60 seconds while a job is active (RUNNING or PAUSE), run the full
     analyze() pipeline and cache the resulting JobStateReport.
  3. Expose get_latest_report(printer_name) so /job_state can serve the cache
     instantly without triggering a live analysis on every browser poll.

This module is completely independent of the MJPEG stream — it runs even when
no browser has the stream page open.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import Counter, deque
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_PERSIST_DIR = Path.home() / ".bambu-mcp"


def _persist_path(printer_name: str) -> Path:
    return _PERSIST_DIR / f"job_health_{printer_name.replace(' ', '_')}.json"


def _save_result(printer_name: str, result: dict) -> None:
    try:
        _PERSIST_DIR.mkdir(parents=True, exist_ok=True)
        # Strip large image data before persisting — only save scalar fields.
        slim = {k: v for k, v in result.items() if not k.endswith("_png")}
        _persist_path(printer_name).write_text(json.dumps(slim))
    except Exception as e:
        log.debug("job_monitor: failed to persist result for %s: %s", printer_name, e)


def _load_result(printer_name: str) -> Optional[dict]:
    try:
        p = _persist_path(printer_name)
        if p.exists():
            return json.loads(p.read_text())
    except Exception as e:
        log.debug("job_monitor: failed to load persisted result for %s: %s", printer_name, e)
    return None


def _clear_result(printer_name: str) -> None:
    try:
        _persist_path(printer_name).unlink(missing_ok=True)
    except Exception as e:
        log.debug("job_monitor: failed to clear persisted result for %s: %s", printer_name, e)

# How often to run full analysis while a job is active (seconds).
ANALYZE_INTERVAL = 60
# How often to run the fast hot-pixel pre-check (seconds).
PRECHECK_INTERVAL = 10
# Hot-pixel threshold that triggers a forced full-analysis cycle.
PRECHECK_HOT_PCT_TRIGGER = 0.06  # ~Obico warning-grade; sourced from Obico threshold 0.08 minus margin

# States considered "job active".
_ACTIVE_STATES = {"RUNNING", "PAUSE"}
# States considered "job idle" (transition from these triggers reference capture).
_IDLE_STATES = {"IDLE", "FINISH", "FAILED"}

# Stage codes that mean "printing normally" (full analysis runs only in this state).
# All other codes = gated.  Sources: firmware stage table in get_job_info docstring.
_STAGE_PRINTING = 255
_STAGE_NAMES: dict[int, str] = {
    255: "printing",
    4:   "filament change",
    6:   "M400 pause",
    17:  "user pause",
    8:   "heating nozzle",
    1:   "auto-leveling",
    2:   "heatbed preheat",
    15:  "nozzle clean",
    0:   "idle",
}

# Confidence accumulation: severity ordering for tie-breaking.
_VERDICT_SEVERITY: dict[str, int] = {"clean": 0, "warning": 1, "critical": 2}


def _stable_verdict(window: deque) -> Optional[str]:
    """Return the mode verdict from the window (min 3 samples); tie-break = more severe."""
    if len(window) < 3:
        return None
    counts = Counter(window)
    max_count = max(counts.values())
    # All verdicts tied at the max count — pick most severe.
    candidates = [v for v, c in counts.items() if c == max_count]
    return max(candidates, key=lambda v: _VERDICT_SEVERITY.get(v, 0))


def _fp_trend(history: list) -> str:
    """Characterise the direction of failure_probability over the rolling window."""
    if len(history) < 3:
        return "building"
    vals   = history
    recent = sum(vals[-2:]) / 2
    older  = sum(vals[:-2]) / max(len(vals) - 2, 1)
    if older < 0.005:
        return "stable"
    ratio = recent / older
    if ratio > 1.20:
        return "escalating"
    if ratio < 0.80:
        return "improving"
    return "stable"


def _get_config_value(text: str, key: str, default: str = "") -> str:
    """Extract a single key=value from a BambuStudio config/INI/gcode-header string."""
    m = re.search(rf'(?:^|;)\s*{re.escape(key)}\s*=\s*(.+?)(?:\s*;.*)?$', text, re.MULTILINE)
    return m.group(1).strip() if m else default


def _read_print_settings(printer_name: str, job_name: str) -> dict:
    """Extract slicer settings from the cached .3mf for the active job.

    Returns a dict with:
      has_support, support_type, brim_type, brim_width_mm, has_raft,
      layer_height_mm, initial_layer_height_mm, infill_density_pct, wall_loops.
    Returns {} if the local file is unavailable.
    """
    from session_manager import session_manager

    printer = session_manager.get_printer(printer_name)
    job     = session_manager.get_job(printer_name)
    if not printer or not job:
        return {}

    gcode_file = getattr(job, "gcode_file", None)
    if not gcode_file:
        return {}

    cfg_obj = getattr(printer, "config", None)
    cache_base = getattr(cfg_obj, "bpm_cache_path", None)
    serial     = getattr(cfg_obj, "serial_number", None)
    if not cache_base:
        return {}

    cache_path = Path(cache_base)
    if serial:
        cache_path = cache_path / serial
    filename  = gcode_file.lstrip("/").replace("/", "-")
    localfile = cache_path / filename

    if not localfile.exists():
        log.debug("job_monitor[%s]: 3mf not cached locally, skipping print settings", printer_name)
        return {}

    try:
        with ZipFile(str(localfile), "r") as zf:
            cfg_text = ""
            if "Metadata/project_settings.config" in zf.namelist():
                with zf.open("Metadata/project_settings.config") as f:
                    cfg_text = f.read().decode("utf-8", errors="ignore")
            # Also try the gcode header (; key = value comments) as fallback.
            gcode_header = ""
            gcode_entry = f"Metadata/plate_1.gcode"
            try:
                if gcode_entry in zf.namelist():
                    with zf.open(gcode_entry) as f:
                        # Only read the header (first 8 KB) — settings are at the top.
                        gcode_header = f.read(8192).decode("utf-8", errors="ignore")
            except Exception:
                pass
            combined = cfg_text + "\n" + gcode_header
    except BadZipFile:
        log.debug("job_monitor[%s]: bad 3mf zip: %s", printer_name, localfile)
        return {}
    except Exception as e:
        log.debug("job_monitor[%s]: print settings read error: %s", printer_name, e)
        return {}

    def _val(key, default=""):
        return _get_config_value(combined, key, default)

    def _int(key, default=0):
        try:
            return int(_val(key, str(default)).split("%")[0].strip())
        except (ValueError, AttributeError):
            return default

    def _float(key, default=0.0):
        try:
            return float(_val(key, str(default)))
        except (ValueError, AttributeError):
            return default

    brim_type     = _val("brim_type", "no_brim")
    raft_layers   = _int("raft_layers", 0)
    infill_raw    = _val("sparse_infill_density") or _val("infill_density") or "15%"
    try:
        infill_pct = float(infill_raw.strip().rstrip("%").strip())
    except ValueError:
        infill_pct = 15.0

    return {
        "has_support":             _val("enable_support", "0") not in ("0", "false", ""),
        "support_type":            _val("support_type", "normal"),
        "brim_type":               brim_type,
        "brim_width_mm":           _float("brim_width", 0.0) if brim_type != "no_brim" else 0.0,
        "has_raft":                raft_layers > 0,
        "layer_height_mm":         _float("layer_height", 0.2),
        "initial_layer_height_mm": _float("initial_layer_height", 0.2),
        "infill_density_pct":      infill_pct,
        "wall_loops":              _int("wall_loops") or _int("perimeters") or _int("perimeter_count") or 2,
    }


# States considered "job finished / idle".
_IDLE_STATES = {"IDLE", "FINISH", "FAILED", "SLICING", "INIT", ""}


class _PrinterMonitor:
    """Tracks one printer's job state and runs background analysis."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._lock = threading.Lock()
        self._last_gcode_state: Optional[str] = None
        self._latest_report: Optional[object] = None   # JobStateReport
        self._latest_result: Optional[dict] = _load_result(name)  # pre-load from disk
        self._last_analyze_time: float = 0.0
        self._last_precheck_time: float = 0.0
        self._last_precheck_hot_pct: Optional[float] = None
        self._confidence_window: deque = deque(maxlen=5)
        self._fp_history: deque = deque(maxlen=10)        # rolling failure_probability history
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_latest_result(self) -> Optional[dict]:
        with self._lock:
            return self._latest_result

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"job-monitor-{self.name}", daemon=True
        )
        self._thread.start()
        log.info("job_monitor: started for %s", self.name)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        log.info("job_monitor: stopped for %s", self.name)

    def on_update(self) -> None:
        """Called by session_manager update callback — fast, non-blocking."""
        try:
            from session_manager import session_manager
            state = session_manager.get_state(self.name)
            if state is None:
                return
            new_state = state.gcode_state or ""
            prev_state = self._last_gcode_state

            if prev_state == new_state:
                return

            log.debug("job_monitor[%s]: gcode_state %s → %s", self.name, prev_state, new_state)
            self._last_gcode_state = new_state

            # Job just became active — store a reference frame immediately.
            if new_state in _ACTIVE_STATES and (prev_state is None or prev_state in _IDLE_STATES):
                log.info("job_monitor[%s]: job started (%s) — scheduling initial reference capture", self.name, new_state)
                # Reset analyze timer so next loop tick triggers immediately.
                self._last_analyze_time = 0.0
                # Reset per-job accumulators.
                self._confidence_window.clear()
                self._fp_history.clear()
                # Clear stale persisted result from previous job.
                with self._lock:
                    self._latest_result = None
                _clear_result(self.name)

        except Exception as e:
            log.debug("job_monitor[%s]: on_update error: %s", self.name, e)

    # ── Background loop ───────────────────────────────────────────────────────

    def _run(self) -> None:
        """Main monitor loop — runs in a daemon thread."""
        # Give sessions a moment to connect on startup.
        time.sleep(5)

        # On startup: if a job is already active, treat it as a fresh start.
        try:
            from session_manager import session_manager
            state = session_manager.get_state(self.name)
            if state and state.gcode_state in _ACTIVE_STATES:
                log.info("job_monitor[%s]: job already active at startup (%s) — capturing initial reference", self.name, state.gcode_state)
                self._last_gcode_state = state.gcode_state
                self._last_analyze_time = 0.0  # run immediately on first tick
        except Exception as e:
            log.debug("job_monitor[%s]: startup state check error: %s", self.name, e)

        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as e:
                log.warning("job_monitor[%s]: tick error: %s", self.name, e)
            # Sleep in short increments so stop_event is responsive.
            for _ in range(10):
                if self._stop_event.is_set():
                    break
                time.sleep(1)

    def _tick(self) -> None:
        from session_manager import session_manager
        state = session_manager.get_state(self.name)
        if state is None:
            return

        gcode_state = state.gcode_state or ""
        if gcode_state not in _ACTIVE_STATES:
            return  # nothing to do while idle

        now = time.monotonic()
        stage = getattr(state, "stage", _STAGE_PRINTING)
        stage_name = _STAGE_NAMES.get(stage, "setup")

        # --- Stage gating ---
        if stage != _STAGE_PRINTING:
            # Publish a gated result without running the camera pipeline.
            if now - self._last_analyze_time >= ANALYZE_INTERVAL:
                self._last_analyze_time = now
                self._store_gated_result(stage, stage_name)
            return

        # --- Fast pre-check (10s) ---
        precheck_triggered = False
        if now - self._last_precheck_time >= PRECHECK_INTERVAL:
            self._last_precheck_time = now
            precheck_triggered = self._run_precheck()

        # --- Full analysis (60s, or immediately on pre-check trigger) ---
        due_for_analysis = (now - self._last_analyze_time >= ANALYZE_INTERVAL)
        if due_for_analysis or precheck_triggered:
            self._last_analyze_time = now
            self._run_analyze(state)

    def _run_precheck(self) -> bool:
        """Lightweight hot-pixel scan.  Returns True if threshold exceeded (triggers full analysis)."""
        try:
            jpeg = _capture_one_frame(self.name)
            if not jpeg:
                return False
            import numpy as np
            from PIL import Image
            import io

            img  = Image.open(io.BytesIO(jpeg)).convert("RGB")
            arr  = np.array(img)
            h, w = arr.shape[:2]
            # Air zone: top 40% × inner 80%
            r0, r1 = 0, int(h * 0.40)
            c0, c1 = int(w * 0.10), int(w * 0.90)
            zone = arr[r0:r1, c0:c1]
            brightness = zone.mean(axis=2)
            hot_pct = float((brightness > 120).sum()) / brightness.size

            with self._lock:
                self._last_precheck_hot_pct = hot_pct

            triggered = hot_pct >= PRECHECK_HOT_PCT_TRIGGER
            if triggered:
                log.info("job_monitor[%s]: pre-check triggered (hot_pct=%.3f ≥ %.3f) — forcing full analysis",
                         self.name, hot_pct, PRECHECK_HOT_PCT_TRIGGER)
            return triggered
        except Exception as e:
            log.debug("job_monitor[%s]: pre-check error: %s", self.name, e)
            return False

    def _store_gated_result(self, stage: int, stage_name: str) -> None:
        """Store a stage-gated result (no camera analysis performed)."""
        from datetime import datetime, timezone
        result = {
            "stage":        stage,
            "stage_name":   stage_name,
            "stage_gated":  True,
            "verdict":      "clean",
            "score":        0.0,
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "stable_verdict":        None,
            "confidence_window":     [],
            "confidence_window_size": 0,
        }
        with self._lock:
            self._latest_result = result
        _save_result(self.name, result)
        log.debug("job_monitor[%s]: stage-gated result stored (stage=%d %s)", self.name, stage, stage_name)

    def _run_analyze(self, state) -> None:
        """Capture a frame and run the full analysis pipeline."""
        from camera.job_analyzer import analyze as _analyze, store_reference, get_reference
        from session_manager import session_manager
        import base64
        from datetime import datetime, timezone

        printer_name = self.name

        # Capture frame directly — no dependency on an active MJPEG stream.
        jpeg: Optional[bytes] = None
        try:
            jpeg = _capture_one_frame(printer_name)
        except Exception as e:
            log.warning("job_monitor[%s]: frame capture failed: %s", printer_name, e)
            return

        if not jpeg:
            log.debug("job_monitor[%s]: no frame available, skipping tick", printer_name)
            return

        # If no reference is stored yet for this printer, this IS the reference.
        ref_jpeg, ref_age = get_reference(printer_name)
        if ref_jpeg is None:
            log.info("job_monitor[%s]: storing initial reference frame", printer_name)
            store_reference(printer_name, jpeg)
            ref_jpeg, ref_age = get_reference(printer_name)

        # Build printer context (mirrors _serve_job_state / analyze_active_job).
        try:
            printer_context = _build_context(printer_name, state)
        except Exception as e:
            log.warning("job_monitor[%s]: context build failed: %s", printer_name, e)
            return

        try:
            report = _analyze(jpeg, printer_context, reference_jpeg=ref_jpeg,
                              reference_age_s=ref_age, quality="auto")
        except Exception as e:
            log.warning("job_monitor[%s]: analyze failed: %s", printer_name, e)
            return

        # Confidence accumulation — deque(maxlen=5), stable after 3+ samples.
        with self._lock:
            self._confidence_window.append(report.verdict)
            window_snapshot = list(self._confidence_window)
            precheck_hot_pct = self._last_precheck_hot_pct

        sv = _stable_verdict(self._confidence_window)
        stage = getattr(state, "stage", _STAGE_PRINTING)
        stage_name = _STAGE_NAMES.get(stage, "setup")

        # Failure probability — Bayesian model, updated every analysis cycle.
        from camera.job_analyzer import compute_failure_probability
        try:
            fp = compute_failure_probability(
                report.score, report.thresh_warn, report.thresh_crit,
                printer_context, stable_verdict=sv or "clean",
            )
        except Exception as e:
            log.debug("job_monitor[%s]: failure_probability error: %s", printer_name, e)
            fp = None

        with self._lock:
            if fp is not None:
                self._fp_history.append(fp)
            fp_history_snapshot = list(self._fp_history)

        fp_trend = _fp_trend(fp_history_snapshot)
        fp_peak  = max(fp_history_snapshot) if fp_history_snapshot else fp

        def _uri(b):
            return "data:image/png;base64," + base64.b64encode(b).decode() if b else None

        result = {
            # core scoring
            "verdict":        report.verdict,
            "score":          round(report.score, 4),
            "hot_pct":        round(report.hot_pct, 4),
            "strand_score":   round(report.strand_score, 4),
            "edge_density":   round(report.edge_density, 4),
            "diff_score":     round(report.diff_score, 4) if report.diff_score is not None else None,
            "reference_age_s": round(report.reference_age_s, 1) if report.reference_age_s is not None else None,
            "thresh_warn":    round(report.thresh_warn, 4),
            "thresh_crit":    round(report.thresh_crit, 4),
            "quality":        report.quality,
            # stage
            "stage":          stage,
            "stage_name":     stage_name,
            "stage_gated":    False,
            # pre-check
            "precheck_hot_pct":     round(precheck_hot_pct, 4) if precheck_hot_pct is not None else None,
            "precheck_triggered":   (precheck_hot_pct is not None and precheck_hot_pct >= PRECHECK_HOT_PCT_TRIGGER),
            # confidence
            "stable_verdict":         sv,
            "confidence_window":      window_snapshot,
            "confidence_window_size": len(window_snapshot),
            # failure probability (updated every cycle, trends over rolling window)
            "failure_probability":       fp,
            "failure_probability_trend": fp_trend,
            "failure_probability_peak":  round(fp_peak, 4) if fp_peak is not None else None,
            # job context
            "layer":         printer_context.get("layer", 0),
            "total_layers":  printer_context.get("total_layers", 0),
            "progress_pct":  printer_context.get("progress_pct", 0),
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            # image assets
            "job_state_composite_png": _uri(report.job_state_composite_png),
            "raw_png":                 _uri(report.raw_png),
            "annotated_png":           _uri(report.annotated_png),
            "health_panel_png":        _uri(report.health_panel_png),
        }

        with self._lock:
            self._latest_report = report
            self._latest_result = result
        _save_result(self.name, result)

        log.info("job_monitor[%s]: analysis complete — verdict=%s stable=%s score=%.3f layer=%s/%s",
                 printer_name, report.verdict, sv or "building",
                 report.score, printer_context.get("layer", "?"), printer_context.get("total_layers", "?"))


# ── Module-level singleton registry ───────────────────────────────────────────

_monitors: dict[str, _PrinterMonitor] = {}
_registry_lock = threading.Lock()


def register(printer_name: str) -> None:
    """Create and start a monitor for a printer (idempotent)."""
    with _registry_lock:
        if printer_name not in _monitors:
            m = _PrinterMonitor(printer_name)
            _monitors[printer_name] = m
            m.start()
            log.info("job_monitor: registered %s", printer_name)


def on_update(printer_name: str) -> None:
    """Forward a session_manager update event to the relevant monitor."""
    m = _monitors.get(printer_name)
    if m:
        m.on_update()


def get_latest_result(printer_name: str) -> Optional[dict]:
    """Return the most recent cached analysis result, or None if not yet available."""
    m = _monitors.get(printer_name)
    return m.get_latest_result() if m else None


def stop_all() -> None:
    """Stop all monitors (called on server shutdown)."""
    with _registry_lock:
        for m in _monitors.values():
            m.stop()
        _monitors.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _capture_one_frame(printer_name: str) -> Optional[bytes]:
    """Capture one JPEG frame using the correct protocol for this printer."""
    from session_manager import session_manager
    from tools.camera import _capture_jpeg

    printer = session_manager.get_printer(printer_name)
    if printer is None:
        log.debug("job_monitor[%s]: no printer session for frame capture", printer_name)
        return None
    return _capture_jpeg(printer)


def _build_context(printer_name: str, state) -> dict:
    """Build the printer_context dict expected by job_analyzer.analyze()."""
    from session_manager import session_manager
    job    = session_manager.get_job(printer_name)
    config = session_manager.get_config(printer_name)

    climate = state.climate
    nozzles_list = [
        {"id": e.id, "temp": e.temp, "target": e.temp_target}
        for e in (state.extruders or [])
    ]
    nozzle        = nozzles_list[0]["temp"]   if nozzles_list else getattr(state, "active_nozzle_temp", 0)
    nozzle_target = nozzles_list[0]["target"] if nozzles_list else getattr(state, "active_nozzle_temp_target", 0)

    has_device_error = any(e.get("type") == "device_error" for e in (state.hms_errors or []))
    hms_errors = [
        {"code": e.get("code", ""), "msg": e.get("msg", ""), "is_critical": True}
        for e in (state.hms_errors or [])
        if e.get("type") == "device_hms" and has_device_error
    ]

    detectors = {}
    if config:
        detectors = {
            "spaghetti_detector":      {"enabled": getattr(config, "spaghetti_detector", False),
                                        "sensitivity": getattr(config, "spaghetti_detector_sensitivity", "medium")},
            "nozzleclumping_detector": {"enabled": getattr(config, "nozzleclumping_detector", False)},
            "airprinting_detector":    {"enabled": getattr(config, "airprinting_detector", False)},
        }

    active_ams_id = getattr(state, "active_ams_id", -1)
    ams_hum = 0
    if active_ams_id >= 0:
        au = next((u for u in (getattr(state, "ams_units", None) or [])
                   if u.ams_id == active_ams_id), None)
        if au:
            ams_hum = getattr(au, "humidity_index", 0)

    # Material-aware scoring context
    printer_obj = session_manager.get_printer(printer_name)
    _model = getattr(getattr(printer_obj, "config", None), "printer_model", None)
    try:
        from bambu_printer_manager import getPrinterSeriesByModel
        _series = getPrinterSeriesByModel(_model).name if _model else "UNKNOWN"
    except Exception:
        _series = "UNKNOWN"
    _active_nozzle = getattr(state, "active_nozzle", None)
    _speed_raw = getattr(printer_obj, "speed_level", 0) if printer_obj else 0
    _speed_name = getattr(_speed_raw, "name", str(_speed_raw)).upper()
    _caps = getattr(getattr(printer_obj, "config", None), "capabilities", None) if printer_obj else None
    _flow_type = getattr(getattr(_active_nozzle, "flow_type", None), "name", "STANDARD") if _active_nozzle else "STANDARD"

    # Active filament (mirrors tools/camera.py _build_status logic)
    active_filament = None
    active_tray_id = getattr(state, "active_tray_id", -1)
    if active_tray_id not in (-1, 255):
        active_spool = next(
            (s for s in (state.spools or []) if s.id == active_tray_id), None
        )
        if active_spool:
            color = active_spool.color or ""
            if color and not color.startswith("#") and len(color) == 6 and all(
                c in "0123456789abcdefABCDEF" for c in color
            ):
                color = "#" + color
            active_filament = {
                "type": active_spool.type or "",
                "color": color,
                "remaining_pct": active_spool.remaining_percent,
            }

    return {
        "job_name":              (job.subtask_name or job.gcode_file or "") if job else "",
        "gcode_state":           state.gcode_state or "IDLE",
        "layer":                 job.current_layer    if job else 0,
        "total_layers":          job.total_layers     if job else 0,
        "progress_pct":          job.print_percentage if job else 0,
        "remaining_minutes":     job.remaining_minutes if job else 0,
        "nozzle_temp":           nozzle,
        "nozzle_target":         nozzle_target,
        "bed_temp":              climate.bed_temp        if climate else 0,
        "bed_target":            climate.bed_temp_target if climate else 0,
        "chamber_temp":          climate.chamber_temp    if climate else 0,
        "part_fan_pct":          climate.part_cooling_fan_speed_percent if climate else 0,
        "aux_fan_pct":           climate.aux_fan_speed_percent          if climate else 0,
        "exhaust_fan_pct":       climate.exhaust_fan_speed_percent      if climate else 0,
        "ams_humidity":          ams_hum,
        "hms_errors":            hms_errors,
        "detectors":             detectors,
        # Material-aware scoring context
        "active_filament":       active_filament,
        "stage_id":              getattr(job, "stage_id", 255) if job else 255,
        "printer_series":        _series,
        "nozzle_diameter_mm":    getattr(_active_nozzle, "diameter_mm", 0.4) if _active_nozzle else 0.4,
        "nozzle_flow_type":      _flow_type,
        "speed_level":           _speed_name,
        "is_chamber_light_on":   getattr(printer_obj, "light_state", False) if printer_obj else False,
        "is_chamber_door_open":  getattr(climate, "is_chamber_door_open", False) if climate else False,
        "is_chamber_lid_open":   getattr(climate, "is_chamber_lid_open", False) if climate else False,
        "has_chamber":           getattr(_caps, "has_chamber_temp", False) if _caps else False,
        "print_settings":        getattr(getattr(job, "project_info", None), "metadata", {}).get("slicer_settings", {}),
    }
