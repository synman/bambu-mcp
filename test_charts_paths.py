"""Regression test for the output file path of tools/charts.py open_charts.

open_charts used to build Path(f"/tmp/bambu-charts-{name}.html") from the RAW printer name, so a name
holding "/" or ".." wrote to (or failed in) another directory, and a pre-planted symlink at the
predictable path redirected the write anywhere the process could write.

What this pins, driving the real open_charts and the real render_charts_html (a collector is registered
in the real data_collector; no printer, no network, no daemon). Three side effects are stubbed because
they would touch the operator's desktop or depend on a live daemon: webbrowser.open (records the URL),
tools.camera._focus_existing_tab (a 3 s osascript against Chrome/Safari) and tools.system.get_server_info
(selects the http:// or the file:// URL).
  1. an awkward name ("../x/A&B") writes one file directly inside /tmp, the file name still starts with
     "bambu-charts-", and the file:// URL open_charts opens decodes to the path it reports;
  2. a name that would climb out of a planted directory ("<dir>/../pwn") writes nothing outside the
     bambu-charts-* namespace;
  3. ordinary names (letters, digits, "-", "_", ".", "~", a space, non-ASCII) give the byte-for-byte
     file name they gave before the fix (anchors: they pass before and after);
  4. two different names get different file names ("A/B" vs "A%2FB"); whether two such names can still
     land on one file is up to the filesystem (APFS folds case and Unicode normalisation), and no test
     claims otherwise;
  5. a symlink planted at the target path is refused, not followed (the victim file is untouched), both
     when it is there before the check and when it is planted after the check, just before the write;
  6. the http:// URL is unchanged, and tools/charts.py adds no public callable (registration rule);
  7. a printer name whose file name would be too long for the filesystem (300 ASCII characters, about 80
     CJK characters) still gives an "output_path" no longer than 200 bytes and never raises; two long names
     sharing a head stay distinct; a name whose file name is exactly 200 bytes is left as it is;
  8. any other failure to write the file is returned as {"error": "write_failed", "detail": ...}, and a
     new file is created private to the owner (0600) while an existing one is replaced, not appended to.

Every file this test writes has a unique per-run token in its name and is deleted afterwards.

Run directly (no pytest needed):  .venv/bin/python3 test_charts_paths.py
"""

import errno
import hashlib
import os
import stat
import sys
import tempfile
import traceback
import uuid
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import webbrowser  # noqa: E402

from data_collector import data_collector  # noqa: E402
from tools import camera, charts, system  # noqa: E402

TMP = Path("/tmp")
PREFIX = "bambu-charts-"


class _Env:
    """Stub the three side effects, register a collector for `name`, and remove everything it wrote."""

    def __init__(self, name: str, api_port: int = 0):
        self.name = name
        self.api_port = api_port
        self.opened: list[str] = []
        self.made: list[Path] = []

    def __enter__(self):
        self._orig = (webbrowser.open, camera._focus_existing_tab, system.get_server_info)
        webbrowser.open = lambda url, *a, **k: (self.opened.append(url), True)[1]
        camera._focus_existing_tab = lambda url: False
        system.get_server_info = lambda: {"api_port": self.api_port} if self.api_port else {}
        data_collector.register_printer(self.name)
        return self

    def __exit__(self, *exc):
        webbrowser.open, camera._focus_existing_tab, system.get_server_info = self._orig
        with data_collector._lock:
            data_collector._collectors.pop(self.name, None)
        for p in self.made:
            if p.is_symlink() or p.is_file():
                p.unlink()
            elif p.is_dir():
                p.rmdir()

    def track(self, *paths: Path):
        self.made.extend(paths)


def _token() -> str:
    return uuid.uuid4().hex[:10]


def _as_path(url: str) -> str:
    parts = urlsplit(url)
    assert parts.scheme == "file", url
    return unquote(parts.path)


def test_awkward_name_writes_one_file_directly_inside_tmp():
    t = _token()
    name = f"../x/A&B-{t}"
    with _Env(name) as env:
        expected_leaf = f"{PREFIX}..%2Fx%2FA&B-{t}.html"
        env.track(TMP / expected_leaf)
        res = charts.open_charts(name)
        assert "output_path" in res, res
        out = Path(res["output_path"])
        env.track(out)
        assert out.exists(), f"open_charts reported {out} but nothing was written there"
        assert out.parent.resolve() == TMP.resolve(), (out, out.parent.resolve())
        assert out.name.startswith(PREFIX) and "/" not in out.name, out.name
        assert out.name == expected_leaf, out.name
        # the file:// URL that was opened is the file that was written
        assert len(env.opened) == 1, env.opened
        assert _as_path(env.opened[0]) == str(out), (env.opened[0], str(out))


def test_name_cannot_climb_out_of_the_bambu_charts_namespace():
    t = _token()
    planted = TMP / f"{PREFIX}{t}"
    escapee = TMP / f"pwn-{t}.html"
    name = f"{t}/../pwn-{t}"
    with _Env(name) as env:
        env.track(escapee, planted)
        planted.mkdir()
        res = charts.open_charts(name)
        assert not escapee.exists(), f"name walked out of the namespace and wrote {escapee}"
        assert "output_path" in res, res
        out = Path(res["output_path"])
        env.track(out)
        assert out.parent.resolve() == TMP.resolve() and out.name.startswith(PREFIX), out


def test_ordinary_names_give_the_pre_fix_file_name():
    t = _token()
    for name in (f"H2D-{t}_1.a~b", f"My Printer {t}", f"café ☃ {t}", f"A+B #1 {t}"):
        with _Env(name) as env:
            res = charts.open_charts(name)
            assert "output_path" in res, (name, res)
            env.track(Path(res["output_path"]))
            assert res["output_path"] == f"/tmp/bambu-charts-{name}.html", (name, res)
            assert Path(res["output_path"]).exists(), name


def test_distinct_names_get_distinct_file_names():
    t = _token()
    a, b = f"A/B-{t}", f"A%2FB-{t}"
    with _Env(a) as ea, _Env(b) as eb:
        ra, rb = charts.open_charts(a), charts.open_charts(b)
        for env, res in ((ea, ra), (eb, rb)):
            if "output_path" in res:
                env.track(Path(res["output_path"]))
        assert "output_path" in ra and "output_path" in rb, (ra, rb)
        assert ra["output_path"] != rb["output_path"], (a, b, ra, rb)


def test_a_symlink_planted_at_the_target_is_refused_not_followed():
    t = _token()
    name = f"H2D-{t}"
    with tempfile.TemporaryDirectory() as victim_dir:
        victim = Path(victim_dir) / "victim.txt"
        victim.write_text("precious")
        link = TMP / f"{PREFIX}{name}.html"
        os.symlink(victim, link)
        with _Env(name) as env:
            env.track(link)
            res = charts.open_charts(name)
            assert victim.read_text() == "precious", "open_charts wrote through the planted symlink"
            assert res.get("error") == "unsafe_output_path", res
            assert isinstance(res.get("detail"), str), res
            assert not env.opened, env.opened


_MAX_LEAF_BYTES = 200


def _check_written(env, res, name):
    """A long name succeeded: the file is a direct child of /tmp, named bambu-charts-*.html, at most 200 bytes."""
    assert isinstance(res, dict) and "output_path" in res, (name[:20], res)
    out = Path(res["output_path"])
    env.track(out)
    assert out.exists(), out
    assert out.parent.resolve() == TMP.resolve(), out
    assert out.name.startswith(PREFIX) and out.name.endswith(".html"), out.name
    assert len(out.name.encode("utf-8")) <= _MAX_LEAF_BYTES, (len(out.name.encode("utf-8")), out.name)
    return out


def test_over_long_names_return_a_short_file_instead_of_raising():
    t = _token()
    for name in ("a" * 280 + t + "a" * 10, "漢" * 80 + t):
        assert len((PREFIX + name + ".html").encode("utf-8")) > 255, "anchor: the unshortened leaf is too long"
        with _Env(name) as env:
            _check_written(env, charts.open_charts(name), name)


def test_two_long_names_sharing_a_head_stay_distinct():
    t = _token()
    a, b = "a" * 300 + t + "X", "a" * 300 + t + "Y"
    with _Env(a) as ea, _Env(b) as eb:
        pa = _check_written(ea, charts.open_charts(a), a)
        pb = _check_written(eb, charts.open_charts(b), b)
        assert pa != pb, (pa, pb)
        for name, out in ((a, pa), (b, pb)):
            digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
            assert out.name.endswith(f"-{digest}.html"), (out.name, digest)


def test_file_name_length_boundary_is_exactly_200_bytes():
    exact = "b" * (200 - len(PREFIX) - len(".html"))
    assert charts._chart_file_path(exact).name == f"{PREFIX}{exact}.html"      # 200 bytes: untouched
    assert len(charts._chart_file_path(exact).name.encode()) == 200
    over = exact + "c"
    out = charts._chart_file_path(over)                                         # 201 bytes: shortened + hash
    assert out.name != f"{PREFIX}{over}.html" and len(out.name.encode()) <= 200, out.name
    assert out.name.endswith(f"-{hashlib.sha256(over.encode()).hexdigest()[:12]}.html"), out.name
    assert out.name.startswith(PREFIX + "b" * 100), out.name                    # still readable: a head is kept
    cjk = "漢" * 80                                                             # cut lands inside a multibyte char
    leaf = charts._chart_file_path(cjk).name
    assert len(leaf.encode("utf-8")) <= 200 and "\ufffd" not in leaf, leaf
    assert leaf.startswith(PREFIX + "漢" * 10), leaf


def _plant_after_check(victim: Path, planted: list):
    """A _chart_file_path that runs the real check and then plants a symlink at its answer: the gap the fix closes."""
    real = charts._chart_file_path

    def wrapper(name):
        out = real(name)
        assert out is not None
        os.symlink(victim, out)
        planted.append(out)
        return out
    return real, wrapper


def test_a_symlink_planted_after_the_check_is_refused_not_followed():
    for existing_victim in (True, False):
        t = _token()
        name = f"H2D-late-{t}"
        with tempfile.TemporaryDirectory() as victim_dir:
            victim = Path(victim_dir) / "victim.txt"
            if existing_victim:
                victim.write_text("precious")
            planted: list = []
            real, wrapper = _plant_after_check(victim, planted)
            with _Env(name) as env:
                env.track(TMP / f"{PREFIX}{name}.html")
                charts._chart_file_path = wrapper
                try:
                    res = charts.open_charts(name)
                finally:
                    charts._chart_file_path = real
                assert planted, "the symlink was never planted: the test did not exercise the write"
                assert res.get("error") == "unsafe_output_path", (existing_victim, res)
                assert isinstance(res.get("detail"), str), res
                assert not env.opened, env.opened
                if existing_victim:
                    assert victim.read_text() == "precious", "open_charts wrote through the symlink"
                else:
                    assert not victim.exists(), "open_charts created the file the dangling symlink pointed at"


def test_any_other_write_failure_is_returned_as_write_failed():
    t = _token()
    name = f"H2D-dir-{t}"
    target = TMP / f"{PREFIX}{name}.html"
    with _Env(name) as env:
        env.track(target)
        target.mkdir()        # a directory where the file goes: os.open fails EISDIR, which is no symlink
        res = charts.open_charts(name)
        assert res.get("error") == "write_failed", res
        assert isinstance(res.get("detail"), str) and res["detail"], res
        assert set(res) == {"error", "detail"}, res
        assert not env.opened, env.opened


def test_new_file_is_owner_only_and_an_existing_one_is_replaced():
    t = _token()
    name = f"H2D-mode-{t}"
    target = TMP / f"{PREFIX}{name}.html"
    with _Env(name) as env:
        env.track(target)
        res = charts.open_charts(name)
        assert res.get("output_path") == str(target), res
        assert stat.S_IMODE(target.stat().st_mode) & 0o077 == 0, oct(target.stat().st_mode)
        fresh = target.read_text(encoding="utf-8")
        assert fresh.lstrip().startswith("<"), fresh[:60]
        target.write_text("JUNK" * 2_000_000)      # existing file, longer than the dashboard
        res = charts.open_charts(name)
        assert res.get("output_path") == str(target), res
        again = target.read_text(encoding="utf-8")
        assert "JUNKJUNK" not in again and again.lstrip().startswith("<"), again[:60]


def test_http_url_is_unchanged():
    t = _token()
    for name in (f"H2D-{t}", f"Lab A&B+C #{t}"):
        with _Env(name, api_port=4242) as env:
            res = charts.open_charts(name)
            env.track(Path(res["output_path"]))
            assert env.opened == [f"http://localhost:4242/api/charts?printer={quote(name, safe='')}"], env.opened


def test_charts_adds_no_public_callable():
    # Registration rule: every public callable with a docstring in tools/charts.py is an MCP tool.
    public = sorted(
        n for n in dir(charts)
        if not n.startswith("_") and callable(getattr(charts, n))
        and getattr(getattr(charts, n), "__module__", None) == charts.__name__
        and getattr(getattr(charts, n), "__doc__", None)
    )
    assert public == ["open_charts", "render_charts_html", "render_charts_panels"], public


if __name__ == "__main__":
    failed = 0
    for fname, fn in sorted(globals().items()):
        if fname.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok   {fname}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL {fname}: {type(e).__name__}: {e}")
                traceback.print_exc(limit=3)
    sys.exit(1 if failed else 0)
