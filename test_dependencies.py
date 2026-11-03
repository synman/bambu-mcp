"""The dependencies are the declared ones, and every declared one is used.

bambu-mcp is a consumer: it may name what it depends on and must say so. README "Dependencies" is
the statement; this test enforces it. Every third-party import in a tracked Python file must be
declared in pyproject.toml, arrive through a declared package (listed in VIA_DECLARED with the
package that brings it), belong to the standalone scripts (SCRIPTS_ONLY), or be a known gap
(KNOWN_UNDECLARED, each with its reason). Every package pyproject.toml declares must be imported
somewhere, so a stale declaration fails too. A planted fault proves the scan can fail.

Run directly (no pytest needed):  .venv/bin/python3 test_dependencies.py
"""

import ast
import importlib.metadata as metadata
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# import name -> declared package that brings it in
VIA_DECLARED = {
    "pydantic": "mcp",
    "starlette": "mcp",
    "webcolors": "bambu-printer-manager",
}
# import name -> why only the standalone scripts need it
SCRIPTS_ONLY = {
    "matplotlib": "thermal_snapshot_monitor.py and docs/*.py draw plots",
    "mpl_toolkits": "part of matplotlib",
    "scipy": "thermal_snapshot_monitor.py image filters",
}
SCRIPT_FILES = ("thermal_snapshot_monitor.py", "docs/")
# import name -> reason it is used by served code but not declared. Declaring it is a dependency
# change that needs the owner's approval, so it is listed here instead of hidden.
KNOWN_UNDECLARED = {
    "matplotlib": "camera/coord_transform.py imports matplotlib.path inside a function",
}
# files that do not parse, and why (a test that skipped them silently would hide the gap)
UNPARSEABLE = {"calibration/corner_calibration.py": "IndentationError at line 483"}


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared() -> dict[str, str]:
    """pyproject dependency (normalised) -> its requirement string."""
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    return {_norm(re.split(r"[\s\[<>=!~;@]", d, maxsplit=1)[0]): d for d in deps}


def tracked(root: Path):
    done = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, text=True)
    names = [n for n in done.stdout.split("\0") if n] if done.returncode == 0 else []
    return [root / n for n in names if n.endswith(".py")]


def third_party_imports(root: Path, files):
    """{import name: [files]} for every top-level import that is not stdlib or local."""
    local = {p.stem for p in root.glob("*.py")} | {p.name for p in root.iterdir() if (p / "__init__.py").exists()}
    found, unparsed = {}, []
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            unparsed.append(rel)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            for name in names:
                if name not in sys.stdlib_module_names and name not in local:
                    found.setdefault(name, []).append(rel)
    return found, unparsed


def undeclared(found, allowed_runtime, files_by_name=None):
    """Imports no rule allows. SCRIPTS_ONLY imports are allowed only from script files."""
    bad = []
    for name, users in sorted(found.items()):
        for user in users:
            if name in allowed_runtime or name in KNOWN_UNDECLARED:
                continue
            if name in SCRIPTS_ONLY and user.startswith(SCRIPT_FILES):
                continue
            bad.append(f"{name} in {user}")
    return bad


def _allowed():
    dist_of = metadata.packages_distributions()
    names = set(declared()) | {_norm(v) for v in VIA_DECLARED.values()}
    out = {imp for imp, dists in dist_of.items() if any(_norm(d) in names for d in dists)}
    out |= set(VIA_DECLARED)
    return out, dist_of


def test_every_third_party_import_is_declared_or_accounted_for():
    files = tracked(ROOT)
    assert len(files) > 40, "git listing found too little to scan"
    found, unparsed = third_party_imports(ROOT, files)
    assert sorted(unparsed) == sorted(UNPARSEABLE), f"unparseable files changed: {unparsed}"
    allowed, _ = _allowed()
    bad = undeclared(found, allowed)
    assert not bad, "undeclared imports:\n" + "\n".join(bad)


def test_every_declared_dependency_is_imported_somewhere():
    found, _ = third_party_imports(ROOT, tracked(ROOT))
    dist_of = metadata.packages_distributions()
    unused = []
    for dist in declared():
        imports = {imp for imp, dists in dist_of.items() if dist in {_norm(d) for d in dists}}
        if not imports & set(found):
            unused.append(dist)
    assert not unused, f"declared but never imported: {unused}"


def test_the_readme_dependencies_section_names_every_declared_package():
    text = (ROOT / "README.md").read_text()
    section = text[text.index("## Dependencies"):]
    section = section[: section.find("\n## ", 5)] if "\n## " in section[5:] else section
    low = section.lower().replace("_", "-")
    missing = [d for d in declared() if d not in low]
    assert not missing, f"README Dependencies section omits: {missing}"
    for name in list(VIA_DECLARED) + list(KNOWN_UNDECLARED):
        assert name in section, f"README Dependencies section omits {name}"


def test_the_scan_catches_a_planted_import_and_allows_a_declared_one():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.py").write_text("import json\nimport flask\n")
        (root / "b.py").write_text("import not_a_declared_thing\nimport a\n")
        found, _ = third_party_imports(root, [root / "a.py", root / "b.py"])
        assert undeclared(found, {"flask"}) == ["not_a_declared_thing in b.py"], found


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
