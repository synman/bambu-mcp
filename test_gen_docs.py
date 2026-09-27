"""Checks for gen_docs.py, the generator of the published bambu-mcp docs pages.

Builds every page in memory from the live tool registry and OpenAPI document, and writes
nothing: README.md and the bambu-printer-manager docs tree are left alone. Nothing here
touches a printer.

Run directly (no pytest needed):  .venv/bin/python3 test_gen_docs.py
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gen_docs  # noqa: E402

TOOLS = gen_docs.load_tools()
OPENAPI = gen_docs.load_openapi()
PAGES = gen_docs.tool_pages(TOOLS)
REST, ROUTES = gen_docs.rest_page(OPENAPI)


def test_every_tool_has_exactly_one_section():
    headings = [h for text in PAGES.values() for h in re.findall(r"(?m)^## (\w+)$", text)]
    names = sorted(t["name"] for t in TOOLS)
    assert sorted(headings) == names, set(headings) ^ set(names)
    assert len(names) == len(_registry_names()), len(names)


def test_every_category_page_is_in_the_nav():
    nav = gen_docs.summary(TOOLS)
    for rel in PAGES:
        assert f"]({rel})" in nav, rel
    for rel in ("index.md", "tools/index.md", "rest-api.md"):
        assert f"]({rel})" in nav, rel


def test_every_route_and_method_is_documented():
    documented = {}
    for methods, path in re.findall(r"(?m)^### ([A-Z, ]+) (/\S+)$", REST):
        documented.setdefault(path, set()).update(methods.split(", "))
    expected = {path: {m.upper() for m in ms} for path, ms in OPENAPI["paths"].items()}
    assert documented == expected, {k for k in set(documented) | set(expected) if documented.get(k) != expected.get(k)}
    assert ROUTES == len(OPENAPI["paths"])


def test_angle_brackets_outside_code_are_escaped():
    # '<name>' outside a code span or fence would be swallowed as an HTML tag.
    for rel, text in {**PAGES, "rest-api.md": REST}.items():
        prose = re.sub(r"```.*?```", "", text, flags=re.S)
        prose = gen_docs.CODE_SPAN_RE.sub("", prose)
        prose = prose.replace(gen_docs.BANNER, "")
        assert not re.search(r"<[A-Za-z_/]", prose), (rel, re.search(r".{30}<[A-Za-z_/].{30}", prose))


def test_aligned_block_is_fenced_and_prose_is_not():
    md = gen_docs.to_markdown("Payload fields:\n  job_started:       {} (empty)\n  job_paused:        stage_id")
    assert md.startswith("```"), md
    md = gen_docs.to_markdown("native   resolution=native  quality=85\nhigh     resolution=1080p   quality=85")
    assert md.startswith("```"), md  # column-aligned at column 0: only the alignment check fences it
    md = gen_docs.to_markdown("Intro line:\n- first bullet\n  continued\n- second '<name>'")
    assert "Intro line:\n\n- first bullet" in md, md
    assert "&lt;name>" in md and "```" not in md, md


def _registry_names():
    from tools import _registry
    return {fn.__name__ for fn in _registry.registered_tools()}


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as exc:
                failed += 1
                print(f"FAIL {name}: {exc}")
    sys.exit(1 if failed else 0)
