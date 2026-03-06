"""
data_collector.py — Self-contained telemetry history wrapper.

Subscribes to session_manager on_update callbacks. Reads BambuState fields
directly from BambuPrinter — no HTTP, no Flask dependency.

Maintains per-printer rolling history for temperature and fan data, plus
gcode_state_durations tracking for time-in-state analysis.
"""

from __future__ import annotations

import time
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any

_RETENTION_SECONDS = 3600  # 60 minutes
_INTERVAL_SECONDS = 2.5


@dataclass
class DataPoint:
    timestamp: float
    value: float


@dataclass
class Collection:
    """Rolling time-series collection with retention pruning."""
    name: str
    points: deque = field(default_factory=deque)

    def add(self, value: float) -> None:
        self.points.append(DataPoint(time.time(), value))
        self._prune()

    def _prune(self) -> None:
        cutoff = time.time() - _RETENTION_SECONDS
        while self.points and self.points[0].timestamp < cutoff:
            self.points.popleft()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "data": [{"t": p.timestamp, "v": p.value} for p in self.points],
        }

    def to_summary(self) -> dict:
        """Return {min, max, avg, last, count} stats over the current window."""
        if not self.points:
            return {"count": 0, "min": None, "max": None, "avg": None, "last": None}
        values = [p.value for p in self.points]
        return {
            "count": len(values),
            "min": round(min(values), 2),
            "max": round(max(values), 2),
            "avg": round(sum(values) / len(values), 2),
            "last": round(values[-1], 2),
        }


class PrinterDataCollector:
    """Collects telemetry history for a single printer."""

    COLLECTION_NAMES = [
        "tool",        # nozzle 0 temp
        "tool_1",      # nozzle 1 temp (H2D dual extruder)
        "bed",
        "chamber",
        "part_fan",
        "aux_fan",
        "exhaust_fan",
        "heatbreak_fan",
    ]

    def __init__(self, name: str):
        self.name = name
        self.collections: dict[str, Collection] = {
            n: Collection(n) for n in self.COLLECTION_NAMES
        }
        self.gcode_state_durations: dict[str, float] = {}
        self._last_gcode_state: str | None = None
        self._last_job_id: str | None = None
        self._last_tick: float = time.time()
        self._lock = threading.Lock()

    def on_update(self, printer) -> None:
        """Called on every MQTT state update. Reads directly from BambuPrinter."""
        now = time.time()
        state = printer.printer_state
        if not state:
            return

        with self._lock:
            elapsed = now - self._last_tick
            self._last_tick = now

            # Temperature collections
            try:
                extruders = state.extruders or []
                if extruders:
                    self.collections["tool"].add(
                        extruders[0].active_nozzle_temp or 0.0
                    )
                    if len(extruders) > 1:
                        self.collections["tool_1"].add(
                            extruders[1].active_nozzle_temp or 0.0
                        )
            except Exception:
                pass

            try:
                climate = state.climate
                if climate:
                    self.collections["bed"].add(climate.bed_temp or 0.0)
                    self.collections["chamber"].add(climate.chamber_temp or 0.0)
            except Exception:
                pass

            # Fan collections
            try:
                fans = state.fans
                if fans:
                    self.collections["part_fan"].add(fans.part_fan_speed or 0.0)
                    self.collections["aux_fan"].add(fans.aux_fan_speed or 0.0)
                    self.collections["exhaust_fan"].add(fans.exhaust_fan_speed or 0.0)
                    self.collections["heatbreak_fan"].add(fans.cooling_fan_speed or 0.0)
            except Exception:
                pass

            # gcode_state_durations
            try:
                gcode_state = state.gcode_state
                if gcode_state:
                    # Reset on new job
                    job = printer.active_job_info
                    job_id = getattr(job, "subtask_name", None) if job else None
                    if job_id and job_id != self._last_job_id:
                        self.gcode_state_durations = {}
                        self._last_job_id = job_id

                    # Track state transition
                    if gcode_state != self._last_gcode_state:
                        self._last_gcode_state = gcode_state
                    self.gcode_state_durations[gcode_state] = (
                        self.gcode_state_durations.get(gcode_state, 0.0) + elapsed
                    )
            except Exception:
                pass

    def get_all_data(self) -> dict:
        """Return all collections + gcode_state_durations as a serializable dict."""
        with self._lock:
            return {
                "collections": {k: v.to_dict() for k, v in self.collections.items()},
                "gcode_state_durations": dict(self.gcode_state_durations),
            }

    def get_summary(self) -> dict:
        """Return per-collection summary stats + gcode_state_durations."""
        with self._lock:
            return {
                "summary": {k: v.to_summary() for k, v in self.collections.items()},
                "gcode_state_durations": dict(self.gcode_state_durations),
            }

    def get_collection(self, field: str) -> dict | None:
        """Return the full time-series dict for one named field, or None if unknown."""
        with self._lock:
            col = self.collections.get(field)
            return col.to_dict() if col else None


class DataCollector:
    """Global data collector — one PrinterDataCollector per printer."""

    def __init__(self):
        self._collectors: dict[str, PrinterDataCollector] = {}
        self._lock = threading.Lock()

    def register_printer(self, name: str) -> None:
        with self._lock:
            if name not in self._collectors:
                self._collectors[name] = PrinterDataCollector(name)

    def on_update(self, name: str, printer) -> None:
        """Called by session_manager's on_update callback."""
        with self._lock:
            collector = self._collectors.get(name)
        if collector:
            collector.on_update(printer)

    def get_all_data(self, name: str) -> dict | None:
        with self._lock:
            collector = self._collectors.get(name)
        return collector.get_all_data() if collector else None

    def get_summary(self, name: str) -> dict | None:
        with self._lock:
            collector = self._collectors.get(name)
        return collector.get_summary() if collector else None

    def get_collection(self, name: str, field: str) -> dict | None:
        with self._lock:
            collector = self._collectors.get(name)
        return collector.get_collection(field) if collector else None

    def get_configured_printers(self) -> list[str]:
        with self._lock:
            return list(self._collectors.keys())


# Module-level singleton
data_collector = DataCollector()
