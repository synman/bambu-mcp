"""Conformity test: every bambu-mcp tool declares a title and a read-only classification.

Reads the live list_tools() a client receives from a scratch FastMCP server that goes through
the same register_tools() the daemon uses, not the decorators. Following the mcp-builder skill,
step 3.3 (isaac reference/qvc_conventions.md, "Tool annotations"):
  1. the tool COUNT is stated here, so a rename or an unseen registration path fails instead
     of emptying into a vacuous pass;
  2. title and readOnlyHint are present on every tool, inside annotations;
  3. every explicit pre-existing declaration is unchanged (there were none before the registry
     existed, so PRIOR is empty);
  4. every guarded tool (one whose input schema has user_permission) is declared a write.
Title uniqueness across servers cannot be checked from this repo; within it, it is asserted.

Nothing here touches a printer.

Run directly (no pytest needed):  .venv/bin/python3 test_tool_annotations.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from tools import _registry  # noqa: E402

EXPECTED_TOOLS = 101
EXPECTED_GUARDED = 50
GUARD = "user_permission"
PRIOR: dict[str, dict] = {}


def _live():
    mcp = FastMCP(name="bambu-mcp-test")
    count = _registry.register_tools(mcp)
    return count, asyncio.run(mcp.list_tools())


def test_tool_count_is_stated():
    count, tools = _live()
    assert count == EXPECTED_TOOLS, count
    assert len(tools) == EXPECTED_TOOLS, len(tools)


def test_every_tool_declares_title_and_read_only_hint():
    _, tools = _live()
    bad = [t.name for t in tools
           if t.annotations is None or not t.annotations.title or t.annotations.readOnlyHint is None]
    assert not bad, bad


def test_prior_declarations_are_unchanged():
    _, tools = _live()
    by_name = {t.name: t for t in tools}
    for name, want in PRIOR.items():
        got = by_name[name].annotations
        for key, value in want.items():
            assert getattr(got, key) == value, (name, key)


def test_every_guarded_tool_is_declared_a_write():
    _, tools = _live()
    guarded = [t for t in tools if GUARD in (t.inputSchema.get("properties") or {})]
    assert len(guarded) == EXPECTED_GUARDED, len(guarded)
    wrong = [t.name for t in guarded if t.annotations.readOnlyHint is not False]
    assert not wrong, wrong


def test_titles_are_unique_within_the_server():
    _, tools = _live()
    titles = [t.annotations.title for t in tools]
    assert len(set(titles)) == len(titles), sorted({x for x in titles if titles.count(x) > 1})


def test_a_tool_without_an_entry_fails_registration():
    saved = _registry.ANNOTATIONS.pop("get_temperatures")
    try:
        try:
            _registry.register_tools(FastMCP(name="bambu-mcp-test"))
        except RuntimeError as exc:
            assert "get_temperatures" in str(exc)
        else:
            raise AssertionError("registration accepted a tool with no annotations entry")
    finally:
        _registry.ANNOTATIONS["get_temperatures"] = saved


def test_an_orphaned_entry_fails_registration():
    _registry.ANNOTATIONS["not_a_tool"] = ("Bambu: Not A Tool", True, False, True, False)
    try:
        try:
            _registry.register_tools(FastMCP(name="bambu-mcp-test"))
        except RuntimeError as exc:
            assert "not_a_tool" in str(exc)
        else:
            raise AssertionError("registration accepted an orphaned entry")
    finally:
        del _registry.ANNOTATIONS["not_a_tool"]


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
