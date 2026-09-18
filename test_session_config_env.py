"""Tests for the env-backed BambuConfig overrides in session_manager.py.

Why it exists: bpm exposes `ftps_connection_timeout` (bambuconfig.py:81, default
15s) which bounds the FTPS control-connection handshake behind every SD-card file
operation. bambu-mcp built BambuConfig with a fixed kwarg list, so the knob was
unreachable from this interface — bpm gained a capability no consumer could pass.

The tests use the REAL BambuConfig, never a stub, so a renamed or removed bpm
field fails here as a TypeError instead of silently going back to unreachable.

Run directly (no pytest needed):  .venv/bin/python3 test_session_config_env.py
"""

import os
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import session_manager  # noqa: E402

_VAR = "BAMBU_MCP_FTPS_TIMEOUT"


def _set(value):
    if value is None:
        os.environ.pop(_VAR, None)
    else:
        os.environ[_VAR] = value


def _bpm_default() -> int:
    session_manager._ensure_imports()
    return session_manager._BambuConfig(
        hostname="h", access_code="a", serial_number="s"
    ).ftps_connection_timeout


# --- the parser ------------------------------------------------------------


def test_unset_yields_no_override():
    _set(None)
    assert session_manager._bpm_config_overrides() == {}


def test_valid_value_becomes_an_override():
    _set("45")
    assert session_manager._bpm_config_overrides() == {"ftps_connection_timeout": 45}
    _set(None)


def test_surrounding_whitespace_is_tolerated():
    _set("  45\n")
    assert session_manager._bpm_config_overrides() == {"ftps_connection_timeout": 45}
    _set(None)


def test_junk_and_nonpositive_fall_back_to_bpm():
    for bad in ("", "   ", "abc", "15s", "0", "-5", "1.5"):
        _set(bad)
        assert session_manager._bpm_config_overrides() == {}, f"{bad!r} should not override"
    _set(None)


# --- the route: does the override reach a real BambuConfig? ----------------


def test_override_reaches_a_real_bambuconfig():
    session_manager._ensure_imports()   # never rely on another test having run
    _set("45")
    cfg = session_manager._BambuConfig(
        hostname="h", access_code="a", serial_number="s",
        **session_manager._bpm_config_overrides(),
    )
    assert cfg.ftps_connection_timeout == 45
    _set(None)


def _capture_start_printer_config(env_value):
    """Run _start_printer with stubs and return the BambuConfig it built."""
    session_manager._ensure_imports()
    _set(env_value)
    seen = {}
    real_config = session_manager._BambuConfig
    real_printer = session_manager._BambuPrinter
    real_creds = session_manager.auth.get_printer_credentials

    def _spy_config(**kwargs):
        cfg = real_config(**kwargs)          # real dataclass — rejects bad field names
        seen["config"] = cfg
        return cfg

    session_manager.auth.get_printer_credentials = lambda name: {
        "ip": "10.0.0.1", "access_code": "code", "serial": "SER1"
    }
    session_manager._BambuConfig = _spy_config
    session_manager._BambuPrinter = lambda config: types.SimpleNamespace(
        on_update=None, start_session=lambda: None, quit=lambda: None
    )
    try:
        session_manager.SessionManager()._start_printer("unit-test-printer")
    finally:
        session_manager._BambuConfig = real_config
        session_manager._BambuPrinter = real_printer
        session_manager.auth.get_printer_credentials = real_creds
        _set(None)
    return seen["config"]


def test_start_printer_passes_the_env_timeout():
    cfg = _capture_start_printer_config("45")
    assert cfg.ftps_connection_timeout == 45
    assert cfg.hostname == "10.0.0.1"
    assert cfg.serial_number == "SER1"


def test_start_printer_without_the_env_keeps_the_bpm_default():
    """Read the default live from bpm, never a literal.

    This is the drift detector for the pair: if bpm ever moves its default off
    15 and this repo has mirrored the old value, the comparison goes red here.
    """
    cfg = _capture_start_printer_config(None)
    assert cfg.ftps_connection_timeout == _bpm_default()


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
