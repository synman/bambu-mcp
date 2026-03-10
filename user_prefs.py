"""
Per-printer sticky preference store for bambu-mcp.

Preferences are stored in ~/.bambu-mcp/user_prefs.json as a plain JSON file.
Keys use the pattern "{printer_name}:{field}" (e.g. "H2D:bed_leveling") or
"{printer_name}:ams{unit_id}:{field}" for AMS-specific fields.

Thread-safety: a threading.Lock guards all reads and writes within a process.
Atomic write pattern (write → fsync → rename) prevents partial-write corruption.
"""

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_PREFS_PATH = Path.home() / ".bambu-mcp" / "user_prefs.json"
_lock = threading.Lock()


def _load() -> dict:
    """Return the preferences dict from disk, or {} on missing/corrupt file."""
    if not _PREFS_PATH.exists():
        return {}
    try:
        return json.loads(_PREFS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("user_prefs: failed to load %s: %s", _PREFS_PATH, e)
        return {}


def _save(data: dict) -> None:
    """Atomically write *data* to _PREFS_PATH (write → fsync → rename)."""
    _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=_PREFS_PATH.parent, prefix=".user_prefs_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, _PREFS_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_pref(key: str, default=None):
    """Load ~/.bambu-mcp/user_prefs.json and return the value for *key*, or *default*."""
    with _lock:
        return _load().get(key, default)


def set_pref(key: str, value) -> None:
    """Save/update *key* → *value* in ~/.bambu-mcp/user_prefs.json."""
    with _lock:
        data = _load()
        data[key] = value
        _save(data)


def delete_pref(key: str) -> bool:
    """Delete *key* from the store. Returns True if the key existed."""
    with _lock:
        data = _load()
        if key not in data:
            return False
        del data[key]
        _save(data)
        return True


def get_all_prefs() -> dict:
    """Return a copy of the full preferences dict."""
    with _lock:
        return dict(_load())
