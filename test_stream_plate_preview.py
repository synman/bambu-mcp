"""Regression tests for the stream page's plate preview showing an older job's plate.

The stream server pre-loads the last saved plate PNGs from ~/.bambu-mcp so the panel appears at
once. Those files carried no record of which job they belonged to, so the cache started with no
key and a new job's first lookup never cleared them. When that lookup failed (the A1's FTPS drops
connections during a print), the failure path stored the new job's key beside the old images, and
every later lookup returned the old plate as this job's. It never retried. Observed 2026-09-29: the
A1 page served a 2026-04-09 plate for "Shiny Vihelmo".

The disk images now carry their job key, a failed lookup leaves the panel empty and retries after
60 s, and the page clears an image the server no longer has.

The tests drive the real ``tools.camera.start_stream`` closures and the real served page script.
The printer, camera session, stream server, bpm project lookup and home folder are stubbed. Nothing
touches a printer, the network or the daemon.

Run directly (no pytest needed):  .venv/bin/python3 test_stream_plate_preview.py
"""

import contextlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpm.bambuproject as bambuproject  # noqa: E402
import job_project  # noqa: E402
import tools.camera as camera  # noqa: E402
import tools.files as files  # noqa: E402
from camera import mjpeg_server as mjpeg_module  # noqa: E402

NAME = "plate-preview-test"
OLD_THUMB, OLD_LAYOUT = b"old-thumb", b"old-layout"
NEW_THUMB, NEW_LAYOUT = b"new-thumb", b"new-layout"


def _info():
    import base64
    uri = lambda b: "data:image/png;base64," + base64.b64encode(b).decode()  # noqa: E731
    info = bambuproject.ProjectInfo()   # the real dataclass the lookup returns
    info.metadata = {"thumbnail": uri(NEW_THUMB), "topimg": uri(b"top"),
                     "map": {"bbox_objects": [{"id": 1}]}}
    return info


@contextlib.contextmanager
def _stream(disk=None, key=None, fetch=None, job=None, printer=None):
    """Start the stream with stubs; yield (thumbnail_fn, layout_fn, calls, clock, home)."""
    home = Path(tempfile.mkdtemp(prefix="plate-preview-"))
    plate_dir = home / ".bambu-mcp"
    plate_dir.mkdir()
    if disk:
        (plate_dir / f"plate_thumb_{NAME}.png").write_bytes(OLD_THUMB)
        (plate_dir / f"plate_layout_{NAME}.png").write_bytes(OLD_LAYOUT)
    if key is not None:
        (plate_dir / f"plate_key_{NAME}.json").write_text(json.dumps(key))

    captured, calls, clock = {}, [], [1000.0]
    printer = printer or SimpleNamespace(config=SimpleNamespace(printer_model="A1"))
    job = job or SimpleNamespace(project_info={"id": "/new.gcode.3mf"}, gcode_file="", subtask_name="new")

    def get_project_info(path, p, plate_num=1):
        calls.append((path, plate_num))
        return (fetch or (lambda: _info()))()

    def start(name, frame_factory, port, **kw):
        captured.update(kw)
        return "http://localhost:49999/"

    patches = [
        (camera.pathlib.Path, "home", classmethod(lambda cls: home)),
        (camera, "_get_printer_checked", lambda n: (printer, None)),
        (camera, "get_protocol", lambda p: "tcp_tls"),
        (camera, "_make_stream_session", lambda p: (SimpleNamespace(), lambda: None)),
        (camera.mjpeg_server, "is_running", lambda n: False),
        (camera.mjpeg_server, "start", start),
        (camera.session_manager, "get_job", lambda n: job),
        (camera.session_manager, "get_printer", lambda n: printer),
        (bambuproject, "get_project_info", get_project_info),
        (files, "_build_layout_uri", lambda t, o, m: "data:image/png;base64,"
         + __import__("base64").b64encode(NEW_LAYOUT).decode()),
        (job_project, "_DIR", plate_dir),
    ]
    if hasattr(camera, "time"):   # absent before the retry clock existed, so the old code still runs
        patches.append((camera.time, "monotonic", lambda: clock[0]))
    saved = [(o, a, o.__dict__[a] if isinstance(o, type) else getattr(o, a)) for o, a, _ in patches]
    try:
        for o, a, v in patches:
            setattr(o, a, v)
        result = camera.start_stream(NAME)
        assert "error" not in result, result
        yield captured["thumbnail_fn"], captured["layout_fn"], calls, clock, plate_dir
    finally:
        for o, a, v in saved:
            setattr(o, a, v)
        shutil.rmtree(home, ignore_errors=True)


def _fail():
    raise ConnectionResetError(54, "Connection reset by peer")


def test_a_failed_lookup_never_shows_an_older_jobs_plate():
    # The observed case: disk images saved before keys were, and the new job's fetch fails
    with _stream(disk=True, fetch=_fail) as (thumb, layout, calls, clock, plate_dir):
        assert thumb() is None, "the old plate is shown as this job's"
        assert layout() is None
        assert thumb() is None, "the old plate came back on the next poll"
        assert not (plate_dir / f"plate_thumb_{NAME}.png").exists()


def test_a_failed_lookup_is_retried_after_a_minute():
    outcome = {"fail": True}

    def fetch():
        if outcome["fail"]:
            _fail()
        return _info()

    with _stream(fetch=fetch) as (thumb, layout, calls, clock, plate_dir):
        assert thumb() is None
        assert thumb() is None and len(calls) == 1, "retried at once, which hammers a printing A1"
        outcome["fail"] = False
        clock[0] += 61
        assert thumb() == NEW_THUMB, "never retried once the printer answered again"
        assert layout() == NEW_LAYOUT
        assert json.loads((plate_dir / f"plate_key_{NAME}.json").read_text()) == ["/new.gcode.3mf", 1]


def test_a_saved_plate_for_the_same_job_is_served_without_a_lookup():
    # A daemon restart mid-job keeps showing the job's plate at once, without touching FTPS
    with _stream(disk=True, key=["/new.gcode.3mf", 1], fetch=_fail) as (thumb, layout, calls, _, _d):
        assert thumb() == OLD_THUMB
        assert layout() == OLD_LAYOUT
        assert calls == []


def test_a_saved_plate_for_another_job_is_replaced():
    with _stream(disk=True, key=["/older.gcode.3mf", 2]) as (thumb, layout, calls, _, _d):
        assert thumb() == NEW_THUMB
        assert layout() == NEW_LAYOUT
        assert calls == [("/new.gcode.3mf", 1)]


def _unresolved(listing_calls, cached=None, live=True):
    """A job bpm lost across a daemon restart (no project, no recallable record), and a printer
    whose SD card holds /jobs/new.gcode.3mf. ``live=False`` makes the live listing fail."""
    tree = {"id": "/", "name": "/", "children": [
        {"id": "/jobs", "name": "jobs", "children": [{"id": "/jobs/new.gcode.3mf", "name": "new.gcode.3mf"}]}]}

    def listing():
        listing_calls.append(1)
        return tree if live else None

    job = SimpleNamespace(project_info={}, gcode_file="", subtask_name="new", wall_start_time=-1.0)
    printer = SimpleNamespace(config=SimpleNamespace(printer_model="A1"),
                              cached_sd_card_3mf_files=cached, get_sdcard_3mf_files=listing)
    return job, printer, tree


def test_the_page_never_lists_the_sd_card_itself():
    # The page used to run its own "<job name>.gcode.3mf" search, a copy of bpm's weaker than
    # bpm's (an A1 screen start's name already ends in .gcode.3mf). bpm now finds the project,
    # from its cache when the listing fails, so a job bpm has no project for shows no new plate.
    listing = []
    job, printer, _ = _unresolved(listing)
    with _stream(job=job, printer=printer) as (thumb, layout, calls, clock, _d):
        for _ in range(3):
            assert thumb() is None and layout() is None
        clock[0] += 120
        thumb()
        assert listing == [], f"{len(listing)} SD card listings from the stream page"
        assert calls == []


def test_the_page_shows_the_plate_bpm_chose():
    # A screen start names no plate; bpm picks the file's lowest one and the page must follow it
    # rather than re-derive plate 1 from a gcode_file that carries no plate.
    job = SimpleNamespace(project_info={"id": "/multi.gcode.3mf", "plate_num": 2},
                          gcode_file="multi.gcode.3mf", subtask_name="multi.gcode.3mf")
    with _stream(job=job) as (thumb, layout, calls, clock, _d):
        assert thumb() == NEW_THUMB
        assert calls == [("/multi.gcode.3mf", 2)]


# The page side: run the real served script under node, with a stub DOM and a fetch that answers
# 404 for the thumbnail and a PNG for the layout.
_HARNESS = r"""
const vm = require('vm'), fs = require('fs');
const els = {};
function el(id) {
  if (els[id]) return els[id];
  const e = { id, attrs: {}, style: {}, classes: new Set(),
    set src(v) { this.attrs.src = v; }, get src() { return this.attrs.src; },
    removeAttribute(a) { delete this.attrs[a]; },
    getContext: () => new Proxy(function () {}, { get: () => () => {} }),
    querySelector: () => null, addEventListener() {}, getBoundingClientRect: () => ({}) };
  e.classList = { add: c => e.classes.add(c), remove: c => e.classes.delete(c),
                  contains: c => e.classes.has(c), toggle() {} };
  return (els[id] = e);
}
const answers = JSON.parse(process.argv[3]);
function fetch(url) {
  const path = url.split('?')[0];
  if (!(path in answers)) return new Promise(() => {});
  const a = answers[path];
  if (a === 'network') return Promise.reject(new Error('down'));
  return Promise.resolve({ ok: a === 200, status: a,
                           headers: { get: () => (a === 200 ? 'image/png' : 'text/plain') } });
}
const noop = () => {};
const ctx = vm.createContext({
  document: { getElementById: el, querySelector: () => null, querySelectorAll: () => [] },
  window: { addEventListener: noop, innerHeight: 600 }, fetch, setInterval: noop, setTimeout: noop,
  localStorage: { getItem: () => null, setItem: noop, removeItem: noop }, Date, Math, JSON,
  Promise, console, Uint8Array, TextDecoder, AbortController, requestAnimationFrame: noop,
  createImageBitmap: () => new Promise(() => {}), Blob: function () {},
});
el('thumb-img').src = 'old-thumb'; el('layout-img').src = 'old-layout';
el('preview-wrap').classes.add('hidden');
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
setTimeout(() => {
  const s = id => ({ src: els[id].attrs.src || null, display: els[id].style.display || '' });
  console.log(JSON.stringify({ thumb: s('thumb-img'), layout: s('layout-img'),
                               hidden: els['preview-wrap'].classes.has('hidden') }));
}, 50);
"""


def _run_page(answers):
    node = shutil.which("node")
    if node is None:
        return None
    script = re.findall(r"<script[^>]*>(.*?)</script>", mjpeg_module._HTML_PAGE, re.S)
    assert len(script) == 1
    tmp = Path(tempfile.mkdtemp(prefix="plate-page-"))
    try:
        (tmp / "page.js").write_text(script[0])
        (tmp / "harness.js").write_text(_HARNESS)
        proc = subprocess.run([node, str(tmp / "harness.js"), str(tmp / "page.js"), json.dumps(answers)],
                              capture_output=True, text=True, timeout=60)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert proc.returncode == 0, proc.stderr[-800:]
    return json.loads(proc.stdout)


def test_page_clears_an_image_the_server_no_longer_has():
    out = _run_page({"/thumbnail": 404, "/layout": 200})
    if out is None:
        print("SKIP test_page_clears_an_image_the_server_no_longer_has: node is not installed")
        return
    assert out["thumb"] == {"src": None, "display": "none"}, out
    assert out["layout"]["src"].startswith("/layout?t="), out
    assert out["layout"]["display"] == "", out
    assert out["hidden"] is False, out


def test_page_hides_the_panel_when_neither_image_loads():
    out = _run_page({"/thumbnail": 404, "/layout": "network"})
    if out is None:
        print("SKIP test_page_hides_the_panel_when_neither_image_loads: node is not installed")
        return
    assert out["thumb"]["src"] is None and out["layout"]["src"] is None, out
    assert out["hidden"] is True, out


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
