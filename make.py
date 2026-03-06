#!/usr/bin/env python3
"""
make.py — cross-platform install/update script for bambu-mcp.

Creates .venv inside the project directory (if it doesn't exist), upgrades pip,
then pip-installs this package in editable mode along with all declared
dependencies (including bambu-printer-manager from the devel branch).

Usage:
    python make.py          # macOS / Linux / Windows

After running, point your MCP config at the python interpreter printed at the end.
"""

import os
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


def main() -> None:
    if not VENV_DIR.exists():
        print(f"Creating virtualenv at {VENV_DIR}")
        venv.create(str(VENV_DIR), with_pip=True)
    else:
        print(f"Reusing existing virtualenv at {VENV_DIR}")

    print("Upgrading pip...")
    run(str(PYTHON), "-m", "pip", "install", "--quiet", "--upgrade", "pip")

    print("Installing/updating bambu-mcp and dependencies...")
    run(str(PIP), "install", "--force-reinstall", "-e", str(PROJECT))

    print()
    print("Done.")
    print(f"MCP config should use: {PYTHON}")


if __name__ == "__main__":
    main()
