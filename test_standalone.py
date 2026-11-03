"""The tree names no other component, no machine and no home layout.

An application references nothing outside itself in code, comments or docs: no sibling tool, no
host, no person's account, no home directory layout. Where it finds its environment is read from the
process environment, never written in source. This scans every text file the repo tracks for such names.
The banned names are assembled from pieces so this file does not hold them itself. A planted fault
in a scratch tree proves the scan can fail, and a second proves it ignores what it should.

An exception would be listed in EXEMPT with its reason, so it stays visible.

Run directly (no pytest needed):  .venv/bin/python3 test_standalone.py
"""

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

_BANNED = [
    "is" + "aac",
    "node-" + "kb",
    "kb_" + "get",
    "kb_" + "search",
    "/Users/",
    "shell" + "ware",
    r"10\.151\.",
    "ds16" + "21",
    r"~/" + "ai",
    r"\.cla" + "ude",
    "workspace-" + "paths",
    "mcp-" + "reload",
    "bambu-printer-" + "app",
    "web" + "camd",
    "go2" + "rtc",
    "fw-" + "fetch",
]
BANNED = re.compile("|".join(_BANNED))

SKIP_DIRS = {".venv", ".git", "__pycache__", "bambu_mcp.egg-info", "node_modules", "uploads"}
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".pyc", ".log", ".enc", ".ico", ".woff", ".woff2"}

# path -> reason it stays. Empty: nothing in the tree is exempt.
EXEMPT: dict[str, str] = {}


def tracked(root: Path):
    """Files git tracks in *root*; every file when it is not a git tree."""
    done = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True)
    if done.returncode != 0:
        return sorted(p for p in root.rglob("*") if p.is_file())
    return [root / name for name in done.stdout.split("\0") if name]


def scan(root: Path, files=None):
    hits = []
    for path in sorted(tracked(root) if files is None else files):
        rel = path.relative_to(root)
        if not path.is_file() or set(rel.parts) & SKIP_DIRS or path.suffix.lower() in SKIP_SUFFIX:
            continue
        relname = rel.as_posix()
        if relname in EXEMPT or relname == "test_standalone.py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if BANNED.search(line):
                hits.append(f"{relname}:{n}: {line.strip()[:100]}")
    return hits


def test_the_tree_names_no_tool_host_or_home_layout():
    hits = scan(ROOT)
    assert len(tracked(ROOT)) > 50, "git listing found too little to scan"
    assert not hits, "tree names its environment:\n" + "\n".join(hits[:40])


def test_the_scan_catches_a_planted_fault_and_skips_what_it_should():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "ok.py").write_text("print('plain')\n")
        (root / "bad.py").write_text("PATH = '/Users/someone/project'\n")
        (root / ".venv").mkdir()
        (root / ".venv" / "lib.py").write_text("x = '/Users/someone'\n")
        (root / "image.png").write_bytes(b"\x89PNG /Users/x")
        found = scan(root)
        assert len(found) == 1 and found[0].startswith("bad.py:1:"), found


def test_every_exemption_still_exists_and_still_needs_it():
    for rel in EXEMPT:
        path = ROOT / rel
        if path.exists():
            assert BANNED.search(path.read_text(encoding="utf-8")), f"{rel} no longer needs its exemption"


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
