"""Regression tests for two tools/files.py defects.

Defect 8 (open_plate_viewer): the plate count was taken as the LENGTH of plate 1's
`plates` list and plate numbers 1..count were fetched. A .3mf may carry a sparse plate
set such as [1, 5, 6, 12] (plates deleted in the slicer), so real plates above the count
never appeared, and the absent numbers in 1..count were filled by bpm's silent fallback
to another plate, so plate 1's images rendered under the headings "Plate 2", "Plate 3"
and "Plate 4". bpm's own get_all_project_info reads the actual plate set; the viewer now
uses it, exactly as the get_all_project_info tool does.

Defect 9 (refresh_sdcard): the tool discarded the return value of get_sdcard_contents()
and get_sdcard_3mf_files() and reported success whenever nothing raised. bpm reports a
failed listing by returning None (it also clears both cached trees), which list_sdcard_files,
get_file_info and the two get_3mf_entry_* tools already turn into an error.

These tests drive the REAL bpm BambuPrinter, get_sdcard_contents, get_project_info and
get_all_project_info against a REAL .3mf zip. Only the FTPS client (printer I/O),
session_manager and webbrowser are stubbed. Nothing touches a printer or the network.

Run directly (no pytest needed):  .venv/bin/python3 test_files_defects.py
"""

import base64
import contextlib
import datetime
import io
import re
import shutil
import sys
import tempfile
import types
import webbrowser
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpm.bambuprinter as bpm_printer  # noqa: E402
from bpm.bambuconfig import BambuConfig  # noqa: E402
from bpm.ftpsclient.ftpsclient import FtpListItem  # noqa: E402
from PIL import Image  # noqa: E402

from tools import files as files_mod  # noqa: E402

PRINTER = "bambu_mcp_test_files_defects"
VIEWER_HTML = Path(f"/tmp/plate_viewer_{PRINTER}.html")
LIST_FAILED = "Failed to retrieve SD card contents"


# ── fixtures ────────────────────────────────────────────────────────────────


def _png_uri(rgb) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), rgb).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _png_bytes(rgb) -> bytes:
    return base64.b64decode(_png_uri(rgb).split(",", 1)[1])


def _build_3mf(plate_nums, with_images=True) -> bytes:
    """A minimal but real sliced .3mf holding exactly `plate_nums`, each plate carrying its
    own object name (part_<N>) and its own image colour so plates are distinguishable."""
    slice_plates = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in plate_nums:
            slice_plates.append(
                f'<plate><metadata key="index" value="{n}"/>'
                f'<object identify_id="{100 + n}" name="part_{n}.stl"/>'
                f'<filament id="1" type="PLA" color="#FF0000"/></plate>'
            )
            zf.writestr(
                f"Metadata/plate_{n}.json",
                '{"bbox_objects": [{"name": "part_%d.stl", "bbox": [10, 10, 60, 60]}],'
                ' "filament_ids": [0], "filament_colors": ["#FF0000"]}' % n,
            )
            if with_images:
                zf.writestr(f"Metadata/plate_{n}.png", _png_bytes((n * 10 % 256, 40, 200)))
                zf.writestr(f"Metadata/top_{n}.png", _png_bytes((200, n * 10 % 256, 40)))
        zf.writestr("Metadata/slice_info.config", "<config>" + "".join(slice_plates) + "</config>")
    return buf.getvalue()


class _Card:
    """Stands in for the printer's FTPS server. `client_class` replaces bpm's IoTFTPSClient,
    so the real BambuPrinter FTPS code paths run against it."""

    def __init__(self, files=None, connect_error=None):
        self.files = dict(files or {})  # root-level path -> bytes
        self.connect_error = connect_error
        self.connects = 0
        self.downloads = []
        self.listings = 0
        self.listing_fails = False
        self.mkdirs = []
        self.uploads = []
        self.moves = []
        card = self

        class _Client:
            ftps_session = None  # bpm only disconnects a client that has a live session

            def __init__(self, *args, **kwargs):
                card.connects += 1
                if card.connect_error:
                    raise card.connect_error

            def list_files_ex(self, path):
                if card.listing_fails:
                    return None  # bpm's contract for a listing that failed (an empty folder is [])
                if path != "/":
                    return []
                card.listings += 1
                stamp = datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc)
                return [
                    FtpListItem(p, p.lstrip("/"), len(b), False, stamp, "o", "g", "-rw-r--r--")
                    for p, b in card.files.items()
                ]

            def fexists(self, path):
                return path in card.files

            def download_file(self, src, dest):
                card.downloads.append(src)
                Path(dest).write_bytes(card.files[src])

            def mkdir(self, path):
                card.mkdirs.append(path)

            def upload_file(self, src, dest):
                card.uploads.append((src, dest))

            def move_file(self, src, dest):
                card.moves.append((src, dest))

            def disconnect(self):
                pass

        self.client_class = _Client


@contextlib.contextmanager
def _env(card, listing_returns_none=False):
    """Real BambuPrinter (no MQTT session started) wired to `card`, exposed to the tools
    through a stub session_manager. webbrowser.open is recorded, never executed."""
    cache = Path(tempfile.mkdtemp(prefix="bambu_mcp_files_defects_"))
    saved = (bpm_printer.IoTFTPSClient, files_mod.session_manager, webbrowser.open)
    opened = []
    try:
        bpm_printer.IoTFTPSClient = card.client_class
        printer = bpm_printer.BambuPrinter(
            BambuConfig("127.0.0.1", "12345678", "TESTSERIAL", bpm_cache_path=cache)
        )
        if listing_returns_none:
            # A real failed listing: the FTPS client reports it as None, bpm's own
            # _get_sftp_files and get_sdcard_contents carry it up, clear both cached trees and
            # return None. Nothing between the FTPS client and the tool is stubbed.
            card.listing_fails = True
        files_mod.session_manager = types.SimpleNamespace(get_printer=lambda name: printer)
        webbrowser.open = lambda url, *a, **k: opened.append(url) or True
        yield printer, opened
    finally:
        bpm_printer.IoTFTPSClient, files_mod.session_manager, webbrowser.open = saved
        shutil.rmtree(cache, ignore_errors=True)
        VIEWER_HTML.unlink(missing_ok=True)


def _viewer(plate_nums, target_plate=None, with_images=True):
    card = _Card({"/sparse.3mf": _build_3mf(plate_nums, with_images)})
    with _env(card) as (_printer, opened):
        result = files_mod.open_plate_viewer(PRINTER, "/sparse.3mf", target_plate)
        html = VIEWER_HTML.read_text() if VIEWER_HTML.exists() else ""
    return result, html, opened, card


def _plate_block(html, n) -> str:
    m = re.search(rf'<div class="plate" id="plate-{n}">(.*?)</div></div>\n', html, re.S)
    assert m, f"no block for plate-{n}; plate ids present: {re.findall(r'id=.plate-(\d+)', html)}"
    return m.group(1)


# ── defect 8: open_plate_viewer ─────────────────────────────────────────────


def test_viewer_shows_exactly_the_real_sparse_plates():
    result, html, _, card = _viewer([1, 5, 6, 12])
    assert "error" not in result, result
    headings = re.findall(r"<h3>Plate (\d+)</h3>", html)
    assert headings == ["1", "5", "6", "12"], headings
    assert result["plates"] == 4, result
    assert (card.downloads, card.listings) == (["/sparse.3mf"], 1), (card.downloads, card.listings)


def test_viewer_each_heading_carries_its_own_plate_images():
    _, html, _, _ = _viewer([1, 5, 6, 12])
    for n in (1, 5, 6, 12):
        block = _plate_block(html, n)
        assert f"part_{n}" in block, (n, block[:200])
        assert _png_uri((n * 10 % 256, 40, 200)) in block, f"plate {n} shows another plate's thumbnail"
        assert _png_uri((200, n * 10 % 256, 40)) in block, f"plate {n} shows another plate's top view"


def test_viewer_no_plate_is_rendered_under_two_headings():
    _, html, _, _ = _viewer([1, 5, 6, 12])
    for n in (1, 5, 6, 12):
        seen = html.count(f'<p class="parts">part_{n}</p>')
        assert seen == 1, f"plate {n}'s part label appears {seen} times (another plate's images under the wrong heading)"


def test_viewer_does_not_invent_absent_plate_numbers():
    _, html, _, _ = _viewer([1, 5, 6, 12])
    ids = re.findall(r'id="plate-(\d+)"', html)
    assert ids == ["1", "5", "6", "12"], ids


def test_viewer_plate_set_not_starting_at_one():
    result, html, _, _ = _viewer([10])
    assert "error" not in result, result
    headings = re.findall(r"<h3>Plate (\d+)</h3>", html)
    assert headings == ["10"], headings
    assert "part_10" in _plate_block(html, 10)


def test_viewer_target_plate_anchor_resolves_for_a_sparse_plate():
    _, html, opened, _ = _viewer([1, 5, 6, 12], target_plate=12)
    assert opened and opened[0].endswith("#plate-12"), opened
    assert 'id="plate-12"' in html, "the #plate-12 fragment has no anchor to scroll to"


def test_viewer_contiguous_plates_unchanged():
    result, html, _, card = _viewer([1, 2, 3])
    assert "error" not in result, result
    assert re.findall(r"<h3>Plate (\d+)</h3>", html) == ["1", "2", "3"]
    assert result["plates"] == 3, result
    assert card.downloads == ["/sparse.3mf"], f"the .3mf must be downloaded once: {card.downloads}"


def test_viewer_unparseable_file_returns_the_error_shape():
    result, html, opened, _ = _viewer([1, 2], with_images=False)
    assert result == {"error": "Could not retrieve project info for '/sparse.3mf'"}, result
    assert not opened, "a failed build must not open a browser"


# ── defect 9: refresh_sdcard and its sibling tools ──────────────────────────


def _populated_card():
    return _Card({"/a.3mf": b"x", "/b.gcode": b"y"})


def test_refresh_full_reports_a_failed_listing():
    with _env(_populated_card(), listing_returns_none=True) as (printer, _):
        r = files_mod.refresh_sdcard(PRINTER, "full")
        cached = printer.cached_sd_card_contents
    assert cached is None, "precondition: bpm cleared its cached tree, so the listing failed"
    assert r == {"error": LIST_FAILED}, r


def test_refresh_3mf_reports_a_failed_listing():
    with _env(_populated_card(), listing_returns_none=True) as (printer, _):
        r = files_mod.refresh_sdcard(PRINTER, "3mf")
        cached = printer.cached_sd_card_3mf_files
    assert cached is None, "precondition: bpm cleared its cached 3mf tree, so the listing failed"
    assert r == {"error": LIST_FAILED}, r


def test_refresh_mode_is_case_insensitive_on_failure_too():
    with _env(_populated_card(), listing_returns_none=True):
        r = files_mod.refresh_sdcard(PRINTER, "3MF")
    assert r == {"error": LIST_FAILED}, r


def test_refresh_failure_path_really_used_the_fake_ftps_client():
    card = _populated_card()
    with _env(card, listing_returns_none=True):
        files_mod.refresh_sdcard(PRINTER, "full")
    assert card.connects >= 1, "the stubbed FTPS client was never used: the test drove the wrong path"


def test_refresh_succeeds_on_a_populated_card():
    with _env(_populated_card()) as (printer, _):
        full = files_mod.refresh_sdcard(PRINTER, "full")
        tree = printer.cached_sd_card_contents
        m3 = files_mod.refresh_sdcard(PRINTER, "3mf")
    assert full == {"success": True, "mode": "full", "printer": PRINTER}, full
    assert m3 == {"success": True, "mode": "3mf", "printer": PRINTER}, m3
    assert [c["id"] for c in tree["children"]] == ["/a.3mf", "/b.gcode"], tree


def test_refresh_succeeds_on_a_genuinely_empty_card():
    # An empty card is a real tree with no children, not a failed listing.
    with _env(_Card()) as (printer, _):
        full = files_mod.refresh_sdcard(PRINTER, "full")
        m3 = files_mod.refresh_sdcard(PRINTER, "3mf")
        tree = printer.cached_sd_card_contents
    assert full == {"success": True, "mode": "full", "printer": PRINTER}, full
    assert m3 == {"success": True, "mode": "3mf", "printer": PRINTER}, m3
    assert tree == {"id": "/", "name": "/", "children": []}, tree


def test_refresh_reports_an_ftps_connection_failure():
    with _env(_Card(connect_error=OSError("timed out"))):
        r = files_mod.refresh_sdcard(PRINTER, "full")
    assert r == {"error": f"Error refreshing SD card on '{PRINTER}': timed out"}, r


def test_refresh_rejects_an_unknown_mode():
    with _env(_populated_card()):
        r = files_mod.refresh_sdcard(PRINTER, "bogus")
    assert r == {"error": "Unknown mode 'bogus'. Must be 'full' or '3mf'."}, r


def test_siblings_report_a_failed_listing_as_the_same_error():
    with _env(_populated_card(), listing_returns_none=True):
        results = {
            "list_live": files_mod.list_sdcard_files(PRINTER),
            "list_cached": files_mod.list_sdcard_files(PRINTER, cached=True),
            "get_file_info": files_mod.get_file_info(PRINTER, "/a.3mf"),
            "by_name": files_mod.get_3mf_entry_by_name(PRINTER, "a.3mf"),
            "by_id": files_mod.get_3mf_entry_by_id(PRINTER, "/a.3mf"),
        }
    bad = {k: v for k, v in results.items() if v != {"error": LIST_FAILED}}
    assert not bad, bad


def test_create_folder_keeps_success_with_null_contents_when_only_the_relisting_failed():
    # The mkdir itself succeeded; reporting an error would misstate what happened on the card.
    card = _populated_card()
    with _env(card, listing_returns_none=True):
        r = files_mod.create_folder(PRINTER, "/newdir", user_permission=True)
    assert card.mkdirs == ["/newdir"], card.mkdirs
    assert r == {"success": True, "path": "/newdir", "contents": None}, r


def test_upload_and_rename_keep_success_when_only_the_relisting_failed():
    card = _populated_card()
    local = Path(tempfile.mkdtemp(prefix="bambu_mcp_files_defects_up_")) / "note.txt"
    try:
        local.write_text("x")
        with _env(card, listing_returns_none=True):
            up = files_mod.upload_file(PRINTER, str(local), "/note.txt", user_permission=True)
            mv = files_mod.rename_sdcard_file(PRINTER, "/a.3mf", "/c.3mf", user_permission=True)
    finally:
        shutil.rmtree(local.parent, ignore_errors=True)
    assert card.uploads == [(str(local), "/note.txt")], card.uploads
    assert card.moves == [("/a.3mf", "/c.3mf")], card.moves
    assert up == {"success": True, "remote_path": "/note.txt", "contents": None}, up
    assert mv == {"success": True, "src_path": "/a.3mf", "dest_path": "/c.3mf"}, mv


if __name__ == "__main__":
    failed = 0
    for tname, fn in sorted(globals().items()):
        if tname.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {tname}")
            except Exception as e:
                failed += 1
                print(f"FAIL {tname}: {type(e).__name__}: {e}")
    print(f"{failed} failed")
    sys.exit(1 if failed else 0)
