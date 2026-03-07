#!/usr/bin/env python3
"""
make.py — cross-platform install/update script for bambu-mcp.

Creates .venv inside the project directory (if it doesn't exist), upgrades pip,
then pip-installs this package in editable mode along with all declared
dependencies (including bambu-printer-manager from the devel branch).

Usage:
    python make.py              # install/update the venv
    python make.py config       # generate MCP config files from templates
    python make.py version-sync # sync version references in README.md and PLAN.md

After running, point your MCP config at the python interpreter printed at the end.
"""

import os
import re
import subprocess
import sys
import venv
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
VENV_DIR = PROJECT / ".venv"

# pip and python paths differ between Unix and Windows
if sys.platform == "win32":
    PYTHON = VENV_DIR / "Scripts" / "python.exe"
    PIP    = VENV_DIR / "Scripts" / "pip.exe"
else:
    PYTHON = VENV_DIR / "bin" / "python3"
    PIP    = VENV_DIR / "bin" / "pip"


def run(*cmd: str) -> None:
    result = subprocess.run(list(cmd), check=True)
    if result.returncode != 0:
        sys.exit(result.returncode)


def cmd_install() -> None:
    if not VENV_DIR.exists():
        print(f"Creating virtualenv at {VENV_DIR}")
        venv.create(str(VENV_DIR), with_pip=True)
    else:
        print(f"Reusing existing virtualenv at {VENV_DIR}")

    print("Upgrading pip...")
    run(str(PYTHON), "-m", "pip", "install", "--quiet", "--upgrade", "pip")

    print("Installing/updating bambu-mcp and dependencies...")
    run(str(PIP), "install", "--force-reinstall", "-e", str(PROJECT))

    print("Force-reinstalling bambu-printer-manager from devel branch...")
    run(str(PIP), "install", "--force-reinstall",
        "bambu-printer-manager @ git+https://github.com/synman/bambu-printer-manager.git@devel")

    print()
    print("Done.")
    print(f"MCP config should use: {PYTHON}")


def cmd_config() -> None:
    """Generate MCP client config files from *.example.json templates."""
    config_dir = PROJECT / "config"
    install_dir = str(Path.home())
    generated = []
    for template in sorted(config_dir.glob("*.example.json")):
        content = template.read_text()
        content = content.replace("<install-dir>", install_dir)
        out = config_dir / template.name.replace(".example.json", ".json")
        out.write_text(content)
        generated.append(out)
        print(f"  Generated: {out}")
    if not generated:
        print("No *.example.json templates found in config/")
    else:
        print(f"\nGenerated {len(generated)} config file(s). Do not commit these — they contain your local paths.")


def cmd_version_sync() -> None:
    """Sync version string from pyproject.toml into README.md and PLAN.md."""
    pyproject = PROJECT / "pyproject.toml"
    text = pyproject.read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        print("ERROR: could not find version in pyproject.toml")
        sys.exit(1)
    version = m.group(1)
    print(f"Current version: {version}")

    patched = 0
    for target, pattern, replacement in [
        (PROJECT / "README.md",
         r'\*\*Version:\s*[\d.]+\*\*',
         f"**Version: {version}**"),
        (PROJECT / "PLAN.md",
         r'\*\*Current version: [\d.]+\*\*',
         f"**Current version: {version}**"),
    ]:
        if not target.exists():
            continue
        original = target.read_text()
        updated = re.sub(pattern, replacement, original)
        if updated != original:
            target.write_text(updated)
            print(f"  Patched: {target.name}")
            patched += 1
        else:
            print(f"  Up to date: {target.name}")

    print(f"\nVersion sync complete ({patched} file(s) updated).")


def main() -> None:
    subcmd = sys.argv[1] if len(sys.argv) > 1 else None
    if subcmd == "config":
        cmd_config()
    elif subcmd == "version-sync":
        cmd_version_sync()
    elif subcmd is None:
        cmd_install()
    else:
        print(f"Unknown sub-command: {subcmd!r}")
        print("Usage: python make.py [config|version-sync]")
        sys.exit(1)


if __name__ == "__main__":
    main()
