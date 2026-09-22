"""Tests for camera/status_helpers.py get_active_spool — the HUD's active-filament lookup.

get_active_spool used to try a unit-qualified match, then fall back to matching the spool id
alone. bpm reports the active tray two ways: a single-extruder printer's active_tray_id is the
raw ``tray_now``, an ABSOLUTE tray id (unit n slot s -> 4n+s, the spool's own ``id``), while a
dual-extruder printer's is the slot inside the active unit (the H2D's AMS HT reads
active_ams_id 128 with active_tray_id 0). So the id-only fallback is right for neither
identically: on an H2D with the AMS HT feeding and no HT spool in the list it returned the AMS 2
Pro slot-0 spool (id 0), a filament that is not loaded. It also returned None for the right
external holder (255) and returned bpm's empty placeholder for an empty 254.

The rule is now the one tools.state.get_spool_info uses: no active tray -> None; otherwise a spool
matches when it sits in the active unit (ams_id == active_ams_id) and the tray names it by slot or,
for a real AMS tray, by absolute id; 254/255 match by slot alone. The last group of tests runs
both functions over one table of states and asserts they agree. The rule is written twice on
purpose: every documented function in tools/*.py is registered as an MCP tool, so a shared helper
there would add a tool, and camera/ code does not import tools/state.py.

These tests use real bpm BambuState / BambuSpool objects and stub only tools.state's session
manager. Nothing here touches a printer, the network or the daemon.

Run directly (no pytest needed):  .venv/bin/python3 test_status_helpers.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bpm.bambuspool import BambuSpool  # noqa: E402
from bpm.bambustate import BambuState  # noqa: E402
from camera.status_helpers import build_active_filament, get_active_spool  # noqa: E402
from tools import state as state_mod  # noqa: E402


def _tray(unit_ams_id, slot, type_="PLA", color="#FF0000"):
    """A real AMS tray the way bpm builds it: id is the absolute id (4n+slot; HT unit 128 -> 16+slot)."""
    tray_id = 16 + slot if unit_ams_id >= 128 else unit_ams_id * 4 + slot
    return BambuSpool(id=tray_id, slot_id=slot, ams_id=unit_ams_id, type=type_, color=color)


def _unit(unit_ams_id, type_="PLA"):
    return [_tray(unit_ams_id, s, type_=type_) for s in range(4)]


def _external(holder_id, type_="PETG"):
    """A loaded external holder: slot_id is the holder id, ams_id -1 (bpm's tray-loaded branch)."""
    return BambuSpool(id=holder_id, slot_id=holder_id, ams_id=-1, type=type_, color="#00FF00")


def _placeholder(holder_id):
    """bpm's empty external holder: BambuSpool(holder_id) leaves slot_id and ams_id at -1."""
    return BambuSpool(holder_id)


def _state(active_ams_id, active_tray_id, spools):
    return BambuState(active_ams_id=active_ams_id, active_tray_id=active_tray_id, spools=list(spools))


def _identity(spool):
    return None if spool is None else (spool.id, spool.slot_id, spool.ams_id)


# name -> (state, expected identity as (id, slot_id, ams_id) or None)
def _cases():
    ams0, ams1, ams2, ht = _unit(0), _unit(1), _unit(2), _unit(128, "ABS")
    return {
        # single extruder: active_tray_id is the absolute tray id, active_ams_id = tray >> 2
        "single_unit0_slot2": (_state(0, 2, ams0), (2, 2, 0)),
        "single_second_unit_by_absolute_id": (_state(1, 6, ams0 + ams1), (6, 2, 1)),
        "single_third_unit_by_absolute_id": (_state(2, 9, ams0 + ams1 + ams2), (9, 1, 2)),
        # dual extruder: active_tray_id is the slot inside the unit assigned to the extruder
        "h2d_ams2pro_slot1": (_state(0, 1, ams0 + ht), (1, 1, 0)),
        "h2d_ht_slot0_present": (_state(128, 0, ams0 + ht), (16, 0, 128)),
        "h2d_ht_slot2_present": (_state(128, 2, ams0 + ht), (18, 2, 128)),
        "h2d_second_unit_by_slot": (_state(1, 2, ams0 + ams1), (6, 2, 1)),
        # the wrong-unit trap: HT active, tray 0, no HT spool listed -> AMS 2 Pro slot 0 (id 0) must not be returned
        "h2d_ht_active_no_ht_spool_listed": (_state(128, 0, ams0), None),
        "h2d_ht_slot3_active_no_ht_spool_listed": (_state(128, 3, ams0), None),
        # nothing active
        "no_active_tray": (_state(-1, -1, ams0 + [_placeholder(254), _placeholder(255)]), None),
        "no_active_tray_stale_unit": (_state(0, -1, ams0), None),
        "no_spools": (_state(0, 1, []), None),
        # external holders match by slot alone, in the ams_id -1 group
        "external_254_loaded": (_state(-1, 254, ams0 + [_external(254), _placeholder(255)]), (254, 254, -1)),
        "external_255_loaded": (_state(-1, 255, ams0 + [_placeholder(254), _external(255)]), (255, 255, -1)),
        "external_254_empty_placeholder": (_state(-1, 254, ams0 + [_placeholder(254)]), None),
        "external_255_empty_placeholder": (_state(-1, 255, ams0 + [_placeholder(255)]), None),
    }


def _check(name):
    state, want = _cases()[name]
    got = get_active_spool(state)
    assert _identity(got) == want, f"{name}: got {_identity(got)}, want {want}"


def test_single_unit0_slot2():
    _check("single_unit0_slot2")


def test_single_second_unit_by_absolute_id():
    _check("single_second_unit_by_absolute_id")


def test_single_third_unit_by_absolute_id():
    _check("single_third_unit_by_absolute_id")


def test_h2d_ams2pro_slot1():
    _check("h2d_ams2pro_slot1")


def test_h2d_ht_slot0_present():
    _check("h2d_ht_slot0_present")


def test_h2d_ht_slot2_present():
    _check("h2d_ht_slot2_present")


def test_h2d_second_unit_by_slot():
    _check("h2d_second_unit_by_slot")


def test_h2d_ht_active_no_ht_spool_listed_is_none_not_ams2pro_slot0():
    _check("h2d_ht_active_no_ht_spool_listed")
    _check("h2d_ht_slot3_active_no_ht_spool_listed")


def test_no_active_tray_is_none():
    _check("no_active_tray")
    _check("no_active_tray_stale_unit")
    _check("no_spools")


def test_external_254_loaded_and_empty():
    _check("external_254_loaded")
    _check("external_254_empty_placeholder")


def test_external_255_loaded_and_empty():
    _check("external_255_loaded")
    _check("external_255_empty_placeholder")


def test_state_without_the_fields_is_none():
    class Bare:
        pass
    assert get_active_spool(Bare()) is None


def test_build_active_filament_follows_the_lookup():
    ams0, ht = _unit(0), _unit(128, "ABS")
    none = build_active_filament(_state(128, 0, ams0))
    assert none is None, f"HT active, no HT spool listed: got {none}"
    got = build_active_filament(_state(128, 1, ams0 + ht))
    assert got == {"type": "ABS", "color": "#FF0000", "remaining_pct": 0}, got


def _colour_of(colour):
    """build_active_filament's colour for an active spool that carries ``colour``."""
    spool = BambuSpool(id=0, slot_id=0, ams_id=0, type="PLA", color=colour)
    got = build_active_filament(_state(0, 0, [spool]))
    assert got is not None, colour
    return got["color"]


# What the stream HUD is handed as a swatch colour. The colour ends up inside a style attribute, so it
# leaves this function as a hex string or as nothing. bpm stores a spool colour as the CSS3 name when
# webcolors knows the hex ("white", "black", "gray") and as "#" + the raw tray_color when it does not,
# so a CSS3 name is resolved to its hex here rather than dropped: dropping it would grey out every
# white and black spool. Anything else, including bpm's raw "#" + tray_color text, becomes "".
_COLOUR_TABLE = [
    ("#1a2b3c", "#1a2b3c"),                  # 6 hex digits
    ("#1A2B3C", "#1A2B3C"),
    ("#1A2B3CFF", "#1A2B3CFF"),              # 8 hex digits (RRGGBBAA)
    ("1a2b3c", "#1a2b3c"),                   # bare hex gets the "#"
    ("1A2B3CFF", "#1A2B3CFF"),
    ("red", "#ff0000"),                      # a CSS3 name bpm produced from an exact hex match
    ("white", "#ffffff"),
    ("Gray", "#808080"),
    ("zzzzzz", ""),                          # not hex, not a name
    ("#12345", ""),                          # 5 digits
    ("#1234567", ""),                        # 7 digits
    ("#1a2b3c\n", ""),                       # a trailing newline is not a colour
    (" #1a2b3c", ""),
    ('"><img src=x onerror=alert(1)>', ""),
    ('#1a2b3c"><img src=x onerror=alert(1)>', ""),
    ('" onmouseover="alert(2)', ""),
    ("#1a2b3c;background:url(javascript:alert(1))", ""),
    ("red;background:url(javascript:alert(1))", ""),
    ("notacolor", ""),
    ("", ""),
    (None, ""),
    (123456, ""),
]


def _colour_or_error(raw):
    try:
        return _colour_of(raw)
    except Exception as exc:  # noqa: BLE001 - a crash is a wrong answer here, and the table must list it
        return f"raised {type(exc).__name__}"


def test_build_active_filament_colour_is_hex_or_empty():
    got = [(raw, want, _colour_or_error(raw)) for raw, want in _COLOUR_TABLE]
    bad = [row for row in got if row[1] != row[2]]
    assert not bad, "colour table mismatches (input, want, got): " + repr(bad)


def test_build_active_filament_colour_never_carries_markup():
    for raw, _want in _COLOUR_TABLE:
        got = _colour_or_error(raw)
        assert got == "" or (got.startswith("#") and len(got) in (7, 9)
                             and all(c in "0123456789abcdefABCDEF" for c in got[1:])), (raw, got)


def test_build_active_filament_ordinary_spool_is_unchanged():
    spool = BambuSpool(id=0, slot_id=0, ams_id=0, type="PLA", color="#1a2b3c", remaining_percent=54)
    got = build_active_filament(_state(0, 0, [spool]))
    assert got == {"type": "PLA", "color": "#1a2b3c", "remaining_pct": 54}, got


def test_agrees_with_tools_state_get_spool_info_on_every_case():
    """The two lookups must never disagree: same states, same answer, including the None cases."""
    class _Manager:
        def __init__(self):
            self.state = None

        def get_state(self, name):
            return self.state

    mgr = _Manager()
    real = state_mod.session_manager
    state_mod.session_manager = mgr
    try:
        for name, (state, want) in _cases().items():
            mgr.state = state
            info = state_mod.get_spool_info("unit-test-printer")
            assert "error" not in info, (name, info)
            active = info["active_spool"]
            theirs = None if active is None else (active["id"], active["slot_id"], active["ams_id"])
            ours = _identity(get_active_spool(state))
            assert theirs == ours == want, f"{name}: get_spool_info={theirs} get_active_spool={ours} want={want}"
    finally:
        state_mod.session_manager = real


if __name__ == "__main__":
    import traceback
    failed = 0
    for tname, fn in sorted(globals().items()):
        if tname.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {tname}")
            except Exception as exc:
                failed += 1
                print(f"FAIL {tname}: {exc}")
                traceback.print_exc()
    print(f"{failed} failed")
    sys.exit(1 if failed else 0)
