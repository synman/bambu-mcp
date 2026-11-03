"""The server's version of record: one constant, plain semver, reported by initialize, equal to pyproject.

`__version__` in server.py is the only source. pyproject.toml
repeats it, the low-level MCP server carries it, the REST API and mDNS advertisement read it, and the
initialize response reports it. hooks/version_bump_gate.py gates a commit on it, and SERVING in that
gate is pinned here against the tree so a new module cannot ship un-gated.

Nothing here touches a printer: importing server registers tools and starts nothing.

Run directly (no pytest needed):  .venv/bin/python3 test_version.py
"""

import importlib.util
import json
import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import server  # noqa: E402

SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
INITIALIZE = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
               "clientInfo": {"name": "version-test", "version": "0"}},
}
HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}

# Python files that exist in the tree and are NOT served, each with its reason. Anything else that
# the gate does not list in SERVING fails test_every_python_file_is_serving_or_declared_not.
NOT_SERVING = (
    "make.py",                      # install / version-sync helper
    "gen_docs.py",                  # publishes the bpm docs site
    "smoke_test.py",                # manual smoke run
    "thermal_snapshot_monitor.py",  # standalone script, imported by nothing
    "test_*.py",                    # tests
    "calibration/*.py",             # operator calibration scripts, not imported by the daemon
    "docs/*",                       # docs and proof-of-concept scripts
    "hooks/*.py",                   # the repo-local pre-commit gate
    "uploads/*",                    # runtime upload directory
)
SKIP_DIRS = {".venv", "__pycache__", ".git", "bambu_mcp.egg-info", "node_modules"}


def _gate():
    spec = importlib.util.spec_from_file_location("version_bump_gate", ROOT / "hooks" / "version_bump_gate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _python_files():
    found = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                found.append(str(Path(dirpath, name).relative_to(ROOT)))
    return sorted(found)


def test_version_is_a_plain_semver_triple():
    assert SEMVER.fullmatch(server.__version__), server.__version__


def test_pyproject_version_equals_the_server_constant():
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert declared == server.__version__, f"pyproject {declared} != server.__version__ {server.__version__}"


def test_the_lowlevel_server_carries_the_constant():
    assert server.mcp._mcp_server.version == server.__version__
    options = server.mcp._mcp_server.create_initialization_options()
    assert options.server_version == server.__version__


def test_initialize_over_http_reports_the_constant_not_the_sdk_version():
    from starlette.testclient import TestClient

    app = server.mcp.streamable_http_app()
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        response = client.post("/mcp", headers=HEADERS, json=INITIALIZE)
    assert response.status_code == 200, response.text
    data = next(line[6:] for line in response.text.splitlines() if line.startswith("data: "))
    info = json.loads(data)["result"]["serverInfo"]
    assert info == {"name": "bambu-mcp", "version": server.__version__}, info


def test_no_serving_file_reads_the_version_from_package_metadata():
    gate = _gate()
    offenders = []
    for rel in _python_files():
        if not gate.is_serving(rel):
            continue
        text = (ROOT / rel).read_text()
        if re.search(r"importlib\.metadata", text) and re.search(r"""version\(\s*["']bambu-mcp["']""", text):
            offenders.append(rel)
    assert not offenders, f"derive the version from server.__version__, not metadata: {offenders}"


def test_the_rest_api_and_mdns_read_the_constant():
    import api_server

    assert (ROOT / "server.py").read_text().count("api_server.SERVER_VERSION = __version__") == 1
    api_server.SERVER_VERSION = server.__version__
    doc = api_server.build_openapi_document(api_server._build_app())
    assert doc["info"]["version"] == server.__version__, doc["info"]
    assert '"version": SERVER_VERSION' in (ROOT / "api_server.py").read_text()   # the mDNS TXT record


def test_serving_patterns_each_match_something():
    gate = _gate()
    files = _python_files()
    assert len(files) > 30, "tree walk found too little to pin anything"
    dead = [pat for pat in gate.SERVING if not any(_match(f, pat) for f in files)]
    assert not dead, f"SERVING patterns that match no file: {dead}"


def test_every_python_file_is_serving_or_declared_not():
    gate = _gate()
    stray = [f for f in _python_files() if not gate.is_serving(f) and not any(_match(f, p) for p in NOT_SERVING)]
    assert not stray, f"add to SERVING in hooks/version_bump_gate.py, or to NOT_SERVING here with a reason: {stray}"


def test_the_gate_names_this_app_and_its_version_file():
    gate = _gate()
    assert gate.APP == "bambu-mcp"
    assert gate.VERSION_FILE == "server.py"
    assert gate.VERSION_RE.search((ROOT / gate.VERSION_FILE).read_text()).group(1) == server.__version__
    assert gate.is_serving(gate.VERSION_FILE)


def _match(path: str, pattern: str) -> bool:
    import fnmatch

    return fnmatch.fnmatchcase(path, pattern)


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
