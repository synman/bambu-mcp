"""The SD card routes must report a failed listing, not "success" or "null".

bpm now returns None from get_sdcard_contents() / get_sdcard_3mf_files() when the FTPS listing
FAILED, and an empty tree when the card is simply empty. The four routes used to discard that:
the refresh routes answered success and the get routes answered a JSON null with status 200.
A live listing that failed is now a 502 with the usual error body. The cached=true read is
unchanged: an empty cache is just "nothing cached yet", not a failure.

Drives the real Flask app from api_server._build_app(); only session_manager.get_printer is
stubbed, so nothing touches a printer, the network or the daemon.

Run directly (no pytest needed):  .venv/bin/python3 test_sdcard_routes.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import api_server  # noqa: E402
import session_manager as _sm_mod  # noqa: E402

EMPTY_TREE = {"id": "/", "name": "/", "children": []}
_app = api_server._build_app()
assert _app is not None


class _Printer:
    def __init__(self, contents, three_mf):
        self._contents = contents
        self._three_mf = three_mf
        self.cached_sd_card_contents = None
        self.cached_sd_card_3mf_files = None

    def get_sdcard_contents(self):
        return self._contents

    def get_sdcard_3mf_files(self):
        return self._three_mf


class _Stub:
    def __init__(self, printer):
        self.printer = printer

    def __enter__(self):
        self._orig = _sm_mod.session_manager.get_printer
        _sm_mod.session_manager.get_printer = lambda name: self.printer
        return self

    def __exit__(self, *exc):
        _sm_mod.session_manager.get_printer = self._orig


def _call(printer, method, url):
    with _Stub(printer):
        return getattr(_app.test_client(), method)(url)


def _failed(resp):
    body = resp.get_json()
    assert resp.status_code == 502, (resp.status_code, resp.data[:200])
    assert body["status"] == "error" and "listing failed" in body["reason"], body


def test_refresh_contents_reports_a_failed_listing():
    _failed(_call(_Printer(None, None), "post", "/api/refresh_sdcard_contents?printer=P"))


def test_refresh_3mf_reports_a_failed_listing():
    _failed(_call(_Printer(None, None), "post", "/api/refresh_sdcard_3mf_files?printer=P"))


def test_get_contents_reports_a_failed_live_listing():
    _failed(_call(_Printer(None, None), "get", "/api/get_sdcard_contents?printer=P"))


def test_get_3mf_reports_a_failed_live_listing():
    _failed(_call(_Printer(None, None), "get", "/api/get_sdcard_3mf_files?printer=P"))


def test_the_find_routes_report_a_failed_listing_the_same_way():
    _failed(_call(_Printer(None, None), "get", "/api/find_3mf_by_name?printer=P&name=a.3mf"))
    _failed(_call(_Printer(None, None), "get", "/api/find_3mf_by_id?printer=P&id=/a.3mf"))


def test_an_empty_card_is_success_with_an_empty_tree():
    p = _Printer(EMPTY_TREE, EMPTY_TREE)
    for method, url in (
        ("post", "/api/refresh_sdcard_contents?printer=P"),
        ("post", "/api/refresh_sdcard_3mf_files?printer=P"),
    ):
        r = _call(p, method, url)
        assert r.status_code == 200 and r.get_json()["status"] == "success", (url, r.data[:200])
    for url in ("/api/get_sdcard_contents?printer=P", "/api/get_sdcard_3mf_files?printer=P"):
        r = _call(p, "get", url)
        assert r.status_code == 200 and r.get_json() == EMPTY_TREE, (url, r.data[:200])


def test_a_cached_read_of_an_empty_cache_is_still_null_not_an_error():
    p = _Printer(None, None)
    for url in ("/api/get_sdcard_contents?printer=P&cached=true", "/api/get_sdcard_3mf_files?printer=P&cached=true"):
        r = _call(p, "get", url)
        assert r.status_code == 200 and r.get_json() is None, (url, r.status_code, r.data[:200])


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {name}")
            except Exception as exc:  # noqa: BLE001
                failed += 1
                print(f"FAIL {name}: {exc!r}")
    sys.exit(1 if failed else 0)
