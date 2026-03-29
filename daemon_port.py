"""
daemon_port.py — Port resolution and PID/port file management for daemon mode.

When bambu-mcp runs as a streamable-http daemon (singleton), this module resolves
the daemon port from the configured chain, manages port and PID files for process
discovery, and provides liveness checks for stale PID cleanup.

Port resolution chain:
  1. BAMBU_MCP_PORT env var
  2. Vault pref pref-bambu-mcp-port
  3. Default: 25099 (top of registered range 25000–25099)
  4. Descending scan 25099→25000 if preferred port is unavailable

Port/PID files:
  ~/.bambu-mcp/daemon.port — plain text integer, written atomically on start
  ~/.bambu-mcp/daemon.pid  — plain text integer, written on start

Usage:
  from daemon_port import resolve_port, write_port_file, write_pid_file
  from daemon_port import read_port_file, check_pid_alive, cleanup_files
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import tempfile
from pathlib import Path

from port_pool import assert_registered_port, _BAMBU_DAEMON_PORT_MIN, _BAMBU_DAEMON_PORT_MAX

log = logging.getLogger(__name__)

_DEFAULT_DAEMON_PORT = 25099
_BAMBU_MCP_DIR = Path.home() / ".bambu-mcp"
_PORT_FILE = _BAMBU_MCP_DIR / "daemon.port"
_PID_FILE = _BAMBU_MCP_DIR / "daemon.pid"

_daemon_mode: bool = False


def is_daemon_mode() -> bool:
    """Return True if the current process is running in daemon mode."""
    return _daemon_mode


def set_daemon_mode(enabled: bool = True) -> None:
    """Mark the current process as running in daemon mode."""
    global _daemon_mode
    _daemon_mode = enabled


def resolve_port() -> int:
    """Resolve the daemon port from the configured chain.

    Resolution order:
      1. BAMBU_MCP_PORT env var
      2. Vault pref pref-bambu-mcp-port (via secrets.py)
      3. Default: 25099
      4. If preferred port unavailable: descending scan 25099→25000

    Returns:
        An available port in the registered range 25000–25099.

    Raises:
        OSError: If no port in the range is available.
    """
    preferred = _read_env_port() or _read_vault_port() or _DEFAULT_DAEMON_PORT
    assert_registered_port(preferred)

    if _is_port_available(preferred):
        log.info("resolve_port: using port %d", preferred)
        return preferred

    log.info("resolve_port: preferred port %d unavailable, scanning", preferred)
    for port in range(_BAMBU_DAEMON_PORT_MAX, _BAMBU_DAEMON_PORT_MIN - 1, -1):
        if _is_port_available(port):
            log.info("resolve_port: found available port %d", port)
            return port

    raise OSError(
        f"bambu-mcp daemon: no available port in "
        f"{_BAMBU_DAEMON_PORT_MIN}–{_BAMBU_DAEMON_PORT_MAX}"
    )


def write_port_file(port: int) -> None:
    """Atomically write the daemon port to the port file."""
    assert_registered_port(port)
    _BAMBU_MCP_DIR.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=_BAMBU_MCP_DIR, prefix=".port-")
    try:
        os.write(tmp_fd, f"{port}\n".encode())
        os.close(tmp_fd)
        os.replace(tmp_path, _PORT_FILE)
        log.info("write_port_file: wrote port %d to %s", port, _PORT_FILE)
    except Exception:
        os.close(tmp_fd) if not os.get_inheritable(tmp_fd) else None
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def read_port_file() -> int | None:
    """Read and validate the daemon port from the port file.

    Returns:
        The port number if the file exists and contains a valid registered port,
        or None if the file doesn't exist or contains an invalid value.
    """
    try:
        text = _PORT_FILE.read_text().strip()
        port = int(text)
        assert_registered_port(port)
        return port
    except (FileNotFoundError, ValueError):
        return None


def write_pid_file() -> None:
    """Write the current process PID to the PID file."""
    _BAMBU_MCP_DIR.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(f"{os.getpid()}\n")
    log.info("write_pid_file: wrote PID %d to %s", os.getpid(), _PID_FILE)


def read_pid_file() -> int | None:
    """Read the PID from the PID file.

    Returns:
        The PID if the file exists and contains a valid integer, or None.
    """
    try:
        return int(_PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def check_pid_alive(pid: int | None = None) -> bool:
    """Check if the process with the given PID is alive.

    Args:
        pid: Process ID to check.  If None, reads from the PID file.

    Returns:
        True if the process exists, False otherwise.
    """
    if pid is None:
        pid = read_pid_file()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def cleanup_files() -> None:
    """Remove port and PID files (called on clean shutdown)."""
    for path in (_PORT_FILE, _PID_FILE):
        try:
            path.unlink(missing_ok=True)
            log.info("cleanup_files: removed %s", path)
        except OSError as exc:
            log.warning("cleanup_files: failed to remove %s: %s", path, exc)


def cleanup_stale() -> bool:
    """Check for and clean up stale port/PID files from a dead daemon.

    Returns:
        True if stale files were cleaned up, False if the daemon is still alive
        or no files exist.
    """
    pid = read_pid_file()
    if pid is None:
        # No PID file — check for orphaned port file
        if _PORT_FILE.exists():
            log.warning("cleanup_stale: orphaned port file (no PID file)")
            cleanup_files()
            return True
        return False

    if check_pid_alive(pid):
        return False  # daemon is alive

    log.warning("cleanup_stale: PID %d is dead, cleaning up stale files", pid)
    cleanup_files()
    return True


# ── Internal helpers ─────────────────────────────────────────────────────────

def _read_env_port() -> int | None:
    """Read BAMBU_MCP_PORT env var."""
    val = os.environ.get("BAMBU_MCP_PORT")
    if val:
        try:
            port = int(val)
            assert_registered_port(port)
            return port
        except ValueError as exc:
            log.warning("_read_env_port: invalid BAMBU_MCP_PORT=%s: %s", val, exc)
    return None


def _read_vault_port() -> int | None:
    """Read pref-bambu-mcp-port from the workspace vault."""
    try:
        import subprocess
        result = subprocess.run(
            [str(Path.home() / "ai/isaac/services/get-secret.sh"),
             "pref-bambu-mcp-port"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            port = int(result.stdout.strip())
            assert_registered_port(port)
            return port
    except Exception as exc:
        log.debug("_read_vault_port: vault lookup failed: %s", exc)
    return None


def _is_port_available(port: int) -> bool:
    """Check if a port is available via socket.bind() probe."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("", port))
            return True
        except OSError:
            return False
