#!/usr/bin/env python3
"""Version-bump gate for bambu-mcp — a repo-local pre-commit hook.

Blocks a commit that stages a change to one of this MCP's SERVING files
while the ``__version__`` constant in VERSION_FILE is unchanged against
HEAD. It checks only that the version MOVED — whether the step is major,
minor, or patch is the committer's own judgment. A commit that touches no
serving file passes silently.

Install once per clone::

    python3 hooks/version_bump_gate.py --install

Where the isaac commit-policy gate owns ``.git/hooks/pre-commit`` the hook
is installed as ``.git/hooks/pre-commit.pre-isaac`` — that gate runs first and
``exec``s a ``*.pre-isaac`` hook after it passes, so this hook's exit code
becomes the commit's. Where no pre-commit hook exists it is installed as
``pre-commit`` directly. A foreign pre-commit hook is never overwritten.
The install is a COPY, never a symlink — a symlink into the worktree
dangles in a linked worktree — so re-run ``--install`` after editing
this file.

This gate lives in this repo on purpose: nothing about it is installed
into, or read from, isaac.
"""
import fnmatch
import importlib.util
import os
import re
import shutil
import subprocess
import sys

APP = "bambu-mcp"
VERSION_FILE = "server.py"
VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']', re.M)
# Repo-relative paths that serve the MCP (fnmatch patterns; ``*`` also
# matches ``/``). bambu-mcp runs from this tree under launchd, so the serving
# set is every module the daemon can import plus the version file itself. Tests,
# docs, deploy and config files, calibration scripts and the standalone tools
# (make.py, gen_docs.py, smoke_test.py, thermal_snapshot_monitor.py) do not
# change what the running server does. test_version.py pins this list against
# the tree, so a new module cannot ship un-gated.
SERVING = (
    "server.py",
    "api_server.py",
    "auth.py",
    "daemon_port.py",
    "data_collector.py",
    "job_project.py",
    "notifications.py",
    "port_pool.py",
    "secrets_store.py",
    "session_manager.py",
    "user_prefs.py",
    "tools/*.py",
    "camera/*.py",
    "prompts/*.py",
    "resources/*.py",
)
ISAAC_MARKER = "ISAAC-COMMIT-POLICY-GATE"
HOOK = "pre-commit"


def _git(*args):
    return subprocess.run(["git"] + list(args), capture_output=True, text=True)


def staged_paths():
    res = _git("diff", "--cached", "--name-only", "--no-renames", "-z")
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or "git diff --cached failed")
    return [p for p in res.stdout.split("\0") if p]


def version_at(rev):
    """Version string in VERSION_FILE at *rev* (``""`` = the index); None if absent."""
    res = _git("show", "%s:%s" % (rev, VERSION_FILE))
    if res.returncode != 0:
        return None
    match = VERSION_RE.search(res.stdout)
    return match.group(1) if match else None


def is_serving(path):
    return any(fnmatch.fnmatchcase(path, pat) for pat in SERVING)


def decide(staged, old, new):
    """Return (exit_code, message). Pure — reads no git state."""
    hit = sorted(p for p in staged if is_serving(p))
    if not hit:
        return 0, ""
    if old is None:  # no committed version to compare against
        return 0, ""
    if new is not None and new != old:
        return 0, ""
    lines = [
        '[%s version-bump-gate] BLOCKED: staged serving files changed but %s '
        'still reads __version__ = "%s".' % (APP, VERSION_FILE, old)
    ]
    if new is None:
        lines.append("  the staged %s carries no __version__ line" % VERSION_FILE)
    lines.append("  bump the version (major/minor/patch is your call), stage it, and commit again.")
    lines.append("  staged serving files: " + ", ".join(hit))
    return 1, "\n".join(lines)


def gate():
    try:
        staged = staged_paths()
    except RuntimeError as exc:
        print("[%s version-bump-gate] cannot read the index, refusing to guess: %s" % (APP, exc),
              file=sys.stderr)
        return 1
    code, msg = decide(staged, version_at("HEAD"), version_at(""))
    if msg:
        print(msg, file=sys.stderr)
    return code


# --- install -----------------------------------------------------------------

def _hooks_dir():
    res = _git("rev-parse", "--git-path", "hooks")
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or "not a git repository")
    return os.path.abspath(res.stdout.strip())


def _is_ours(path):
    if os.path.islink(path):
        return os.path.basename(os.path.realpath(path)) == os.path.basename(__file__)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return "version-bump-gate" in fh.read()
    except OSError:
        return False


def _install_copy(target, src):
    """Install *src* at *target* as a COPY, never a symlink.

    A symlink into the worktree dangles in a linked worktree, where the
    hooks dir is shared but this file's path is not — node-kb-mcp's
    ``scripts/install-hooks.sh`` documents the same reason and installs a
    copy for it. The installed copy does not track later edits to this
    file, so re-run ``--install`` after changing it.
    """
    if os.path.lexists(target):
        os.remove(target)
    shutil.copy2(src, target)
    os.chmod(target, os.stat(target).st_mode | 0o111)


def _rechain_isaac_stub(stub_text, hooks_dir):
    """Ask isaac's own installer to re-render its stub so it chains us.

    Scoped to this hooks dir and idempotent (that installer keeps an existing
    ``*.pre-isaac`` and adds the chain line). Returns False when isaac's
    installer cannot be located; the next ``install-hooks.sh`` run chains it.
    """
    gate_path = re.search(r'^GATE="([^"]+)"', stub_text, re.M)
    py = re.search(r'^PY="([^"]+)"', stub_text, re.M)
    if not gate_path or not py:
        return False
    installer = os.path.join(os.path.dirname(gate_path.group(1)), "install_commit_hooks.py")
    if not os.path.isfile(installer):
        return False
    spec = importlib.util.spec_from_file_location("isaac_install_commit_hooks", installer)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.install_into(hooks_dir, py.group(1))
    return True


def install():
    src = os.path.abspath(__file__)
    try:
        hooks_dir = _hooks_dir()
    except RuntimeError as exc:
        print("[%s version-bump-gate] %s" % (APP, exc), file=sys.stderr)
        return 1
    os.makedirs(hooks_dir, exist_ok=True)
    pre = os.path.join(hooks_dir, HOOK)
    text = ""
    if os.path.lexists(pre):
        with open(pre, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    if ISAAC_MARKER in text:
        target = os.path.join(hooks_dir, HOOK + ".pre-isaac")
        if os.path.lexists(target) and not _is_ours(target):
            print("[%s version-bump-gate] refusing: %s holds a foreign preserved hook" % (APP, target),
                  file=sys.stderr)
            return 1
        _install_copy(target, src)
        chained = target in text
        if not chained:
            try:
                if _rechain_isaac_stub(text, hooks_dir):
                    with open(pre, encoding="utf-8", errors="replace") as fh:
                        chained = target in fh.read()
            except Exception as exc:  # isaac's installer is optional here
                print("[%s version-bump-gate] could not re-render isaac's stub: %s" % (APP, exc),
                      file=sys.stderr)
        state = ("chained by isaac's pre-commit" if chained
                 else "NOT yet chained — isaac's install-hooks.sh (run at every session start) chains it")
        print("[%s version-bump-gate] installed %s -> %s (%s)" % (APP, target, src, state))
        return 0
    if text and not _is_ours(pre):
        print("[%s version-bump-gate] refusing to overwrite a foreign pre-commit hook at %s" % (APP, pre),
              file=sys.stderr)
        return 1
    _install_copy(pre, src)
    print("[%s version-bump-gate] installed %s -> %s" % (APP, pre, src))
    return 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--install"]:
        sys.exit(install())
    if sys.argv[1:]:
        print(__doc__)
        sys.exit(2)
    sys.exit(gate())
