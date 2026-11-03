"""hooks/version_bump_gate.py, end to end in a scratch git repo.

Every case runs the real script against a real index. The install case makes git itself dispatch
the hook on commit. Nothing here touches this repo's own index or a printer.

Run directly (no pytest needed):  .venv/bin/python3 test_version_bump_gate.py
"""

import contextlib
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GATE = ROOT / "hooks" / "version_bump_gate.py"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def _mod():
    spec = importlib.util.spec_from_file_location("version_bump_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(cwd, *args, check=True):
    env = dict(os.environ, **GIT_ENV)
    return subprocess.run(["git", "-c", "commit.gpgsign=false"] + list(args), cwd=cwd, env=env,
                          capture_output=True, text=True, check=check)


def _run_gate(cwd, *args):
    env = dict(os.environ, **GIT_ENV)
    return subprocess.run([sys.executable, str(GATE)] + list(args), cwd=cwd, env=env,
                          capture_output=True, text=True)


@contextlib.contextmanager
def _repo():
    """A scratch repo with a committed server.py at 1.0.0, one serving module and a README."""
    mod = _mod()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _git(root, "init", "-q", "--template=", ".")
        version = root / mod.VERSION_FILE
        version.write_text('"""probe"""\n__version__ = "1.0.0"\n')
        probe = root / "tools" / "probe.py"    # matches the tools/*.py serving pattern
        probe.parent.mkdir()
        probe.write_text("# serving\n")
        (root / "README.md").write_text("docs\n")
        assert mod.is_serving("tools/probe.py") and not mod.is_serving("README.md")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "--no-verify", "-m", "base")
        yield root, mod, probe, version


def _touch(path):
    with path.open("a") as fh:
        fh.write("# touched\n")


def test_decide_is_pure_and_only_checks_movement():
    mod = _mod()
    assert mod.decide(["README.md"], "1.0.0", "1.0.0") == (0, "")
    assert mod.decide(["tools/probe.py"], "1.0.0", "1.0.0")[0] == 1
    assert mod.decide(["tools/probe.py"], "1.0.0", "0.9.0") == (0, "")   # moved: direction is not judged
    assert mod.decide(["tools/probe.py"], "1.0.0", "2.0.0") == (0, "")
    assert mod.decide(["tools/probe.py"], None, None) == (0, "")         # nothing committed to compare
    code, msg = mod.decide(["tools/probe.py"], "1.0.0", None)
    assert code == 1 and "no __version__" in msg


def test_blocks_serving_change_without_bump():
    with _repo() as (root, mod, probe, _):
        _touch(probe)
        _git(root, "add", "-A")
        res = _run_gate(root)
        assert res.returncode == 1, res
        assert "BLOCKED" in res.stderr and mod.VERSION_FILE in res.stderr and "tools/probe.py" in res.stderr


def test_blocks_a_change_to_the_version_file_itself_that_does_not_move_it():
    with _repo() as (root, mod, _, version):
        version.write_text(version.read_text() + "# a wording change in the entry file\n")
        _git(root, "add", "-A")
        assert _run_gate(root).returncode == 1


def test_passes_once_version_moved():
    with _repo() as (root, mod, probe, version):
        _touch(probe)
        version.write_text(version.read_text().replace('"1.0.0"', '"1.0.1"'))
        _git(root, "add", "-A")
        res = _run_gate(root)
        assert res.returncode == 0 and res.stdout == "" and res.stderr == ""


def test_silent_outside_serving():
    with _repo() as (root, _, _, _):
        (root / "README.md").write_text("docs changed\n")
        (root / "test_x.py").write_text("# a test\n")
        _git(root, "add", "-A")
        res = _run_gate(root)
        assert res.returncode == 0 and res.stdout == "" and res.stderr == ""


def test_install_makes_git_dispatch_the_gate():
    with _repo() as (root, mod, probe, version):
        assert _run_gate(root, "--install").returncode == 0
        hook = root / ".git" / "hooks" / "pre-commit"
        assert not hook.is_symlink()                    # a copy, never a symlink
        assert hook.read_bytes() == GATE.read_bytes()   # byte-equal, so drift is a red test
        assert os.access(hook, os.X_OK)
        _touch(probe)
        _git(root, "add", "-A")
        refused = _git(root, "commit", "-q", "-m", "no bump", check=False)
        assert refused.returncode != 0 and "BLOCKED" in refused.stderr
        version.write_text(version.read_text().replace('"1.0.0"', '"1.0.1"'))
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "bumped")


def test_install_refuses_a_foreign_pre_commit():
    with _repo() as (root, _, _, _):
        hook = root / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\necho someone else\n")
        res = _run_gate(root, "--install")
        assert res.returncode == 1 and "call `python3" in res.stderr
        assert hook.read_text() == "#!/bin/sh\necho someone else\n"
        assert sorted(os.listdir(hook.parent)) == ["pre-commit"]   # nothing is chained into another hook


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
