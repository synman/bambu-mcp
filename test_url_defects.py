"""Regression test for the URL builders in tools/url_factory.py (defect 10) and the snapshot / monitoring routes.

The four builders (get_snapshot, get_monitoring_data, get_monitoring_history, get_monitoring_series)
used to interpolate name, field and resolution into the URL with no escaping, and field / resolution
were not validated. A printer name containing a space, an ampersand, a plus or a hash gave a broken
or altered URL (an extra query parameter, a truncated name), and a field such as
"bed&printer=other" injected a parameter.

What this pins, using the real url_factory, the real Flask app from api_server._build_app() to decode
the URL exactly as the daemon would, the real data_collector and the real monitoring routes:
  1. ordinary names give the byte-for-byte pre-fix URL;
  2. every awkward name round-trips through the real request parser to the same printer value, and
     no query key other than the expected ones appears;
  3. resolution and field are validated against the sets the daemon really honours (camera's
     _RESOLUTION_MAP, data_collector's COLLECTION_NAMES, and api_server._HEALTH_FIELDS), and an invalid
     value returns an {"error": ...} dict and no URL;
  4. end to end, a URL built for an awkward name is answered 200 by the real monitoring routes;
  5. snapshot quality is range-checked (integer 1-100) in BOTH the tool and the /api/snapshot route, and
     the route rejects a bad value before the camera is asked for a frame (the route's OpenAPI type for
     quality stays "integer");
  6. the health-field set is ONE object: url_factory imports api_server._HEALTH_FIELDS, and the
     monitoring_series route reads that module constant instead of defining its own.

Only session_manager.get_printer (and, for the route tests, tools.camera.get_snapshot) is stubbed
(no printer, no network, no daemon).

Run directly (no pytest needed):  .venv/bin/python3 test_url_defects.py
"""

import sys
import traceback
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import api_server  # noqa: E402
import session_manager as _sm_mod  # noqa: E402
from data_collector import PrinterDataCollector, data_collector  # noqa: E402
from tools import camera, url_factory  # noqa: E402

CONNECTED = {
    "H2D-Test_1.a~b",
    "My Printer",
    "A&B",
    "A=B",
    "A+B",
    "A%20B",
    "A#B",
    "A?B",
    "a/b",
    "café ☃",
    "x&field=bed",
    "x&resolution=180p&quality=1",
    "Lab A&B+C #1",
}

PLAIN = "H2D-Test_1.a~b"
AWKWARD_NAMES = sorted(n for n in CONNECTED if n != PLAIN)

TELEMETRY_FIELDS = list(PrinterDataCollector.COLLECTION_NAMES)


def _health_fields() -> list:
    """The health fields the monitoring_series route serves: the api_server module constant."""
    return sorted(api_server._HEALTH_FIELDS)


_app = api_server._build_app()
assert _app is not None


class _Stub:
    """Swap session_manager.get_printer for a name lookup; restore on exit."""

    def __enter__(self):
        self._orig = _sm_mod.session_manager.get_printer
        _sm_mod.session_manager.get_printer = lambda name: object() if name in CONNECTED else None
        return self

    def __exit__(self, *exc):
        _sm_mod.session_manager.get_printer = self._orig


def _args(url: str) -> dict:
    """Decode a built URL with the real Flask request parser; return {key: [values]}."""
    parts = urlsplit(url)
    with _app.test_request_context(parts.path + "?" + parts.query):
        from flask import request
        return {k: request.args.getlist(k) for k in request.args.keys()}


def _all_builders(name: str) -> list[tuple[str, dict, dict]]:
    """(label, result, expected decoded args) for each builder called with valid arguments."""
    return [
        ("get_snapshot", url_factory.get_snapshot(name, "720p", 75, True),
         {"printer": [name], "resolution": ["720p"], "quality": ["75"], "include_status": ["true"]}),
        ("get_monitoring_data", url_factory.get_monitoring_data(name), {"printer": [name]}),
        ("get_monitoring_history", url_factory.get_monitoring_history(name, True),
         {"printer": [name], "raw": ["true"]}),
        ("get_monitoring_series", url_factory.get_monitoring_series(name, "bed"),
         {"printer": [name], "field": ["bed"]}),
    ]


def test_ordinary_names_give_byte_identical_urls():
    base = url_factory._api_base()
    name = "H2D-Test_1.a~b"
    with _Stub():
        assert url_factory.get_snapshot(name) == {
            "url": f"{base}/snapshot?printer={name}&resolution=native&quality=85&include_status=false"}
        assert url_factory.get_snapshot(name, "1080p", 60, True) == {
            "url": f"{base}/snapshot?printer={name}&resolution=1080p&quality=60&include_status=true"}
        assert url_factory.get_monitoring_data(name) == {"url": f"{base}/monitoring_data?printer={name}"}
        assert url_factory.get_monitoring_history(name) == {
            "url": f"{base}/monitoring_history?printer={name}&raw=false"}
        assert url_factory.get_monitoring_history(name, raw=True) == {
            "url": f"{base}/monitoring_history?printer={name}&raw=true"}
        assert url_factory.get_monitoring_series(name, "bed") == {
            "url": f"{base}/monitoring_series?printer={name}&field=bed"}


def test_awkward_names_round_trip_through_the_real_request_parser():
    with _Stub():
        for name in AWKWARD_NAMES:
            for label, result, expected in _all_builders(name):
                assert "url" in result, (label, name, result)
                url = result["url"]
                assert " " not in url and "#" not in url, (label, name, url)
                got = _args(url)
                assert got == expected, (label, name, url, got)


def test_snapshot_resolution_is_validated_against_the_camera_set():
    valid = list(camera._RESOLUTION_MAP)
    assert len(valid) == 6, valid
    with _Stub():
        for res in valid:
            r = url_factory.get_snapshot(PLAIN, res)
            assert "url" in r, (res, r)
            assert _args(r["url"])["resolution"] == [res], (res, r)
        for bad in ["", "4k", "720P", " 720p", "720p ", "720p&quality=1", "native&include_status=true", "1080"]:
            r = url_factory.get_snapshot(PLAIN, bad)
            assert "url" not in r, (bad, r)
            assert r.get("error") == "invalid_resolution", (bad, r)
            assert isinstance(r.get("detail"), str) and "native" in r["detail"], (bad, r)


def test_series_field_is_validated_against_what_the_daemon_serves():
    assert len(TELEMETRY_FIELDS) == 12, TELEMETRY_FIELDS
    HEALTH_FIELDS = _health_fields()
    assert len(HEALTH_FIELDS) == 6, HEALTH_FIELDS
    with _Stub():
        for field in TELEMETRY_FIELDS + HEALTH_FIELDS:
            r = url_factory.get_monitoring_series(PLAIN, field)
            assert "url" in r, (field, r)
            assert _args(r["url"]) == {"printer": [PLAIN], "field": [field]}, (field, r)
        for bad in ["", "Bed", "bed ", "tool_2", "nope", "bed&printer=other", "bed&field=tool", "bed#x", "../bed"]:
            r = url_factory.get_monitoring_series(PLAIN, bad)
            assert "url" not in r, (bad, r)
            assert r.get("error") == "invalid_field", (bad, r)
            assert isinstance(r.get("detail"), str) and "bed" in r["detail"], (bad, r)


def test_not_connected_is_unchanged():
    with _Stub():
        for r in (
            url_factory.get_snapshot("nobody"),
            url_factory.get_monitoring_data("nobody"),
            url_factory.get_monitoring_history("nobody"),
            url_factory.get_monitoring_series("nobody", "bed"),
        ):
            assert r == {"error": "not_connected"}, r


def test_end_to_end_awkward_name_is_served_by_the_real_monitoring_routes():
    name = "Lab A&B+C #1"
    assert name in CONNECTED
    data_collector.register_printer(name)
    client = _app.test_client()
    try:
        with _Stub():
            def fetch(result):
                assert "url" in result, result
                parts = urlsplit(result["url"])
                return client.get(parts.path + "?" + parts.query)

            resp = fetch(url_factory.get_monitoring_data(name))
            assert resp.status_code == 200 and "collections" in resp.get_json(), (resp.status_code, resp.data[:200])
            resp = fetch(url_factory.get_monitoring_history(name))
            assert resp.status_code == 200 and "summary" in resp.get_json(), (resp.status_code, resp.data[:200])
            for field in TELEMETRY_FIELDS + _health_fields():
                resp = fetch(url_factory.get_monitoring_series(name, field))
                assert resp.status_code == 200, (field, resp.status_code, resp.data[:200])
                assert resp.get_json()["field"] == field, (field, resp.get_json())
            # Everything the tool refuses is something the route also refuses (agreement, not a guess).
            for bad in ["nope", "Bed", "tool_2"]:
                parts = urlsplit(f"{url_factory._api_base()}/monitoring_series?printer=Lab%20A%26B%2BC%20%231&field={bad}")
                resp = client.get(parts.path + "?" + parts.query)
                assert resp.status_code == 400, (bad, resp.status_code, resp.data[:200])
    finally:
        with data_collector._lock:
            data_collector._collectors.pop(name, None)


def test_url_factory_adds_no_public_callable():
    # Registration rule: every public callable with a docstring in tools/url_factory.py is an MCP tool.
    public = sorted(
        n for n in dir(url_factory)
        if not n.startswith("_") and callable(getattr(url_factory, n))
        and getattr(getattr(url_factory, n), "__module__", None) == url_factory.__name__
        and getattr(getattr(url_factory, n), "__doc__", None)
    )
    assert public == ["get_monitoring_data", "get_monitoring_history", "get_monitoring_series", "get_snapshot"], public


BAD_QUALITIES = [0, 101, -5, 1000, 85.5, "85", "abc", None, True, False]


def test_snapshot_quality_is_range_checked_by_the_tool():
    with _Stub():
        for q in (1, 55, 85, 100):
            r = url_factory.get_snapshot(PLAIN, "720p", q)
            assert _args(r["url"])["quality"] == [str(q)], (q, r)
        for bad in BAD_QUALITIES:
            r = url_factory.get_snapshot(PLAIN, "720p", bad)
            assert "url" not in r, (bad, r)
            assert r.get("error") == "invalid_quality", (bad, r)
            assert isinstance(r.get("detail"), str) and "1" in r["detail"] and "100" in r["detail"], (bad, r)
        # a bad resolution is still reported as invalid_resolution, and not_connected still comes first
        assert url_factory.get_snapshot(PLAIN, "4k", 0).get("error") == "invalid_resolution"
        assert url_factory.get_snapshot("nobody", "4k", 0) == {"error": "not_connected"}


class _CameraStub:
    """Replace tools.camera.get_snapshot with a recorder so no frame is captured; restore on exit."""

    def __enter__(self):
        self.calls = []
        self._orig = camera.get_snapshot
        camera.get_snapshot = lambda name, **kw: (self.calls.append((name, kw)), {"width": 1})[1]
        return self

    def __exit__(self, *exc):
        camera.get_snapshot = self._orig


def _snapshot(client, printer, **params):
    from urllib.parse import urlencode
    return client.get("/api/snapshot?" + urlencode({"printer": printer, **params}))


def test_snapshot_route_rejects_a_bad_quality_before_the_camera():
    client = _app.test_client()
    with _Stub(), _CameraStub() as cam:
        for bad in ["0", "101", "-5", "1000", "99999999999999999999", "abc", "", "85.5", "1e2"]:
            before = len(cam.calls)
            resp = _snapshot(client, PLAIN, quality=bad)
            assert resp.status_code == 400, (bad, resp.status_code, resp.data[:200])
            body = resp.get_json()
            assert body.get("error") == "invalid_quality", (bad, body)
            assert isinstance(body.get("detail"), str) and "1" in body["detail"] and "100" in body["detail"], (bad, body)
            assert len(cam.calls) == before, f"quality={bad!r} reached the camera: {cam.calls[before:]}"


# Strings int() accepts (underscore separators, non-ASCII digits, surrounding whitespace, a sign) but that are
# not a plain run of ASCII digits: the stream server's regex rejects each, so the route must too.
INT_ACCEPTED_NON_DIGIT_QUALITIES = ["5_0", "٨٥", "８５", " 85", "85 ", "\t85", "+85", "85\n", "8_5", "0_1"]


def test_snapshot_route_rejects_what_int_alone_accepts():
    for s in INT_ACCEPTED_NON_DIGIT_QUALITIES:
        assert isinstance(int(s), int), s   # anchor: the lenient parse really does take every one of them
    client = _app.test_client()
    with _Stub(), _CameraStub() as cam:
        for bad in INT_ACCEPTED_NON_DIGIT_QUALITIES + ["0", "101", "abc", ""]:
            resp = _snapshot(client, PLAIN, quality=bad)
            assert resp.status_code == 400, (bad, resp.status_code, resp.data[:200])
            body = resp.get_json()
            assert body.get("error") == "invalid_quality", (bad, body)
            assert isinstance(body.get("detail"), str) and "1" in body["detail"] and "100" in body["detail"], (bad, body)
            assert not cam.calls, f"quality={bad!r} reached the camera: {cam.calls}"


def test_snapshot_route_and_stream_server_agree_on_quality_strings():
    # The stream server (camera/mjpeg_server.py) is the reference: for every candidate, the route answers
    # 400 exactly when _parse_stream_params raises. "" is left out: parse_qs drops a blank value, so the
    # stream server reads it as "not given" (quality 85) while the route has always answered it 400.
    import importlib
    from urllib.parse import urlencode
    mjpeg = importlib.import_module("camera.mjpeg_server")
    candidates = (INT_ACCEPTED_NON_DIGIT_QUALITIES
                  + ["0", "1", "85", "100", "101", "000085", "0000085", "-5", "1000", "85.5", "1e2", "abc",
                     "99999999999999999999"])
    client = _app.test_client()
    with _Stub(), _CameraStub():
        for s in candidates:
            try:
                mjpeg._parse_stream_params("/?" + urlencode({"quality": s}))
                stream_rejects = False
            except mjpeg._BadStreamParam:
                stream_rejects = True
            route_rejects = _snapshot(client, PLAIN, quality=s).status_code == 400
            assert route_rejects == stream_rejects, (s, "route rejects:", route_rejects, "stream rejects:", stream_rejects)


def test_snapshot_route_passes_a_valid_quality_through_unchanged():
    client = _app.test_client()
    with _Stub(), _CameraStub() as cam:
        for q in ["1", "55", "85", "100"]:
            resp = _snapshot(client, PLAIN, quality=q, resolution="720p")
            assert resp.status_code == 200, (q, resp.status_code, resp.data[:200])
            assert cam.calls[-1] == (PLAIN, {"resolution": "720p", "quality": int(q), "include_status": False}), cam.calls[-1]
        resp = _snapshot(client, PLAIN)  # default quality
        assert resp.status_code == 200 and cam.calls[-1][1]["quality"] == 85, cam.calls[-1]


def test_snapshot_openapi_still_types_quality_as_integer():
    spec = _app.test_client().get("/api/openapi.json").get_json()
    params = {p["name"]: p for p in spec["paths"]["/api/snapshot"]["get"]["parameters"]}
    assert params["quality"]["schema"] == {"type": "integer"}, params["quality"]
    assert params["include_status"]["schema"] == {"type": "boolean"}, params["include_status"]


def test_health_fields_is_one_module_constant_shared_by_route_and_tool():
    hf = getattr(api_server, "_HEALTH_FIELDS", None)
    assert hf is not None, "api_server has no module-level _HEALTH_FIELDS"
    assert len(hf) == 6 and len(set(hf)) == 6, hf
    assert url_factory._HEALTH_FIELDS is hf, "url_factory carries its own copy of the health-field set"
    route = _app.view_functions["monitoring_series_route"]
    assert "_HEALTH_FIELDS" in route.__code__.co_names, "monitoring_series route does not read the module constant"
    assert "_HEALTH_FIELDS" not in route.__code__.co_varnames, "monitoring_series route defines its own _HEALTH_FIELDS"
    assert url_factory._valid_fields() == tuple(PrinterDataCollector.COLLECTION_NAMES) + tuple(hf)


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {type(e).__name__}: {e}")
                traceback.print_exc(limit=3)
    sys.exit(1 if failed else 0)
