"""Tests for print_file's live-spool ams_mapping resolution (tools/files.py).

bpm's get_project_info() cannot supply a real AMS mapping: the 3mf's
`filament_maps` is the slicer's per-filament EXTRUDER assignment and bpm
replaces it with a filament-id placeholder ("1", "2", ...). Until 2026-09-15
print_file sent that placeholder to the printer verbatim when use_ams=True
and no caller mapping was given. It now mirrors bambu-printer-app's
Print3mfFileDialog scoring (reconstructAmsMapping / getProtocolTrayId /
toWireMapping / getMatchQuality): match each project filament to a loaded
spool by exact type then closest colour, encode the tray id per AMS type, and
emit a filament-id-indexed array gap-filled with -1. Assignment runs
exact-first, then best-unused, then reuse. A filament with no loaded match, a
plate with no filament metadata, or an unreadable 3mf REFUSES the print
instead of guessing. preview_ams_mapping() exposes the same resolution
read-only so the pre-print summary can show it.

The end-to-end tests stub session_manager and bpm.bambuproject.get_project_info
so nothing touches a printer. Against the pre-fix copy the first e2e test
fails because the placeholder '["1", "3"]' is sent instead of '[1, -1, 128]'.

Run directly (no pytest needed):  .venv/bin/python3 test_print_file_ams_mapping.py
"""

import sys
import types
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools import files as files_mod  # noqa: E402

resolve = files_mod._resolve_ams_mapping_from_spools
tray_id = files_mod._protocol_tray_id


@dataclass
class Spool:
    ams_id: int
    slot_id: int
    type: str
    color: str
    state: int = 1


def F(fid, ftype, color):
    return {"id": fid, "type": ftype, "color": color}


# ── resolver ────────────────────────────────────────────────────────────────

def test_h2d_two_units_exact_matches():
    # AMS 2 Pro (ams_id 0) slot 1 = tray 1; AMS HT (ams_id 128) slot 0 = tray 128.
    spools = [
        Spool(0, 0, "PLA", "#FF0000FF"),
        Spool(0, 1, "ABS", "black"),          # bpm stores an exact CSS3 hit by name
        Spool(128, 0, "ABS-S", "#FFFFFFFF"),  # and everything else as #RRGGBBAA
    ]
    wire, matches, unmatched = resolve([F(1, "ABS", "#000000"), F(2, "ABS-S", "#FFFFFF")], spools)
    assert wire == [1, 128], wire
    assert unmatched == []
    assert [m["quality"] for m in matches] == ["excellent", "excellent"]
    assert [m["pass"] for m in matches] == ["exact", "exact"]


def test_filament_id_gaps_are_filled_with_minus_one():
    spools = [Spool(0, 0, "PLA", "#FF0000"), Spool(0, 2, "ABS", "#000000")]
    wire, _, unmatched = resolve([F(1, "PLA", "#FF0000"), F(3, "ABS", "#000000")], spools)
    assert wire == [0, -1, 2], wire
    assert unmatched == []


def test_type_mismatch_with_close_colour_is_accepted():
    # PETG loaded, project wants PLA of a near-identical colour (RGB dist ~14 < 60).
    wire, matches, unmatched = resolve([F(1, "PLA", "#FF0000")], [Spool(0, 3, "PETG", "#FF0A0A")])
    assert wire == [3], wire
    assert unmatched == []
    assert matches[0]["quality"] == "poor" and matches[0]["label"] == "Color Match Only"


def test_type_mismatch_with_far_colour_is_unmatched():
    wire, _, unmatched = resolve([F(1, "PLA", "#FF0000")], [Spool(0, 3, "PETG", "#0000FF")])
    assert wire == [-1], wire
    assert [f["id"] for f in unmatched] == [1]


def test_same_type_far_colour_is_a_type_match():
    # BPA rule: a type match is always acceptable; colour only ranks. Labelled so
    # the pre-print summary can call it out.
    wire, matches, _ = resolve([F(1, "PLA", "#FF0000")], [Spool(0, 0, "PLA", "#0000FF")])
    assert wire == [0], wire
    assert matches[0]["quality"] == "fair" and matches[0]["label"] == "Type Match"


def test_type_compare_is_case_insensitive():
    # Third-party spools are often typed in lowercase on the printer display.
    spools = [Spool(0, 0, "pla", "#FF0000"), Spool(0, 1, "PLA", "#FE0000")]
    wire, matches, _ = resolve([F(1, "PLA", "#FF0000")], spools)
    assert wire == [0], wire
    assert matches[0]["pass"] == "exact"


def test_exact_pairs_are_assigned_before_greedy_choices():
    # Review finding: single greedy pass gave filament 1 (PLA blue) the PLA RED spool
    # (type match beats an exact-colour PETG) and stranded filament 2. Exact-first
    # yields the assignment that exists: f2 → tray 0 exact, f1 → tray 1 colour-only.
    filaments = [F(1, "PLA", "#0000FF"), F(2, "PLA", "#FF0000")]
    spools = [Spool(0, 0, "PLA", "#FF0000"), Spool(0, 1, "PETG", "#0000FF")]
    wire, matches, unmatched = resolve(filaments, spools)
    assert wire == [1, 0], wire
    assert unmatched == []
    by_id = {m["id"]: m for m in matches}
    assert by_id[2]["pass"] == "exact" and by_id[1]["pass"] == "best-unused"


def test_one_spool_is_reused_for_two_filaments_of_the_same_material():
    # Two project filament slots, one loaded spool of that material: map both to
    # it (the slicer emits duplicate trays itself) rather than refusing.
    spools = [Spool(0, 0, "PLA", "#FF0000")]
    wire, matches, unmatched = resolve([F(1, "PLA", "#FF0000"), F(2, "PLA", "#FF0000")], spools)
    assert wire == [0, 0], wire
    assert unmatched == []
    by_id = {m["id"]: m for m in matches}
    assert by_id[1]["reused"] is False and by_id[2]["reused"] is True and by_id[2]["pass"] == "reuse"


def test_reuse_still_requires_an_acceptable_score():
    spools = [Spool(0, 0, "PLA", "#FF0000")]
    wire, _, unmatched = resolve([F(1, "PLA", "#FF0000"), F(2, "PETG", "#0000FF")], spools)
    assert wire == [0, -1], wire
    assert [f["id"] for f in unmatched] == [2]


def test_prefers_closest_colour_among_type_matches():
    spools = [Spool(0, 0, "PLA", "#FF0000"), Spool(0, 1, "PLA", "#800000")]
    wire, _, _ = resolve([F(1, "PLA", "#7F0000")], spools)
    assert wire == [1], wire


def test_external_empty_and_untyped_spools_are_not_candidates():
    spools = [
        Spool(-1, 254, "PLA", "#FF0000"),           # external spool holder
        Spool(0, 0, "PLA", "#FF0000", state=0),     # empty slot
        Spool(0, 1, "", "#FF0000"),                 # no type read
    ]
    wire, _, unmatched = resolve([F(1, "PLA", "#FF0000")], spools)
    assert wire == [-1], wire
    assert [f["id"] for f in unmatched] == [1]


def test_unparseable_spool_colour_is_not_chosen():
    spools = [Spool(0, 0, "PLA", ""), Spool(0, 1, "PLA", "#FF0000")]
    wire, _, _ = resolve([F(1, "PLA", "#FF0000")], spools)
    assert wire == [1], wire


def test_filament_without_type_or_colour_is_unmatched():
    wire, _, unmatched = resolve([F(1, "", "#FF0000"), F(2, "PLA", "")], [Spool(0, 0, "PLA", "#FF0000")])
    assert wire == [-1, -1], wire
    assert [f["id"] for f in unmatched] == [1, 2]


def test_filament_with_unusable_id_is_unmatched_not_dropped():
    # bpm emits id -1 when a slice_info filament node has no id attribute.
    wire, _, unmatched = resolve([F(-1, "PLA", "#FF0000")], [Spool(0, 0, "PLA", "#FF0000")])
    assert wire == [], wire
    assert [f["id"] for f in unmatched] == [-1]


def test_protocol_tray_id_encoding():
    assert tray_id(Spool(0, 1, "PLA", "")) == 1
    assert tray_id(Spool(1, 2, "PLA", "")) == 6        # second 4-slot unit
    assert tray_id(Spool(128, 0, "PLA", "")) == 128    # AMS HT: ams_id + slot, NOT ams_id*4
    assert tray_id(Spool(129, 0, "PLA", "")) == 129
    assert tray_id(Spool(-1, 254, "PLA", "")) is None  # external
    assert tray_id(Spool(0, -1, "PLA", "")) is None


# ── print_file / preview_ams_mapping end-to-end (stubbed) ───────────────────

class _Printer:
    def __init__(self, spools):
        self.printer_state = types.SimpleNamespace(gcode_state="IDLE", spools=spools)
        self.calls = []

    def print_3mf_file(self, **kw):
        self.calls.append(kw)


def _stubbed(spools, filaments, gpi=None):
    """Context: session_manager + bpm.bambuproject.get_project_info stubbed."""
    import bpm.bambuproject as bp
    printer = _Printer(spools)
    sm = files_mod.session_manager
    orig = (sm.get_printer, bp.get_project_info)
    sm.get_printer = lambda name: printer
    bp.get_project_info = gpi or (
        lambda path, p, plate_num=1: types.SimpleNamespace(
            metadata={"filament": filaments, "ams_mapping": [str(f["id"]) for f in filaments]}
        )
    )

    def restore():
        sm.get_printer, bp.get_project_info = orig

    return printer, restore


def _run(spools, filaments, gpi=None, **kw):
    printer, restore = _stubbed(spools, filaments, gpi)
    try:
        result = files_mod.print_file("h2d", "/x.gcode.3mf", plate_num=1, user_permission=True, **kw)
    finally:
        restore()
    return printer, result


def _preview(spools, filaments, gpi=None):
    printer, restore = _stubbed(spools, filaments, gpi)
    try:
        return files_mod.preview_ams_mapping("h2d", "/x.gcode.3mf", plate_num=1)
    finally:
        restore()


def test_print_file_sends_live_mapping_not_the_placeholder():
    spools = [Spool(0, 1, "ABS", "black"), Spool(128, 0, "ABS-S", "#FFFFFFFF")]
    printer, result = _run(spools, [F(1, "ABS", "#000000"), F(3, "ABS-S", "#FFFFFF")], use_ams=True)
    assert result.get("success") is True, result
    assert len(printer.calls) == 1
    assert printer.calls[0]["ams_mapping"] == "[1, -1, 128]", printer.calls[0]
    assert printer.calls[0]["use_ams"] is True
    assert result["ams_mapping"] == "[1, -1, 128]"
    assert [m["label"] for m in result["matches"]] == ["Excellent Match", "Excellent Match"]


def test_print_file_refuses_when_a_filament_has_no_loaded_match():
    # Only PETG blue loaded; project wants PLA red — type mismatch AND far colour.
    spools = [Spool(0, 0, "PETG", "#0000FF"), Spool(0, 1, "", "", state=0), Spool(-1, 254, "PLA", "#FF0000")]
    printer, result = _run(spools, [F(1, "PLA", "#FF0000")], use_ams=True)
    assert printer.calls == [], "print must not start on an unmatched filament"
    assert "No loaded AMS spool matches" in result["error"], result
    assert result["resolved_ams_mapping"] == [-1]
    # Diagnostics list only spools that could be printed from, and the external
    # spool separately — never an empty slot.
    assert result["loaded_spools"] == [{"tray_id": 0, "type": "PETG", "color": "#0000FF"}]
    assert result["external_spools"] == [{"slot_id": 254, "type": "PLA", "color": "#FF0000"}]


def test_print_file_same_type_far_colour_prints_and_reports_type_match():
    printer, result = _run([Spool(0, 0, "PLA", "#0000FF")], [F(1, "PLA", "#FF0000")], use_ams=True)
    assert result.get("success") is True, result
    assert printer.calls[0]["ams_mapping"] == "[0]"
    assert result["matches"][0]["label"] == "Type Match"


def test_print_file_refuses_when_project_info_is_none():
    # bpm returns None (no raise) for a plate the file does not contain.
    printer, result = _run([Spool(0, 0, "PLA", "#FF0000")], [], gpi=lambda *a, **k: None, use_ams=True)
    assert printer.calls == []
    assert "no filament metadata" in result["error"], result


def test_print_file_refuses_when_metadata_has_no_filaments():
    printer, result = _run(
        [Spool(0, 0, "PLA", "#FF0000")], [],
        gpi=lambda *a, **k: types.SimpleNamespace(metadata={"map": {}}), use_ams=True,
    )
    assert printer.calls == []
    assert "no filament metadata" in result["error"], result


def test_print_file_refuses_when_every_filament_id_is_unusable():
    printer, result = _run([Spool(0, 0, "PLA", "#FF0000")], [F(-1, "PLA", "#FF0000")], use_ams=True)
    assert printer.calls == [], "an empty wire array must not be sent"
    assert "No loaded AMS spool matches" in result["error"], result


def test_print_file_caller_mapping_bypasses_resolution():
    def boom(*a, **k):
        raise AssertionError("metadata must not be read when a mapping is supplied")
    printer, result = _run([], [], gpi=boom, ams_mapping=[1, -1, 128], use_ams=False)
    assert result.get("success") is True, result
    assert printer.calls[0]["ams_mapping"] == "[1, -1, 128]"
    assert printer.calls[0]["use_ams"] is True


def test_print_file_empty_string_mapping_means_not_provided():
    # BPA's own URL sends ams_mapping= ; an empty override must resolve, not publish "".
    printer, result = _run([Spool(0, 1, "ABS", "black")], [F(1, "ABS", "#000000")], ams_mapping="", use_ams=True)
    assert result.get("success") is True, result
    assert printer.calls[0]["ams_mapping"] == "[1]"


def test_print_file_refuses_when_metadata_unreadable():
    def boom(*a, **k):
        raise OSError("FTPS timeout")
    printer, result = _run([Spool(0, 0, "PLA", "#FF0000")], [], gpi=boom, use_ams=True)
    assert printer.calls == []
    assert "Could not read project metadata" in result["error"], result


def test_print_file_use_ams_false_sends_empty_mapping():
    def boom(*a, **k):
        raise AssertionError("metadata must not be read when use_ams=False")
    printer, result = _run([], [], gpi=boom, use_ams=False)
    assert result.get("success") is True, result
    assert printer.calls[0]["ams_mapping"] == ""
    assert printer.calls[0]["use_ams"] is False


def test_preview_matches_what_print_file_sends_and_never_prints():
    spools = [Spool(0, 1, "ABS", "black"), Spool(128, 0, "ABS-S", "#FFFFFFFF")]
    filaments = [F(1, "ABS", "#000000"), F(3, "ABS-S", "#FFFFFF")]
    printer, restore = _stubbed(spools, filaments)
    try:
        preview = files_mod.preview_ams_mapping("h2d", "/x.gcode.3mf", plate_num=1)
    finally:
        restore()
    assert printer.calls == []
    assert "error" not in preview
    assert preview["resolved_ams_mapping"] == [1, -1, 128]
    assert preview["ams_mapping_json"] == "[1, -1, 128]"
    assert [m["tray_id"] for m in preview["matches"]] == [1, 128]
    _, result = _run(spools, filaments, use_ams=True)
    assert result["ams_mapping"] == preview["ams_mapping_json"]


def test_preview_reports_the_refusal_print_file_would_give():
    preview = _preview([Spool(0, 0, "PETG", "#0000FF")], [F(1, "PLA", "#FF0000")])
    assert "No loaded AMS spool matches" in preview["error"]
    assert preview["unmatched"] == [F(1, "PLA", "#FF0000")]


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
