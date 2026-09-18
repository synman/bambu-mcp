"""
job_project.py — persist the running job's resolved project path, per printer.

bpm learns which .3mf a job prints from the `project_file` command frame at
print start and keeps it only in memory (`ActiveJobInfo.project_info`). A print
started at the printer's own screen reports no `subtask_name`, so after a
daemon restart bpm cannot re-derive the file and every consumer that keys on
the job's project — the stream HUD's plate panels first of all — falls back to
whatever it last cached.

Pattern (bambu-mcp CLAUDE.md — Filesystem Persistence):
  ~/.bambu-mcp/active_project_<printer>.json — saved when a job's project is
  known, read on demand, cleared when the job ends.

Identity: the record carries the job's `wall_start_time`, which bpm itself
persists and restores across restarts, so a stale record from an earlier job
never matches a newer one.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

_DIR = Path.home() / ".bambu-mcp"
_IDENTITY_TOLERANCE_S = 1.0


def _path(name: str) -> Path:
    return _DIR / f"active_project_{name.replace(' ', '_')}.json"


def _project_id(job) -> str:
    pi = getattr(job, "project_info", None)
    if pi is None:
        return ""
    if isinstance(pi, dict):
        return str(pi.get("id") or "")
    return str(getattr(pi, "id", "") or "")


def _plate_num(job) -> int:
    pi = getattr(job, "project_info", None)
    for candidate in (
        pi.get("plate_num") if isinstance(pi, dict) else getattr(pi, "plate_num", None),
        getattr(job, "plate_num", None),
    ):
        try:
            if candidate is not None and int(candidate) > 0:
                return int(candidate)
        except (TypeError, ValueError):
            continue
    return 1


def remember(name: str, job) -> bool:
    """Persist job's project path when bpm knows it. Returns True when a record
    is on disk afterwards (written now or already identical)."""
    project_id = _project_id(job)
    if not project_id:
        return False
    try:
        wall_start = float(getattr(job, "wall_start_time", -1.0) or -1.0)
    except (TypeError, ValueError):
        wall_start = -1.0
    record = {
        "id": project_id,
        "plate_num": _plate_num(job),
        "wall_start_time": wall_start,
        "gcode_file": str(getattr(job, "gcode_file", "") or ""),
    }
    path = _path(name)
    try:
        if path.exists() and json.loads(path.read_text()) == record:
            return True
    except (OSError, ValueError):
        pass
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_DIR, prefix=".active_project-")
        with os.fdopen(fd, "w") as fh:
            json.dump(record, fh)
        os.replace(tmp, path)
        log.info("job_project: remembered %s → %s plate %s", name, project_id, record["plate_num"])
        return True
    except OSError as exc:
        log.warning("job_project: could not persist %s: %s", path, exc)
        return False


def recall(name: str, job) -> tuple[str, int] | None:
    """Return (project_path, plate_num) persisted for THIS job, or None.

    The record must carry the same wall_start_time as the live job; anything
    else is a different job's leftover and is ignored."""
    path = _path(name)
    try:
        record = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        log.warning("job_project: unreadable %s: %s", path, exc)
        return None
    try:
        live = float(getattr(job, "wall_start_time", -1.0) or -1.0)
        stored = float(record.get("wall_start_time", -1.0))
        project_id = str(record.get("id") or "")
        plate_num = int(record.get("plate_num") or 1)
    except (TypeError, ValueError):
        return None
    if not project_id or live <= 0 or stored <= 0 or abs(live - stored) > _IDENTITY_TOLERANCE_S:
        return None
    return project_id, plate_num


def forget(name: str) -> None:
    """Remove the record; called when the job finishes or fails."""
    try:
        _path(name).unlink(missing_ok=True)
    except OSError as exc:
        log.warning("job_project: could not remove %s: %s", _path(name), exc)
