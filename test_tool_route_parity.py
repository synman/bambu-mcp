"""The MCP tools and REST routes that were added to close the tool/route gaps keep working.

Six REST routes wrap existing MCP tools (every-plate 3MF props, file info, plate thumbnail and
top view, AMS mapping preview, session status). Three MCP tools wrap what was REST-only
(filament catalog, sticky preference read and write). The sticky-preference docstrings used to
tell agents to run a Python import, which no MCP client can do; they now name the tools.

Drives the real Flask app from api_server._build_app() and the tool functions directly; only
session_manager.get_printer and bpm's project reader are stubbed, and user_prefs is pointed at
a temporary file, so nothing touches a printer, the network, the daemon or the real prefs.

Run directly (no pytest needed):  .venv/bin/python3 test_tool_route_parity.py
"""

import base64
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import api_server  # noqa: E402
import session_manager as _sm_mod  # noqa: E402
import user_prefs  # noqa: E402

_app = api_server._build_app()
assert _app is not None
_SPEC = api_server.build_openapi_document(_app)

NEW_ROUTES = {
    "/api/get_all_3mf_props_for_file": {"printer", "file", "include_images"},
    "/api/get_file_info": {"printer", "file"},
    "/api/plate_thumbnail": {"printer", "file", "plate", "quality"},
    "/api/plate_topview": {"printer", "file", "plate", "quality"},
    "/api/preview_ams_mapping": {"printer", "file", "plate"},
    "/api/session_status": {"printer"},
}


class _Printer:
    def get_sdcard_contents(self):
        return {"id": "/", "name": "/", "children": [
            {"id": "/a.3mf", "name": "a.3mf", "size": 10, "timestamp": 1.0}]}


class _Stub:
    def __enter__(self):
        self._orig = _sm_mod.session_manager.get_printer
        _sm_mod.session_manager.get_printer = lambda name: _Printer()
        return self

    def __exit__(self, *exc):
        _sm_mod.session_manager.get_printer = self._orig


def _get(url):
    with _Stub():
        return _app.test_client().get(url)


def _png() -> str:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def test_new_routes_publish_their_parameters():
    for path, want in NEW_ROUTES.items():
        op = _SPEC["paths"][path]["get"]
        got = {p["name"] for p in op.get("parameters", [])}
        assert got == want, (path, got)
        assert op["tags"] != ["System"] or path == "/api/session_status", (path, op["tags"])


def test_user_prefs_routes_publish_their_parameters():
    get_op = _SPEC["paths"]["/api/user_prefs"]["get"]
    assert {p["name"] for p in get_op["parameters"]} == {"printer", "key"}, get_op
    post = _SPEC["paths"]["/api/user_prefs"]["post"]["requestBody"]["content"]["application/json"]
    assert set(post["schema"]["properties"]) == {"printer", "key", "value"}, post


def test_file_info_route_returns_the_entry_and_404s_a_missing_file():
    resp = _get("/api/get_file_info?printer=P&file=/a.3mf")
    assert resp.status_code == 200 and resp.get_json()["file"]["name"] == "a.3mf", resp.data
    resp = _get("/api/get_file_info?printer=P&file=/nope.3mf")
    assert resp.status_code == 404 and resp.get_json()["status"] == "error", resp.data


def test_plate_image_routes_serve_jpeg_bytes():
    import bpm.bambuproject as bp

    from dataclasses import dataclass, field

    @dataclass
    class _Info:
        metadata: dict = field(default_factory=dict)

    images = {"thumbnail": _png(), "topimg": _png()}
    orig = bp.get_project_info
    bp.get_project_info = lambda *a, **k: _Info(dict(images))
    try:
        for route in ("plate_thumbnail", "plate_topview"):
            resp = _get(f"/api/{route}?printer=P&file=/a.3mf&plate=1&quality=preview")
            assert resp.status_code == 200, (route, resp.status_code, resp.data[:200])
            assert resp.mimetype == "image/jpeg" and resp.data[:2] == b"\xff\xd8", route
        images.clear()
        resp = _get("/api/plate_thumbnail?printer=P&file=/a.3mf")
        assert resp.status_code == 404, (resp.status_code, resp.data[:200])
    finally:
        bp.get_project_info = orig


def test_filament_catalog_tool_filters():
    from tools.filament import get_filament_catalog
    import gzip
    import json

    def _open(r):
        return json.loads(gzip.decompress(base64.b64decode(r["data"]))) if r.get("compressed") else r

    one = _open(get_filament_catalog(search="gfa00"))
    assert one["count"] == 1 and one["filaments"][0]["tray_info_idx"] == "GFA00", one
    pla = _open(get_filament_catalog(filament_type="pla"))
    assert pla["count"] > 1 and all(f["filament_type"] == "PLA" for f in pla["filaments"]), pla
    assert _open(get_filament_catalog(search="no-such-filament"))["count"] == 0


def test_user_pref_tools_round_trip_and_share_the_route_store():
    from tools.system import get_user_pref, set_user_pref
    orig = user_prefs._PREFS_PATH
    with tempfile.TemporaryDirectory() as d:
        user_prefs._PREFS_PATH = Path(d) / "user_prefs.json"
        try:
            assert get_user_pref("P", "speed_level") == {"key": "P:speed_level", "value": None}
            assert set_user_pref("P", "speed_level", "sport") == {"key": "P:speed_level", "value": "sport"}
            assert get_user_pref("P", "speed_level")["value"] == "sport"
            resp = _app.test_client().get("/api/user_prefs?printer=P&key=speed_level")
            assert resp.get_json()["value"] == "sport", resp.data
        finally:
            user_prefs._PREFS_PATH = orig


def test_no_tool_docstring_tells_an_agent_to_import_python():
    from tools import _registry
    bad = [fn.__name__ for fn in _registry.registered_tools()
           if "from user_prefs import" in (fn.__doc__ or "")]
    assert not bad, bad


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as exc:
                failed += 1
                print(f"FAIL {name}: {exc!r}")
    print(f"{failed} failed")
    sys.exit(1 if failed else 0)
